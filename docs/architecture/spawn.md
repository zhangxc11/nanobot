# Spawn 体系架构

> 本文件包含 Spawn subagent 能力增强、SessionMessenger 跨 Session 消息投递、follow_up/stop/status 的架构设计。

## 本文件索引

| 章节 | 标题 |
|------|------|
| §十一 | spawn subagent 能力增强 |
| §十二 | SessionMessenger — 跨 Session 消息投递协议 |
| §十五 | Spawn follow_up — 向 subagent 追加消息 |
| §十六 | Spawn stop — 主动停止 subagent |
| §十七 | Spawn status — 查询 subagent 执行状态 |
| §十八 | SubagentManager 单例化 + 跨进程恢复 |

---

## 十一、spawn subagent 能力增强（Phase 26）

> 需求：REQUIREMENTS.md §二十四

### 11.1 概述

增强 `SubagentManager._run_subagent()` 的能力，使 subagent 从"玩具级"提升为可靠的后台任务执行器。改动集中在 `subagent.py`，不影响主 agent loop。

### 11.2 当前架构

```
SpawnTool.execute(task, label)
    │
    └── SubagentManager.spawn(task, label, origin_*, session_key)
            │
            └── asyncio.create_task(_run_subagent(...))
                    │
                    ├── 构建独立 ToolRegistry（无 message/spawn/cron）
                    ├── 构建 system prompt（含 skills summary）
                    ├── while iteration < 15:    ← 硬编码
                    │       provider.chat()       ← 无重试
                    │       tools.execute()       ← 应该能工作
                    │       纯内存 messages       ← 不持久化
                    │
                    └── _announce_result() → bus.publish_inbound()
```

### 11.3 改造后架构

```
SpawnTool.execute(task, label, max_iterations?, persist?)
    │
    └── SubagentManager.spawn(task, ..., max_iterations?, persist?)
            │
            └── asyncio.create_task(_run_subagent(...))
                    │
                    ├── 构建独立 ToolRegistry
                    ├── 构建 system prompt
                    ├── [persist] 创建 session: subagent:{parent_key}_{task_id}
                    │
                    ├── while iteration < effective_max_iterations:
                    │       │
                    │       ├── budget alert 注入（复用 _budget_alert_threshold）
                    │       ├── _chat_with_retry()   ← 新增重试
                    │       ├── tools.execute()
                    │       ├── [persist] session.append_message()
                    │       └── usage_recorder.record()  ← 新增
                    │
                    └── _announce_result() → bus.publish_inbound()
```

### 11.4 SpawnTool parameters 扩展

```python
@property
def parameters(self) -> dict:
    return {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The task for the subagent to complete",
            },
            "label": {
                "type": "string",
                "description": "Optional short label for the task (for display)",
            },
            "max_iterations": {
                "type": "integer",
                "description": (
                    "Maximum tool call iterations for the subagent (default 30, max 100). "
                    "Use higher values for complex multi-step tasks."
                ),
            },
            "persist": {
                "type": "boolean",
                "description": (
                    "If true, persist subagent messages to a session file for debugging. "
                    "Default false."
                ),
            },
        },
        "required": ["task"],
    }
```

### 11.5 SubagentManager 改造要点

#### 11.5.1 构造函数扩展

```python
class SubagentManager:
    def __init__(
        self,
        ...,
        default_max_iterations: int = 30,
        usage_recorder: "UsageRecorder | None" = None,
        session_manager: "SessionManager | None" = None,
    ):
        self.default_max_iterations = default_max_iterations
        self.usage_recorder = usage_recorder
        self.session_manager = session_manager
```

#### 11.5.2 spawn() 签名扩展

```python
async def spawn(
    self,
    task: str,
    label: str | None = None,
    ...,
    max_iterations: int | None = None,
    persist: bool = False,
) -> str:
```

#### 11.5.3 _run_subagent() 改造

核心循环改造：

```python
MAX_SUBAGENT_ITERATIONS = 100

async def _run_subagent(self, task_id, task, label, origin,
                         max_iterations, persist):
    effective_max = min(max_iterations or self.default_max_iterations,
                        MAX_SUBAGENT_ITERATIONS)

    # [persist] 创建 session
    session = None
    if persist and self.session_manager:
        session_key = f"subagent:{parent_key_sanitized}_{task_id}"
        session = self.session_manager.get_or_create(session_key)
        # 写入 user 消息
        self.session_manager.append_message(session, {
            "role": "user", "content": task,
            "timestamp": datetime.now().isoformat()
        })

    # 主循环
    while iteration < effective_max:
        iteration += 1

        # Budget alert
        remaining = effective_max - iteration
        threshold = _budget_alert_threshold(effective_max)
        if remaining == threshold:
            messages.append({"role": "system", "content": f"⚠️ Budget alert: ..."})

        # LLM call with retry
        response = await self._chat_with_retry(messages, tools)

        # Usage recording
        if response.usage and self.usage_recorder:
            self.usage_recorder.record(
                session_key=subagent_session_key,  # subagent:{parent}_{task_id}
                model=self.model,
                prompt_tokens=response.usage.get("prompt_tokens", 0),
                ...
            )

        # Tool execution + message building
        ...

        # [persist] 持久化消息
        if session and self.session_manager:
            self.session_manager.append_message(session, assistant_msg)
            for tool_msg in tool_results:
                self.session_manager.append_message(session, tool_msg)
```

#### 11.5.4 _chat_with_retry() 方法

独立实现（不依赖 AgentLoop），逻辑简化版：

```python
async def _chat_with_retry(self, messages, tools, max_retries=3):
    """LLM call with exponential backoff retry for transient errors."""
    delays = [5, 10, 20]
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return await self.provider.chat(
                messages=messages,
                tools=tools.get_definitions(),
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                reasoning_effort=self.reasoning_effort,
            )
        except Exception as e:
            if attempt < max_retries and _is_retryable(e):
                delay = delays[min(attempt, len(delays) - 1)]
                logger.warning("Subagent LLM retry {}/{} after {}s: {}",
                              attempt + 1, max_retries, delay, e)
                await asyncio.sleep(delay)
                last_error = e
            else:
                raise
    raise last_error  # unreachable but type-safe
```

`_is_retryable()` 复用 `agent/loop.py` 中已有的静态方法（提取为模块级函数或导入）。

### 11.6 与现有系统的交互

| 系统 | 交互方式 | 说明 |
|------|----------|------|
| UsageRecorder | 直接调用 | subagent token 消耗记入 `subagent:{parent}_{task_id}` session |
| SessionManager | 直接调用 | persist 模式下写入 `subagent_{parent}_{task_id}.jsonl` |
| MessageBus | _announce_result | 完成后通知主 agent（已有机制，不变） |
| AuditLogger | 通过 ToolRegistry | subagent 的工具调用也被审计（ToolRegistry 已有拦截） |
| ProviderPool | 通过 self.provider | 使用主 agent 的 provider/model（不支持 per-subagent 切换） |

### 11.7 死锁分析

**不存在死锁风险**：
- subagent 在 AgentLoop 进程内以 `asyncio.Task` 运行
- 不经过 web-chat worker 的 HTTP 请求队列
- 不经过 MessageBus.inbound（只通过 bus 发送结果通知）
- 与 B2（worker 并发限制）完全独立

### 11.8 测试设计

| 测试 | 说明 |
|------|------|
| test_default_max_iterations | 默认 30 轮 |
| test_custom_max_iterations | 自定义轮数 |
| test_max_iterations_cap | 超过 100 被限制 |
| test_budget_alert_injected | budget alert 消息注入 |
| test_retry_on_rate_limit | rate limit 时重试 |
| test_retry_exhausted | 重试耗尽后抛异常 |
| test_usage_recorded | token 消耗写入 SQLite |
| test_persist_session | persist 模式下 JSONL 文件生成 |
| test_persist_false_no_file | 默认不生成文件 |
| test_tool_execution_works | write_file + read_file 验证 |
| test_announce_result | 完成后通知主 agent |
| test_spawn_parameters | SpawnTool 参数 schema 正确 |

### 11.9 文件变更清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `agent/subagent.py` | 修改 | 核心改造：max_iterations 可配 + persist + budget alert + retry + usage |
| `agent/tools/spawn.py` | 修改 | parameters 新增 max_iterations/persist；execute 透传 |
| `agent/loop.py` | 修改 | SubagentManager 构造时传入 usage_recorder + session_manager（~2 行） |
| `tests/test_subagent.py` | 新增 | ~12 个测试 |

---


## 十二、SessionMessenger — 跨 Session 消息投递协议（Phase 30）

### 12.1 概述

Subagent 完成任务后需要将结果回报给父 session。在 Phase 30 之前，回报依赖 `MessageBus.publish_inbound()` 发布一条 `InboundMessage`，由 `run()` 调度循环消费。这在 Gateway 模式下工作正常，但在 Web Worker 模式下存在问题：Worker 进程通过 `process_direct()` 调用 agent，**没有 `run()` 循环消费 bus**，导致 subagent 回报消息无人接收。

`SessionMessenger` 协议解决了这个问题：它抽象了"向目标 session 投递消息"的能力，由不同运行模式提供各自的实现。

### 12.2 Protocol 定义

```python
# nanobot/agent/callbacks.py

@runtime_checkable
class SessionMessenger(Protocol):
    async def send_to_session(
        self,
        target_session_key: str,
        content: str,
        source_session_key: str | None = None,
    ) -> bool: ...
```

**职责**：将 `content` 投递到 `target_session_key` 对应的 session。如果提供了 `source_session_key`，内容会被加上 `[Message from session {source_session_key}]` 前缀以便溯源。

**返回值**：`True` 表示消息已投递（注入或触发），`False` 表示投递失败。

### 12.3 三种运行模式的实现

| 模式 | 实现类 | 定义位置 | 投递方式 |
|------|--------|----------|----------|
| Gateway（IM 渠道） | `GatewaySessionMessenger` | `agent/loop.py` — `run()` 内部 | inject 或 trigger（见 §12.4） |
| Web Worker | `WorkerSessionMessenger` | web-chat `worker.py` | HTTP 调用 Gateway API 触发父 session |
| CLI / Cron | 无实现 | — | fallback 到 bus publish（见 §12.5） |

### 12.4 Gateway 模式：inject 与 trigger 两条路径

`GatewaySessionMessenger` 在 `run()` 方法内部定义，持有 `active_sessions` 字典的引用，因此能判断目标 session 是否正在运行：

```
subagent._announce_result()
    │
    └── session_messenger.send_to_session(parent_key, content)
            │
            ├── 父 session 正在运行（active_sessions 中有对应 task 且未完成）
            │       │
            │       └── inject 路径：
            │           worker.callbacks.inject({"role": "system", "content": prefixed})
            │           → 消息进入 GatewayCallbacks._inject_queue
            │           → _run_agent_loop 在下一个 tool round 后通过
            │             check_user_input() 取出
            │           → 作为 system role 消息追加到 messages 列表
            │
            └── 父 session 空闲（不在 active_sessions 中，或 task 已完成）
                    │
                    └── trigger 路径：
                        bus.publish_inbound(InboundMessage(
                            channel="session_messenger",
                            sender_id=source_session_key,
                            chat_id=target_session_key,
                            content=prefixed,
                            session_key_override=target_session_key,
                        ))
                        → 消息进入 bus inbound 队列
                        → run() 调度循环消费后启动新 task 处理
```

### 12.5 与 `_announce_result()` 的关系

`SubagentManager._announce_result()` 是 subagent 完成后的统一出口，采用**优先 messenger、fallback bus** 策略：

```python
# agent/subagent.py — _announce_result()

async def _announce_result(self, ..., parent_session_key):
    # 1. 优先使用 SessionMessenger
    if self.session_messenger and parent_session_key:
        try:
            await self.session_messenger.send_to_session(
                target_session_key=parent_session_key,
                content=announce_content,
                source_session_key=subagent_session_key,
            )
            return  # 成功，直接返回
        except Exception:
            pass  # 失败，降级到 bus

    # 2. Fallback: 通过 bus 发布（CLI 模式、或 messenger 失败时）
    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id=f"{origin['channel']}:{origin['chat_id']}",
        content=announce_content,
        session_key_override=parent_session_key,
    )
    await self.bus.publish_inbound(msg)
```

### 12.6 Role 处理策略

> **§35 更新**：subagent 回报消息改回 `role="user"`。Anthropic API 会将 `role="system"` 消息从对话流抽走拼到 system prompt，导致 cache 失效和位置语义丢失。改为通过 announce_content 末尾的 prompt 指导来防止 agent 误将回报当成用户指令。

subagent 回报消息以 `role="user"` 进入父 session。announce_content 末尾包含明确的 prompt 指导，告知 agent 这是自动化通知而非用户请求。

| 路径 | 判定机制 | 最终 role |
|------|---------|-----------|
| **inject**（父 session 运行中） | `inject()` 传入 `dict`（含 `"role": "user"`）；`_run_agent_loop` 的 inject checkpoint 检测 `isinstance(injected, dict)` 后使用 dict 中的 role | `"user"` |
| **trigger**（父 session 空闲） | `InboundMessage.channel = "session_messenger"`；`_process_message()` 正常处理，`build_messages` 默认 `role="user"` | `"user"` |
| 用户消息 inject | `inject()` 传入 `str`；inject checkpoint 检测 `isinstance(injected, str)` | `"user"` |
| 用户消息 trigger | `msg.channel != "session_messenger"`（如 `"feishu"`、`"telegram"` 等） | `"user"` |

**关键代码**（`_run_agent_loop` inject checkpoint）：

```python
injected = await callbacks.check_user_input()
if injected:
    if isinstance(injected, dict):
        # 结构化注入（e.g. from SessionMessenger, role="user"）
        inject_msg = {"role": injected.get("role", "user"), "content": injected["content"], ...}
    else:
        # 纯字符串注入（用户消息）
        inject_msg = {"role": "user", "content": injected, ...}
```

**防误执行机制**：announce_content 末尾包含 prompt 指导：
```
(This is an automated system notification delivered as a user message for technical reasons.
It is NOT a new user request. Do not execute the subagent's task again.
Simply review the result and decide how to proceed in the context of your current conversation.)
```

### 12.7 初始化与注入

`SessionMessenger` 实例通过依赖注入传递到 `SubagentManager`：

```
AgentLoop.__init__(session_messenger=...)
    │
    └── SubagentManager.__init__(session_messenger=session_messenger)
            │
            └── self.session_messenger = session_messenger
                    │ （subagent 完成时）
                    └── _announce_result() 调用 self.session_messenger.send_to_session()
```

**Gateway 模式**：`GatewaySessionMessenger` 在 `run()` 内部创建后，通过 `self.subagents.session_messenger = messenger` 动态注入（因为它依赖 `active_sessions` 字典，该字典在 `run()` 内才创建）。

**Web Worker 模式**：`WorkerSessionMessenger` 在 worker 初始化 `AgentLoop` 时传入。

**CLI 模式**：不传入 `session_messenger`（默认 `None`），subagent 回报走 bus fallback。

### 12.8 消息流转全景图

```
Subagent task 完成
    │
    └── _announce_result(parent_session_key="feishu:group_123")
            │
            ├── [SessionMessenger 可用]
            │       │
            │       └── messenger.send_to_session("feishu:group_123", result)
            │               │
            │               ├── [Gateway] 父 session 运行中
            │               │       └── inject({"role":"system", "content":...})
            │               │               └── check_user_input() 取出
            │               │                       └── messages.append(system msg)
            │               │                               └── 下一轮 LLM 调用看到 subagent 结果
            │               │
            │               ├── [Gateway] 父 session 空闲
            │               │       └── bus.publish_inbound(channel="session_messenger")
            │               │               └── run() 消费 → 启动新 task
            │               │                       └── _process_message() 识别 channel
            │               │                               └── role 改为 "system"
            │               │
            │               └── [Worker] HTTP 触发父 session
            │                       └── Gateway 收到请求 → 处理
            │
            └── [SessionMessenger 不可用 / 失败]
                    │
                    └── bus.publish_inbound(channel="system", session_key_override=...)
                            └── run() 消费 → _process_message() 处理
```

### 12.9 文件变更清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `agent/callbacks.py` | 新增 | `SessionMessenger` Protocol 定义 |
| `agent/subagent.py` | 修改 | `__init__` 接受 `session_messenger` 参数；`_announce_result()` 优先使用 messenger |
| `agent/loop.py` | 修改 | `run()` 内定义 `GatewaySessionMessenger`；`_process_message()` 识别 `channel="session_messenger"` |
| web-chat `worker.py` | 修改 | 定义 `WorkerSessionMessenger`，初始化时传入 `AgentLoop` |

### 12.10 设计约束：不作为通用工具暴露

SessionMessenger 当前**仅作为内部底层能力**，在受限场景（subagent 结果回报）中使用，**不提供为 agent 可调用的通用工具**。

原因：agent 自主跨 session 发送消息的行为不可控 — 实际使用中观察到 agent 会在不恰当的时机向其他 session 发送消息，干扰正在进行的工作。在没有完善的权限控制和意图验证机制之前，跨 session 通信能力不应暴露给 agent 自主使用。

后续如需开放，应先设计：
- 目标 session 白名单/权限控制
- 发送频率限制
- 用户确认机制

---


## 十五、Spawn follow_up — 向 subagent 追加消息 (§36)

### 概述

为 spawn 工具增加 `follow_up` 参数，允许主 session 向已有 subagent 追加消息。系统根据 subagent 状态自动选择 inject（运行中）或 resume（已结束）。

### 数据结构

```python
@dataclass
class SubagentMeta:
    """subagent 元数据，spawn 时创建，完成后保留供 follow_up 使用。"""
    task_id: str
    subagent_session_key: str
    parent_session_key: str | None
    label: str
    origin: dict[str, str]
    inject_queue: asyncio.Queue[str]      # 运行中注入通道
    status: str                           # running | completed | failed | max_iterations
    max_iterations: int
    persist: bool

class SubagentManager:
    _running_tasks: dict[str, asyncio.Task]       # task_id -> asyncio.Task
    _session_tasks: dict[str, set[str]]            # parent_session_key -> {task_id}
    _task_meta: dict[str, SubagentMeta]            # task_id -> 元数据 (§36 新增)
```

### 生命周期

- `_task_meta` 在 `spawn()` 时创建，subagent 完成后**不删除**
- `_session_tasks` 同样不再在 task 完成时清理（用于鉴权）
- `_running_tasks` 仍在 task 完成时清理（用于判断运行状态）
- 进程重启后内存状态丢失，follow_up 不可用

### Inject 流程

```
主 session → spawn(task="补充信息", follow_up="<id>")
           → SubagentManager.follow_up()
           → 检查 _running_tasks[id] 存在且未完成
           → inject_queue.put_nowait(message)
           → subagent 在下一轮 tool 执行后的 checkpoint drain 读取
```

inject checkpoint 位于 `_run_subagent()` 的 tool 执行循环之后，与主 agent loop 的 `check_user_input()` 对齐。注入的消息格式：

```python
{
    "role": "user",
    "content": "[Message from parent session during execution]\n{message}",
    "timestamp": "..."
}
```

### Resume 流程

```
主 session → spawn(task="请继续", follow_up="<id>")
           → SubagentManager.follow_up()
           → 检查 _running_tasks[id] 不存在或已完成
           → 从 SessionManager 加载历史 messages
           → 构建 resume_messages: system_prompt + history + follow_up_msg
           → 创建新 asyncio.Task → _run_subagent(resume_messages=...)
           → 全新 max_iterations 配额
```

Resume 复用 `_run_subagent()`，通过 `resume_messages` 参数区分：
- `resume_messages=None` → 正常 spawn，构建 system + user 消息
- `resume_messages=[...]` → resume，直接使用传入的消息列表

### 安全鉴权

`_check_ownership(parent_session_key, task_id)` 验证：
1. `task_id` 存在于 `_task_meta`
2. `meta.parent_session_key == parent_session_key`

### SpawnTool 接口

```json
{
    "task": "消息内容",
    "follow_up": "a1b2c3d4"    // 可选：目标 subagent task_id
}
```

- `follow_up` 未设置 → 原有 spawn 行为
- `follow_up` 设置 → 调用 `SubagentManager.follow_up()`

### 影响文件

| 文件 | 改动 |
|------|------|
| `agent/subagent.py` | SubagentMeta、_task_meta、inject checkpoint、follow_up()、_check_ownership() |
| `agent/tools/spawn.py` | follow_up 参数、execute() 路由、description 更新 |
| `tests/test_spawn_follow_up.py` | 26 项新测试 |

---


## 十六、Spawn stop — 主动停止 subagent (§37)

### 概述

为 spawn 工具增加 `stop` 参数，允许主 session 精确停止指定的正在运行的 subagent。与 `follow_up` 互斥。

### 数据结构变更

```python
@dataclass
class SubagentMeta:
    # 已有字段不变
    status: str    # 新增 "stopped" 状态值
    # running | completed | failed | max_iterations | stopped
```

`SubagentManager` 新增方法：

```python
async def stop_subagent(self, parent_session_key: str, task_id: str, reason: str = "") -> str:
    """停止指定 subagent。返回结果描述字符串。"""
```

### 停止流程

```
主 session → spawn(task="原因", stop="<task_id>")
           → SpawnTool.execute() 路由到 stop 分支
           → SubagentManager.stop_subagent()
           → _check_ownership() 鉴权
           → 检查 meta.status
              ├── "running" → _stop_flag 标记 + task.cancel() + 返回成功
              └── 其他 → 返回提示"已结束，无需停止"
```

### 关键实现细节

#### 1. 停止标记 `_stop_flags`

```python
class SubagentManager:
    _stop_flags: set[str]    # 新增：被 stop 的 task_id 集合
```

`stop_subagent()` 先将 task_id 加入 `_stop_flags`，再调用 `task.cancel()`。

`_run_subagent()` 在 CancelledError 处理中检查此标记：

```python
except asyncio.CancelledError:
    if task_id in self._stop_flags:
        # 被主 session stop — 不 announce，状态设为 stopped
        meta.status = "stopped"
        self._stop_flags.discard(task_id)
    else:
        # 其他原因 cancel（如 cancel_by_session）— 保持原有行为
        meta.status = "failed"
        # announce error
```

#### 2. Announce 抑制

被 stop 的 subagent **不发送 announce**，因为：
- subagent 已无轮次来准备 announce 内容
- stop 方法本身的返回值已告知主 session 结果

#### 3. Session 持久化

如果 `persist=True`，在 stop 时向 session 追加一条消息：

```python
{
    "role": "user",
    "content": "[Stopped by parent session] {reason}",
    "timestamp": "..."
}
```

#### 4. 后续 follow_up resume

被 stop 的 subagent（status="stopped"）允许通过 `follow_up` resume：
- `follow_up()` 中 `_running_tasks` 已不存在该 task → 走 resume 路径
- resume 从 session 历史恢复，启动全新 turn

### SpawnTool 接口

```json
{
    "task": "停止原因（可为空字符串）",
    "stop": "a1b2c3d4"           // 目标 subagent task_id
}
```

三种模式互斥：

| 参数组合 | 模式 |
|----------|------|
| `task` only | 新建 subagent |
| `task` + `follow_up` | 追加消息 |
| `task` + `stop` | 停止 subagent |
| `follow_up` + `stop` | ❌ 报错 |

### 影响文件

| 文件 | 改动 |
|------|------|
| `agent/subagent.py` | `_stop_flags` 集合；`stop_subagent()` 方法；`_run_subagent()` CancelledError 分支 |
| `agent/tools/spawn.py` | `stop` 参数；`execute()` 路由；`parameters` / `description` 更新 |
| `tests/test_spawn_stop.py` | 新增测试 |

---


## 十七、Spawn status — 查询 subagent 执行状态 (§38)

> Phase 40

为 spawn 工具增加 `status` 参数，允许主 session 查询 subagent 的执行状态。支持查询单个 subagent 详情和列出当前 session 下所有 subagent 摘要。只读操作，不改变任何状态。

### SubagentMeta 新增字段

```python
@dataclass
class SubagentMeta:
    # ... 现有字段 ...
    # §38 新增
    created_at: str = ""                  # ISO 时间，spawn 时设置
    finished_at: str | None = None        # ISO 时间，状态变更为终态时设置
    current_iteration: int = 0            # _run_subagent 每次 iteration 时同步更新
    last_tool_name: str | None = None     # 每次执行工具后更新
```

更新时机：
- `created_at`：`spawn()` 创建 SubagentMeta 时设置
- `finished_at`：`_run_subagent()` 中 status 变更为 completed/failed/max_iterations/stopped 时设置
- `current_iteration`：`_run_subagent()` while 循环中 `iteration += 1` 后同步 `meta.current_iteration = iteration`
- `last_tool_name`：`_run_subagent()` 执行工具后同步 `meta.last_tool_name = tool_call.name`

### SubagentManager 新增方法

```python
def get_status(self, task_id: str, parent_session_key: str) -> str:
    """查询单个 subagent 状态，返回格式化文本。"""
    meta = self._check_ownership(parent_session_key, task_id)
    # 格式化返回 meta 中的所有关键字段

def list_subagents(self, parent_session_key: str) -> str:
    """列出当前 session 下所有 subagent，返回摘要表格。"""
    # 遍历 _task_meta，过滤 parent_session_key 匹配的
```

### SpawnTool 路由

```python
async def execute(self, task, ..., status=None, ...):
    # 互斥检查：status 与 follow_up、stop 互斥
    if status:
        if status == "list":
            return self._manager.list_subagents(self._session_key)
        else:
            return self._manager.get_status(status, self._session_key)
```

### 参数优先级（更新）

| 模式 | 参数 | 行为 |
|------|------|------|
| 新建 | `task` only | 创建新 subagent |
| 追加 | `task` + `follow_up` | 向已有 subagent 追加消息 |
| 停止 | `task` + `stop` | 停止指定 subagent |
| 查询 | `task` + `status` | 查询 subagent 状态（只读） |
| ❌ | 任意两个 `follow_up`/`stop`/`status` 同时设置 | 报错 |

### 影响文件

| 文件 | 改动 |
|------|------|
| `agent/subagent.py` | SubagentMeta 新增 4 字段；`_run_subagent()` 同步更新字段；新增 `get_status()` / `list_subagents()`；状态变更时设置 `finished_at` |
| `agent/tools/spawn.py` | `status` 参数；`execute()` 路由；互斥检查扩展；`parameters` / `description` 更新 |
| `tests/test_spawn_status.py` | 新增测试 |

---

## §十八 SubagentManager 单例化 + 跨进程恢复

> 需求：§40（`requirements/s40-s49.md`）

### 18.1 问题描述

在 web worker 模式下，每个 HTTP 请求通过 `_create_runner()` 创建一个新的 `AgentLoop` 实例，`SubagentManager` 随之创建并在请求结束后被 GC 回收。这导致：

1. **进程内跨请求丢失**：同一进程中，请求 A spawn 了 subagent，请求 B 无法通过 `follow_up` 找到它（`_task_meta` 已清空）。
2. **进程重启后丢失**：即使 subagent session 文件已持久化到磁盘，重启后内存中没有对应的 `SubagentMeta`，`_check_ownership` 直接抛 `ValueError`。

### 18.2 解决方案概述

两层方案：

**层一：进程内单例化（web worker）**

在 `worker.py` 中将 `SubagentManager` 提升为模块级单例，所有请求共享同一个实例。`AgentLoop.__init__` 新增可选参数 `subagent_manager`，允许外部注入已有实例。

**层二：跨进程按规则恢复（disk fallback）**

`SubagentManager` 新增两个方法：
- `_recover_meta(task_id, parent_session_key)` — 按确定性命名规则构造 session key，O(1) 文件 stat 检查，恢复单个 `SubagentMeta`
- `_load_disk_subagents(parent_session_key)` — glob 匹配前缀，批量恢复，用于 `list_subagents`

`_check_ownership` 在内存未命中时调用 `_recover_meta` 作为 fallback。

### 18.3 _recover_meta 设计

```python
def _recover_meta(self, task_id: str, parent_session_key: str) -> SubagentMeta | None:
    """按确定性命名规则从磁盘恢复 SubagentMeta。

    命名规则（与 spawn() 一致）：
      parent_sanitized = parent_session_key.replace(":", "_")
      subagent_key     = f"subagent:{parent_sanitized}_{task_id}"
      session_path     = workspace/sessions/subagent_{parent_sanitized}_{task_id}.jsonl

    O(1) 文件 stat — 不读取文件内容，只检查文件是否存在。
    恢复的 meta 使用保守默认值：status="unknown", label="(recovered)"。
    """
```

**关键设计决策**：
- 不读取 session 文件内容（避免 I/O 开销），只做 `Path.exists()` 检查
- 恢复后立即缓存到 `_task_meta` 和 `_session_tasks`，后续访问走内存
- `status="unknown"` 表示进程重启后状态未知，`follow_up` 仍可正常 resume（因为 session 文件存在）

### 18.4 _load_disk_subagents 设计

```python
def _load_disk_subagents(self, parent_session_key: str) -> None:
    """glob 匹配前缀，批量恢复磁盘上的 subagent session 文件。

    用于 list_subagents() 在查询前补全内存状态。
    跳过已在内存中的 task_id（避免覆盖运行中的 meta）。
    sessions 目录不存在时静默返回。
    """
```

glob 模式：`subagent_{parent_sanitized}_*.jsonl`，从文件名提取 `task_id`（最后一个 `_` 后的部分，去掉 `.jsonl`）。

### 18.5 _check_ownership 增强

```python
def _check_ownership(self, parent_session_key: str, target_task_id: str) -> SubagentMeta:
    meta = self._task_meta.get(target_task_id)
    if meta is None:
        # Disk fallback: try to recover from session file
        meta = self._recover_meta(target_task_id, parent_session_key)
    if meta is None:
        raise ValueError(f"Unknown subagent task_id: {target_task_id}")
    if meta.parent_session_key != parent_session_key:
        raise ValueError(f"Subagent {target_task_id} does not belong to this session")
    return meta
```

### 18.6 AgentLoop 参数透传

```python
class AgentLoop:
    def __init__(self, ..., subagent_manager: SubagentManager | None = None):
        ...
        if subagent_manager is not None:
            self.subagents = subagent_manager
        else:
            self.subagents = SubagentManager(...)  # 原有逻辑
```

### 18.7 web worker 单例化

```python
# worker.py — 模块级单例
_subagent_manager: "SubagentManager | None" = None
_subagent_manager_lock = threading.Lock()

def _get_subagent_manager() -> "SubagentManager":
    global _subagent_manager
    if _subagent_manager is not None:
        return _subagent_manager
    with _subagent_manager_lock:
        if _subagent_manager is not None:
            return _subagent_manager
        _subagent_manager = SubagentManager(...)  # 与 AgentLoop 创建时参数一致
        return _subagent_manager

def _create_runner():
    ...
    agent_loop = AgentLoop(..., subagent_manager=_get_subagent_manager())
    ...
```

**注意**：单例 `SubagentManager` 使用进程启动时的 `config.workspace_path` 和 `session_manager`，与各请求的 `AgentLoop` 共享同一个 `SubagentManager` 实例，确保跨请求的 `_task_meta` 持久化。

### 18.8 影响文件

| 文件 | 改动 |
|------|------|
| `nanobot/agent/subagent.py` | `_recover_meta`, `_load_disk_subagents`, `_check_ownership` 增强, `list_subagents` 增强 |
| `nanobot/agent/loop.py` | `__init__` 新增 `subagent_manager` 参数 |
| `web-chat/worker.py` | SubagentManager 单例 + `_create_runner` 透传 |
| `tests/test_spawn_singleton.py` | 新测试文件（14 项测试） |

---

*本文档将随开发进展持续更新。*

## §十九 Spawn status 异常诊断字段 (§44)

### 设计

SubagentMeta 新增 3 个诊断字段：

```python
@dataclass
class SubagentMeta:
    ...
    error_count: int = 0               # LLM 调用失败次数
    last_error: str | None = None       # 最近一次错误信息（截断 500 字符）
    last_error_time: str | None = None  # ISO 时间戳
```

### 错误记录时机

`_chat_with_retry()` 新增 `task_id` 参数。每次 LLM 调用异常时（无论是否可重试），更新 meta：
- `error_count += 1`
- `last_error = str(e)[:500]`
- `last_error_time = datetime.now().isoformat()`

只记录 LLM 调用异常，不记录工具执行异常。

### get_status 输出

```
- **error_count**: 3
- **last_error**: Connection timeout after 30s
- **last_error_time**: 2026-03-11T12:00:00
```

### Resume 重置

`follow_up()` resume 时重置 error_count/last_error/last_error_time 为 0/None/None。

---

## §二十三 Subagent announce 隐藏标记 (§45)

### 设计

`_announce_result()` 在 announce_content 开头插入 HTML 注释标记：

```
<!-- nanobot:system -->[Subagent Result Notification]
A previously spawned subagent '{label}' has {status_text}.
...
```

标记用途：
- 下游消费者（前端/日志分析）可识别系统注入的引导 prompt 部分
- HTML 注释不影响 LLM 对内容的理解
- 旧数据不做迁移，只在新消息中添加

