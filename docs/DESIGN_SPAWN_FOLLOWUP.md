# §36 Spawn 附加消息能力 — 需求与架构设计

> 状态：**已确认 — 实施中**
> 日期：2026-03-10

---

## 一、背景与动机

当前 spawn subagent 是"发射后不管"模式：主 session 调用 `spawn(task)` 后，subagent 在后台独立运行，主 session 无法再与之交互。这导致两个痛点：

1. **无法恢复中断的 subagent**：subagent 因网络故障、超出 `max_iterations` 等原因中断后，其 asyncio.Task 已完成，无法继续。目前只能手动找到 session 文件通过 web-subsession 重新触发，操作繁琐。

2. **无法在执行过程中补充信息**：subagent 运行期间，主 session 可能获得了新的上下文信息需要传达给 subagent，但当前没有通道可以做到。

## 二、需求定义

### 核心需求：向 subagent 追加消息（Append）

主 session 通过 spawn 工具，指定一个之前 spawn 过的 subagent（通过 task_id 标识），向其发送一条消息。

**调用者无需关心 subagent 当前状态**，spawn 内部自动判断：

| subagent 状态 | 内部行为 | 效果 |
|--------------|---------|------|
| **已结束**（completed / failed / max_iterations） | **Resume**：从 session 历史恢复，追加消息，启动新一轮 turn | 触发新 turn |
| **运行中** | **Inject**：消息放入 inject_queue，在下一轮 LLM 调用前被读取 | 不触发新 turn |

### 安全约束

- **只能操作自己 spawn 的 subagent**：通过 `SubagentMeta.parent_session_key` 鉴权
- 不能操作其他 session spawn 的 subagent

### 设计决策（已确认）

1. **标识**：用 **task_id**（短 8 字符 hex），LLM 容易引用
2. **Resume 复用原 task_id**：保持追踪连续性
3. **多条 inject 全部 drain**：一次读完所有积累的消息

## 三、现状分析

### 3.1 subagent 当前的数据结构

```python
class SubagentManager:
    _running_tasks: dict[str, asyncio.Task]       # task_id -> asyncio.Task (运行中)
    _session_tasks: dict[str, set[str]]            # parent_session_key -> {task_id, ...}
```

**缺失**：
- 没有 `task_id -> subagent_session_key` 的映射（session_key 在 `_run_subagent` 内部生成，外部无法查到）
- 没有 `task_id -> inject_queue` 的映射（subagent 主循环没有注入检查点）
- Task 完成后从 `_running_tasks` 中移除，无法再找到

### 3.2 subagent 主循环 vs 主 agent loop

| 特性 | 主 agent loop | subagent `_run_subagent` |
|------|-------------|----------------------|
| inject 检查点 | ✅ `check_user_input()` after tool execution | ❌ 无 |
| callbacks 机制 | ✅ `AgentCallbacks` protocol | ❌ 无 |
| session 恢复 | ✅ `get_history()` 加载 | ❌ 每次从头开始 |

### 3.3 session 持久化

subagent 的 session 已经持久化到 JSONL 文件（`persist=True` 时），session_key 格式为 `subagent:{parent_key_sanitized}_{task_id}`。Resume 所需的历史消息已经存在。

## 四、架构设计

### 4.1 新增数据结构

```python
@dataclass
class SubagentMeta:
    """subagent 元数据，spawn 时创建，完成后保留供 append 使用。"""
    task_id: str
    subagent_session_key: str
    parent_session_key: str | None
    label: str
    origin: dict[str, str]               # channel/chat_id 信息
    inject_queue: asyncio.Queue[str]      # 运行中注入通道
    status: str                           # "running" | "completed" | "failed" | "max_iterations"
    max_iterations: int
    persist: bool
```

```python
class SubagentManager:
    # 现有
    _running_tasks: dict[str, asyncio.Task]
    _session_tasks: dict[str, set[str]]
    
    # 新增
    _task_meta: dict[str, SubagentMeta]   # task_id -> 元数据
```

**生命周期**：`_task_meta` 在 subagent 完成后**不删除**，保留供后续 append 使用。由于每条 meta 只有几百字节，且 subagent 数量有限（通常几十个），无需特殊清理策略。进程重启后内存状态丢失，这是可接受的（用户可通过 web-subsession 手动恢复）。

### 4.2 spawn 工具接口扩展

新增一个可选参数 `append`：

```json
{
    "task": "...",
    "label": "...",
    "max_iterations": 30,
    "persist": true,
    "follow_up": "a1b2c3d4"    // 新增：目标 subagent 的 task_id
}
```

**语义**：
- `follow_up` 未设置 → **原有行为**，spawn 新 subagent
- `follow_up` 设置 → **附加消息**，`task` 参数作为消息内容发送给目标 subagent

### 4.3 Append 统一入口

```python
async def append_to_subagent(
    self,
    task_id: str,
    message: str,
    parent_session_key: str,
    max_iterations: int | None = None,
) -> str:
    """向 subagent 追加消息。根据状态自动选择 resume 或 inject。"""
    
    # 1. 鉴权
    meta = self._check_ownership(parent_session_key, task_id)
    
    # 2. 判断状态
    task = self._running_tasks.get(task_id)
    is_running = task is not None and not task.done()
    
    if is_running:
        # ── Inject：放入队列，不触发新 turn ──
        meta.inject_queue.put_nowait(message)
        return f"Message injected into subagent [{meta.label}] (id: {task_id}). It will be read before the next LLM call."
    else:
        # ── Resume：从历史恢复，启动新 turn ──
        if not meta.persist:
            raise ValueError("Cannot resume non-persisted subagent (no session history)")
        # max_iterations 是全新一轮的完整配额（不是剩余量）
        effective_max = max_iterations or meta.max_iterations
        # 启动新 task ...
        return f"Subagent [{meta.label}] resumed (id: {task_id}). I'll notify you when it completes."
```

### 4.4 Resume 流程

```
主 session                          SubagentManager
   │                                      │
   │  spawn(task="请继续",                  │
   │        follow_up="a1b2c3d4")          │
   │ ─────────────────────────────────────>│
   │                                      │
   │                    1. 鉴权：meta.parent_session_key == caller
   │                    2. 检查 task 已结束 → Resume 路径
   │                    3. 从 SessionManager 加载历史 messages
   │                    4. 追加 user message: "请继续"
   │                    5. 创建新 asyncio.Task → _run_subagent(resume_messages=...)
   │                    6. 更新 _running_tasks, meta.status = "running"
   │                                      │
   │  <── "Subagent [xxx] resumed (id: a1b2c3d4)"
```

**Resume 复用 `_run_subagent`**：
- 新增可选参数 `resume_messages: list[dict] | None`
- 如果提供了 `resume_messages`，跳过 system prompt + user task 构建，直接使用传入的 messages
- 其余逻辑（while 循环、tool 执行、budget alert、announce）完全复用

### 4.5 Inject 流程

```
主 session                          SubagentManager                    subagent task
   │                                      │                                │
   │  spawn(task="补充：API key 改了",      │                                │
   │        follow_up="a1b2c3d4")          │                                │
   │ ─────────────────────────────────────>│                                │
   │                                      │                                │
   │                    1. 鉴权通过
   │                    2. 检查 task 运行中 → Inject 路径
   │                    3. inject_queue.put_nowait("补充：API key 改了")
   │                                      │                                │
   │  <── "Message injected into [xxx]"   │                                │
   │                                      │                                │
   │                                      │    ── 当轮 tool 执行完毕后 ──    │
   │                                      │    drain inject_queue            │
   │                                      │    追加到 messages              │
   │                                      │    持久化到 session             │
   │                                      │    继续 LLM 调用               │
```

**subagent 主循环改造**：在 `_run_subagent` 的 tool 执行循环之后，增加 inject_queue drain：

```python
# ── Inject checkpoint (对齐主 agent loop 的 check_user_input) ──
while not inject_queue.empty():
    try:
        injected_text = inject_queue.get_nowait()
        inject_msg = {
            "role": "user",
            "content": f"[Message from parent session during execution]\n{injected_text}",
            "timestamp": datetime.now().isoformat(),
        }
        messages.append(inject_msg)
        if session is not None:
            self.session_manager.append_message(session, inject_msg)
    except asyncio.QueueEmpty:
        break
```

### 4.6 安全鉴权

```python
def _check_ownership(self, parent_session_key: str, target_task_id: str) -> SubagentMeta:
    """验证调用者有权操作目标 subagent。"""
    meta = self._task_meta.get(target_task_id)
    if meta is None:
        raise ValueError(f"Unknown subagent task_id: {target_task_id}")
    if meta.parent_session_key != parent_session_key:
        raise ValueError(f"Subagent {target_task_id} does not belong to this session")
    return meta
```

### 4.7 SpawnTool 返回值

确保 task_id 在返回中清晰可见，方便 LLM 后续引用：

```
# 新 spawn
Subagent [label] started (id: a1b2c3d4, session: subagent:xxx_a1b2c3d4). 
I'll notify you when it completes.

# Append → Resume
Subagent [label] resumed (id: a1b2c3d4). I'll notify you when it completes.

# Append → Inject
Message injected into subagent [label] (id: a1b2c3d4).
```

## 五、文件变更清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `agent/subagent.py` | **核心改动** | 新增 `SubagentMeta`、`_task_meta`、inject checkpoint、`append_to_subagent()`、`_check_ownership()`、`_run_subagent` 增加 resume_messages 参数 |
| `agent/tools/spawn.py` | **接口扩展** | 新增 `follow_up` 参数，execute() 路由到 spawn / follow_up |
| `docs/REQUIREMENTS.md` | 文档 | 新增 §36 需求描述 |
| `docs/ARCHITECTURE.md` | 文档 | 新增 §36 架构描述 |

## 六、工具描述更新

SpawnTool 的 description 需要更新：

```
Spawn a subagent to handle a task in the background, or send a follow-up 
message to an existing subagent.

**New subagent**: Provide `task` without `follow_up`.
**Follow-up**: Set `follow_up` to the target subagent's task_id, and 
  `task` as the message content. The system auto-detects the subagent's state:
  - If finished → resumes it with a new turn
  - If still running → injects the message into its execution flow

You can only follow up on subagents spawned by this session.
```

## 七、边界情况

| 场景 | 处理 |
|------|------|
| Append 到 persist=False 且已结束的 subagent | 拒绝：无 session 历史，无法 resume |
| Append 到不存在的 task_id | 返回错误"Unknown subagent task_id" |
| Append 到其他 session 的 subagent | 返回错误"does not belong to this session" |
| Resume 时 max_iterations | 全新一轮完整配额，不是剩余量 |
| Inject 时 max_iterations | 不影响当前正在执行的轮次配额 |
| 进程重启后 _task_meta 丢失 | Append 不可用，用户需通过 web-subsession 手动恢复 |
| Resume 的 subagent 再次结束 | meta.status 更新，可再次 append |
| 多次快速 inject | 全部入队，下一个 checkpoint 一次 drain 完 |

## 八、未来扩展（不在本次范围）

- **跨进程 Resume**：将 `_task_meta` 持久化到磁盘，支持进程重启后恢复
- **subagent 列表查询**：spawn 工具增加 `list` 模式查看当前/历史 subagent 状态
- **subagent 取消**：spawn 工具增加取消运行中 subagent 的能力
