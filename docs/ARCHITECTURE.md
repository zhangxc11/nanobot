# nanobot 核心 — 架构设计文档

> 版本：V2.0 | 最后更新：2026-02-26
> 本文档描述 `local` 分支的架构设计，包括已实施和规划中的改动。

---

## 一、现有架构概览

### 1.1 核心模块结构

```
nanobot/
├── agent/                  # Agent 核心
│   ├── loop.py            # AgentLoop — 消息处理 + LLM 调用循环
│   ├── context.py         # ContextBuilder — 系统提示词 + 消息构建
│   ├── memory.py          # MemoryStore — MEMORY.md / HISTORY.md 管理
│   ├── skills.py          # SkillsLoader — Skill 发现与加载
│   ├── subagent.py        # SubagentManager — 子 agent 管理
│   └── tools/             # 工具实现
│       ├── base.py        # Tool 基类
│       ├── registry.py    # ToolRegistry — 工具注册表
│       ├── shell.py       # ExecTool — Shell 命令执行
│       ├── filesystem.py  # 文件读写工具
│       ├── web.py         # Web 搜索/抓取
│       ├── message.py     # 消息发送工具
│       ├── spawn.py       # 子 agent 生成
│       ├── cron.py        # 定时任务工具
│       └── mcp.py         # MCP 服务器连接
├── session/
│   └── manager.py         # SessionManager — Session JSONL 读写
├── providers/
│   ├── base.py            # LLMProvider 基类 + LLMResponse
│   ├── litellm_provider.py # LiteLLM 统一 Provider
│   ├── custom_provider.py  # 自定义 OpenAI 兼容 Provider
│   └── registry.py        # Provider 注册表
├── bus/
│   ├── events.py          # InboundMessage / OutboundMessage
│   └── queue.py           # MessageBus — 异步消息队列
├── channels/              # IM 渠道适配
│   ├── manager.py         # ChannelManager
│   ├── telegram.py        # Telegram
│   ├── discord.py         # Discord
│   └── ...                # 其他渠道
├── cli/
│   └── commands.py        # CLI 命令（agent, gateway, cron 等）
├── config/
│   ├── loader.py          # 配置加载
│   └── schema.py          # 配置 Schema
├── cron/
│   └── service.py         # CronService — 定时任务调度
└── heartbeat/
    └── service.py         # HeartbeatService — 心跳检查
```

### 1.2 消息处理流程（现有）

```
                    CLI / IM Channel / Web Worker
                              │
                              ▼
                        MessageBus
                    (InboundMessage)
                              │
                              ▼
                    AgentLoop.run()
                              │
                              ▼
                   _process_message(msg)
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
              session.get_history()   context.build_messages()
                    │                   │
                    └─────────┬─────────┘
                              ▼
                    _run_agent_loop(messages)
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
              provider.chat()     tools.execute()
                    │                   │
                    └─────────┬─────────┘
                              ▼ (循环直到 final_content)
                              │
                    _save_turn(session, all_msgs)
                    session.save()  ← 重写整个 JSONL
                              │
                              ▼
                        MessageBus
                    (OutboundMessage)
```

### 1.3 调用方式

nanobot 当前有三种调用方式：

| 方式 | 入口 | 消息流 | Session 管理 |
|------|------|--------|-------------|
| CLI 单次 | `nanobot agent -m "..."` | `process_direct()` → `_process_message()` | 内置 SessionManager |
| CLI 交互 | `nanobot agent` | MessageBus → `run()` → `_process_message()` | 内置 SessionManager |
| IM Gateway | `nanobot gateway` | Channel → MessageBus → `run()` | 内置 SessionManager |
| Web Worker | `subprocess.Popen(['nanobot', 'agent', ...])` | 独立进程，CLI 单次模式 | 独立进程的 SessionManager |

### 1.4 Session 持久化（现有问题）

```python
# loop.py — _save_turn (当前实现)
def _save_turn(self, session, messages, skip):
    for m in messages[skip:]:
        entry = {k: v for k, v in m.items() if k != "reasoning_content"}
        # ... 截断大 tool result ...
        entry.setdefault("timestamp", datetime.now().isoformat())
        session.messages.append(entry)
    session.updated_at = datetime.now()

# session/manager.py — save (当前实现)
def save(self, session):
    with open(path, "w") as f:        # ← 重写整个文件
        f.write(metadata_line + "\n")
        for msg in session.messages:
            f.write(json.dumps(msg) + "\n")
```

**问题**：`_save_turn` + `save` 只在 `_process_message` 末尾调用。`_run_agent_loop` 运行期间（可能数分钟），所有消息只在内存中。进程异常退出 = 全部丢失。

---

## 二、架构改造设计（Backlog #6 + #7 + #8）

### 2.1 设计原则

1. **最小侵入**：尽量不改变现有的 `_run_agent_loop` 控制流，通过回调/钩子注入新行为
2. **向后兼容**：CLI 和 IM Gateway 的行为不变，SDK 是新增的调用方式
3. **关注点分离**：持久化、usage 记录、进度通知是独立的关注点，通过回调解耦
4. **渐进实施**：可以分阶段实施，每阶段独立可测试

### 2.2 核心改造：EventCallback 机制

在 `_run_agent_loop` 的关键节点注入回调，替代当前的 `on_progress` 单一回调：

```python
# 新增: nanobot/agent/callbacks.py

from dataclasses import dataclass
from typing import Any, Protocol

class AgentCallbacks(Protocol):
    """Agent 执行过程中的回调接口。"""

    async def on_progress(self, text: str, tool_hint: bool = False) -> None:
        """LLM 返回了中间文本或工具调用提示。"""
        ...

    async def on_message(self, message: dict[str, Any]) -> None:
        """一条消息（user/assistant/tool）已产生，可用于实时持久化。"""
        ...

    async def on_usage(self, usage: dict[str, Any]) -> None:
        """一次 agent loop 完成后的 token 用量汇总。"""
        ...

    async def on_done(self, final_content: str | None) -> None:
        """Agent 完成处理。"""
        ...

    async def on_error(self, error: str) -> None:
        """Agent 处理出错。"""
        ...
```

### 2.3 改造后的消息处理流程

```
                    CLI / IM / SDK (Worker)
                              │
                              ▼
                   _process_message(msg, callbacks)
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
              session.get_history()   context.build_messages()
                    │                   │
                    └─────────┬─────────┘
                              ▼
                    _run_agent_loop(messages, callbacks)
                              │
                    ┌─────────┴─────────────────────────────┐
                    ▼                                       ▼
              provider.chat()                         tools.execute()
                    │                                       │
                    ▼                                       ▼
              callbacks.on_message(assistant_msg)    callbacks.on_message(tool_msg)
              session.append_message(assistant_msg)  session.append_message(tool_msg)
                    │                                       │
                    └─────────┬─────────────────────────────┘
                              ▼ (循环)
                              │
                    callbacks.on_usage(usage)
                    UsageRecorder.record(usage)  ← SQLite
                    callbacks.on_done(final_content)
```

### 2.4 Session 实时持久化架构（Backlog #7）

#### 2.4.1 SessionManager 新增方法

```python
class SessionManager:
    # 现有方法保留...

    def append_message(self, session: Session, message: dict) -> None:
        """追加一条消息到 JSONL 文件（不重写整个文件）。
        
        同时更新内存中的 session.messages 列表。
        """
        path = self._get_session_path(session.key)
        
        # 如果文件不存在，先写 metadata 行
        if not path.exists():
            self._write_metadata(path, session)
        
        # 追加消息行
        entry = self._prepare_entry(message)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())  # 确保写入磁盘
        
        # 更新内存
        session.messages.append(entry)
        session.updated_at = datetime.now()

    def update_metadata(self, session: Session) -> None:
        """只更新 JSONL 第一行的 metadata（重写整个文件）。
        
        在 turn 结束时调用，更新 last_consolidated 等字段。
        比 save() 更高效 — 只在需要更新 metadata 时重写。
        """
        # 方案 A: 重写整个文件（简单，当前 save() 的逻辑）
        # 方案 B: 只重写第一行（复杂，需要处理行长度变化）
        # 选择方案 A，因为 metadata 更新频率低（每个 turn 一次）
        self.save(session)
```

#### 2.4.2 JSONL 文件格式（不变）

```jsonl
{"_type": "metadata", "key": "webchat:1234", "created_at": "...", "last_consolidated": 0}
{"role": "user", "content": "你好", "timestamp": "2026-02-26T19:00:00"}
{"role": "assistant", "content": null, "tool_calls": [...], "timestamp": "2026-02-26T19:00:05"}
{"role": "tool", "tool_call_id": "...", "name": "exec", "content": "...", "timestamp": "2026-02-26T19:00:08"}
{"role": "assistant", "content": "完成了", "timestamp": "2026-02-26T19:00:15"}
```

#### 2.4.3 loop.py 改动

```python
# _run_agent_loop 改动要点:

async def _run_agent_loop(self, initial_messages, callbacks=None):
    # ... 现有逻辑 ...
    
    while iteration < self.max_iterations:
        response = await self.provider.chat(...)
        
        if response.has_tool_calls:
            # 构建 assistant 消息
            messages = self.context.add_assistant_message(messages, ...)
            
            # 🆕 实时持久化 + 回调
            if callbacks:
                await callbacks.on_message(messages[-1])
            
            for tool_call in response.tool_calls:
                result = await self.tools.execute(...)
                messages = self.context.add_tool_result(messages, ...)
                
                # 🆕 实时持久化 + 回调
                if callbacks:
                    await callbacks.on_message(messages[-1])
        else:
            messages = self.context.add_assistant_message(messages, ...)
            
            # 🆕 实时持久化 + 回调
            if callbacks:
                await callbacks.on_message(messages[-1])
            break
    
    # 🆕 usage 回调
    if callbacks and accumulated_usage["llm_calls"] > 0:
        await callbacks.on_usage(usage_record)
```

#### 2.4.4 _process_message 改动

```python
async def _process_message(self, msg, callbacks=None):
    # ... 现有逻辑 ...
    
    # 构建 DefaultCallbacks（包含持久化 + usage 记录）
    effective_callbacks = self._build_callbacks(session, callbacks)
    
    # 🆕 user 消息实时写入
    self.sessions.append_message(session, initial_messages[-1])
    
    final_content, _, all_msgs = await self._run_agent_loop(
        initial_messages, callbacks=effective_callbacks,
    )
    
    # 🆕 不再需要 _save_turn（消息已实时写入）
    # 只更新 metadata
    self.sessions.update_metadata(session)
```

### 2.5 统一 Token 用量记录架构（Backlog #8）

#### 2.5.1 UsageRecorder 模块

```python
# 新增: nanobot/usage/__init__.py + recorder.py

class UsageRecorder:
    """统一的 token 用量记录器。
    
    在 nanobot 核心层运行，所有调用方式（CLI/Web/IM/Cron）
    都通过此模块记录 usage。
    """
    
    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = Path.home() / ".nanobot" / "workspace" / "analytics.db"
        self.db_path = db_path
        self._ensure_schema()
    
    def record(self, session_key: str, model: str,
               prompt_tokens: int, completion_tokens: int,
               total_tokens: int, llm_calls: int,
               started_at: str, finished_at: str) -> None:
        """记录一条 usage。线程安全。"""
        ...
    
    def get_global_usage(self) -> dict: ...
    def get_session_usage(self, session_key: str) -> dict: ...
    def get_daily_usage(self, days: int = 30) -> list: ...
```

#### 2.5.2 集成到 AgentLoop

```python
class AgentLoop:
    def __init__(self, ..., usage_recorder: UsageRecorder | None = None):
        self.usage_recorder = usage_recorder or UsageRecorder()
        # ...
```

在 `_run_agent_loop` 末尾：
```python
# 直接写入 SQLite（所有模式统一）
if self.usage_recorder and accumulated_usage["llm_calls"] > 0:
    self.usage_recorder.record(
        session_key=session_key,
        model=self.model,
        **accumulated_usage,
        started_at=loop_started_at,
        finished_at=finished_at,
    )

# stderr 输出保留（向后兼容 + 调试）
print(json.dumps(usage_record), file=sys.stderr)
```

#### 2.5.3 数据流对比

**改造前**：
```
CLI:       agent loop → stderr → 终端（丢弃）
Web:       agent loop → stderr → Worker 解析 → SSE → Gateway → SQLite
IM:        agent loop → stderr → 日志（丢弃）
Cron:      agent loop → stderr → 日志（丢弃）
```

**改造后**：
```
所有模式:  agent loop → UsageRecorder → SQLite（直接写入）
                      → stderr（保留，调试用）
                      → callbacks.on_usage()（通知调用方）
```

#### 2.5.4 SQLite Schema（复用现有）

复用 web-chat 的 `analytics.py` 中的 schema，迁移到 nanobot 核心：

```sql
CREATE TABLE IF NOT EXISTS token_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key       TEXT NOT NULL,
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens      INTEGER DEFAULT 0,
    llm_calls         INTEGER DEFAULT 0,
    started_at        TEXT NOT NULL,
    finished_at       TEXT NOT NULL
);
```

**数据库位置**：`~/.nanobot/workspace/analytics.db`（与 web-chat 共享同一文件）

### 2.6 SDK 接口设计（Backlog #6）

#### 2.6.1 AgentRunner

```python
# 新增: nanobot/sdk/__init__.py + runner.py

class AgentRunner:
    """面向外部调用方的 Agent 执行器。
    
    封装 AgentLoop 的初始化和调用，提供简洁的 API。
    """
    
    def __init__(self, agent_loop: AgentLoop):
        self._loop = agent_loop
    
    @classmethod
    def from_config(cls, config_path: str | None = None) -> "AgentRunner":
        """从配置文件创建 AgentRunner。"""
        from nanobot.config.loader import load_config
        config = load_config(config_path)
        # ... 创建 provider, bus, agent_loop ...
        return cls(agent_loop)
    
    async def run(
        self,
        message: str,
        session_key: str = "sdk:direct",
        callbacks: AgentCallbacks | None = None,
    ) -> AgentResult:
        """执行一次 agent 调用。"""
        result = await self._loop.process_direct(
            content=message,
            session_key=session_key,
            callbacks=callbacks,
        )
        return AgentResult(content=result, ...)
    
    async def close(self):
        """释放资源。"""
        await self._loop.close_mcp()

@dataclass
class AgentResult:
    content: str
    usage: dict | None = None
    tools_used: list[str] | None = None
```

#### 2.6.2 Worker 改造

```python
# worker.py 改造后

from nanobot.sdk import AgentRunner, AgentCallbacks

# 全局 runner（进程生命周期内复用）
_runner: AgentRunner | None = None

def get_runner():
    global _runner
    if _runner is None:
        _runner = AgentRunner.from_config()
    return _runner

class WorkerCallbacks(AgentCallbacks):
    def __init__(self, task):
        self.task = task
    
    async def on_progress(self, text, tool_hint=False):
        self.task['progress'].append(text)
        # 通知 SSE 客户端
        with self.task['_sse_lock']:
            for sse_fn in self.task['_sse_clients']:
                try: sse_fn('progress', {'text': text})
                except: pass
    
    async def on_message(self, message):
        # 消息已由核心层实时持久化，这里只做 SSE 通知
        pass
    
    async def on_usage(self, usage):
        self.task['_usage'] = usage
    
    async def on_done(self, final_content):
        self.task['status'] = 'done'
        # 通知 SSE 客户端
        ...

async def _run_task_background(session_key, message):
    runner = get_runner()
    callbacks = WorkerCallbacks(task)
    result = await runner.run(
        message=message,
        session_key=session_key,
        callbacks=callbacks,
    )
```

**改造收益**：
- 不再需要 `subprocess.Popen` + stdout/stderr 解析
- 不再有 PIPE fd 继承问题
- 不再需要 `start_new_session=True` 进程隔离
- 结构化回调替代文本行解析
- 进程内复用 Provider 连接，减少初始化开销

---

## 三、实施计划

### Phase 1: 实时 Session 持久化（Backlog #7）

**改动范围**：`session/manager.py`, `agent/loop.py`

| 步骤 | 任务 | 说明 |
|------|------|------|
| 1.1 | SessionManager.append_message() | 新增追加写入方法 |
| 1.2 | SessionManager.update_metadata() | 新增 metadata 更新方法 |
| 1.3 | _run_agent_loop 注入实时写入 | 每条消息产生后调用 append_message |
| 1.4 | _process_message 适配 | user 消息实时写入，移除 _save_turn |
| 1.5 | 测试 | CLI 模式验证中途 kill 后 JSONL 完整性 |

**风险评估**：低。主要是 SessionManager 新增方法 + loop.py 调用点变更。

### Phase 2: 统一 Token 记录（Backlog #8）

**改动范围**：新增 `usage/recorder.py`, 改动 `agent/loop.py`, `cli/commands.py`

| 步骤 | 任务 | 说明 |
|------|------|------|
| 2.1 | 创建 UsageRecorder 模块 | SQLite 操作封装 |
| 2.2 | 迁移 web-chat analytics.py 的 schema | 复用现有表结构 |
| 2.3 | AgentLoop 集成 UsageRecorder | 构造函数注入 + _run_agent_loop 写入 |
| 2.4 | CLI commands.py 初始化 UsageRecorder | agent 和 gateway 命令 |
| 2.5 | web-chat Gateway 适配 | 移除 Gateway 层的 usage 写入，改为直接查询 SQLite |
| 2.6 | 测试 | CLI 模式验证 SQLite 记录，Web 模式验证兼容性 |

**风险评估**：中。涉及 web-chat Gateway 的适配，需要确保 UsageIndicator 继续工作。

### Phase 3: SDK 化（Backlog #6）

**改动范围**：新增 `sdk/`, `agent/callbacks.py`, 改动 `agent/loop.py`, web-chat `worker.py`

| 步骤 | 任务 | 说明 |
|------|------|------|
| 3.1 | 定义 AgentCallbacks 协议 | callbacks.py |
| 3.2 | _run_agent_loop 接受 callbacks 参数 | 替换 on_progress |
| 3.3 | 创建 AgentRunner | sdk/runner.py |
| 3.4 | 改造 web-chat Worker | 从 subprocess 改为 SDK 调用 |
| 3.5 | 集成测试 | Web UI 端到端验证 |

**风险评估**：高。Worker 改造是破坏性变更，需要充分测试。建议使用 feature 分支。

### 分支策略

```
local (当前)
  └─ feat/realtime-persist    ← Phase 1
  └─ feat/unified-usage       ← Phase 2
  └─ feat/sdk                 ← Phase 3（同时在 web-chat 开 feature 分支）
```

每个 Phase 完成后合并回 `local`。

---

## 三-B、实时 Token 用量记录（Phase 4）

### 3B.1 问题

Phase 2 的 UsageRecorder 在 `_run_agent_loop` **末尾**一次性写入 SQLite。如果 agent 执行中途异常退出，`accumulated_usage`（内存字典）全部丢失。

### 3B.2 改造方案

将 usage 写入从"循环结束后批量写入"改为"每次 LLM 调用后立即写入"：

```python
# _run_agent_loop 改造要点

while iteration < self.max_iterations:
    response = await self.provider.chat(...)

    # 🆕 每次 LLM 调用后立即写入 SQLite
    if response.usage and self.usage_recorder is not None:
        now = datetime.now().isoformat()
        self.usage_recorder.record(
            session_key=session_key,
            model=self.model,
            prompt_tokens=response.usage.get("prompt_tokens", 0),
            completion_tokens=response.usage.get("completion_tokens", 0),
            total_tokens=response.usage.get("total_tokens", 0),
            llm_calls=1,
            started_at=now,
            finished_at=now,
        )

    # 累加到 accumulated_usage 仍保留（用于 stderr 汇总输出 + callbacks.on_usage）
    if response.usage:
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            accumulated_usage[key] += response.usage.get(key, 0)
        accumulated_usage["llm_calls"] += 1

    # ... 后续 tool call 处理不变 ...

# 循环结束后：
# - 不再调用 usage_recorder.record()（已逐次写入）
# - stderr JSON 输出保留（汇总，向后兼容）
# - callbacks.on_usage 保留（汇总，通知调用方）
```

### 3B.3 时间戳设计

每条 usage 记录的 `started_at` 和 `finished_at` 都取 `datetime.now().isoformat()`，与同一次 LLM 调用产生的 assistant 消息的 `timestamp` 一致。

这意味着可以通过 `session_key + started_at` 将 usage 记录与 session JSONL 中的消息关联。

### 3B.4 对聚合查询的影响

**无影响**。Web-chat 的 UsageIndicator 和 UsagePage 都使用 `SUM()` 聚合查询：

```sql
SELECT SUM(prompt_tokens), SUM(completion_tokens), SUM(total_tokens), SUM(llm_calls)
FROM token_usage WHERE session_key = ?
```

原来一个 turn 写入 1 条记录（`llm_calls=3, total_tokens=15000`），现在写入 3 条记录（每条 `llm_calls=1`），`SUM()` 结果完全相同。

---

## 四、与 Web Chat 的交互

### 4.1 当前交互方式

```
Web Chat Gateway (:8081) ──HTTP──→ Worker (:8082) ──subprocess──→ nanobot CLI
```

### 4.2 Phase 1-2 后的交互（不变）

Phase 1 和 Phase 2 的改动在 nanobot 核心内部，Worker 仍然通过 subprocess 调用。但：
- Session JSONL 实时写入（Worker 不需要等待子进程结束就能看到中间结果）
- Usage 直接写入 SQLite（Gateway 不需要从 Worker SSE 获取 usage）

### 4.3 Phase 3 后的交互（SDK 调用）

```
Web Chat Gateway (:8081) ──HTTP──→ Worker (:8082) ──SDK──→ AgentRunner (进程内)
```

Worker 不再启动子进程，而是在进程内直接调用 AgentRunner。

**Gateway 改动最小化**：
- `/api/usage` 路由不变（仍然查询 SQLite）
- `/api/sessions` 路由不变（仍然读取 JSONL）
- SSE 流的数据源从 Worker stdout 改为 SDK callbacks

---

## 五、文件变更清单（预估）

### nanobot 核心

| 文件 | 改动类型 | Phase |
|------|----------|-------|
| `agent/callbacks.py` | 新增 | 3 |
| `agent/loop.py` | 修改 | 1, 2, 3 |
| `session/manager.py` | 修改 | 1 |
| `usage/__init__.py` | 新增 | 2 |
| `usage/recorder.py` | 新增 | 2 |
| `sdk/__init__.py` | 新增 | 3 |
| `sdk/runner.py` | 新增 | 3 |
| `cli/commands.py` | 修改 | 2 |

### web-chat

| 文件 | 改动类型 | Phase |
|------|----------|-------|
| `worker.py` | 修改 | 3 |
| `gateway.py` | 修改 | 2 (移除 usage 写入) |
| `analytics.py` | 可能移除 | 2 (迁移到 nanobot 核心) |

---

## 六、文件访问审计日志架构（Phase 7）

### 6.1 设计原则

1. **ToolRegistry 拦截层**：在工具注册表的 `execute()` 方法中统一拦截，不修改任何具体工具
2. **结构化提取**：针对不同工具类型，提取有意义的审计字段（路径、字节数、命令等）
3. **上下文感知**：审计记录包含 session_key、channel、chat_id 等关联信息
4. **与现有日志体系一致**：存储格式和目录结构与 LLM 详情日志（Phase 6）保持一致

### 6.2 模块结构

```
nanobot/
├── audit/
│   ├── __init__.py        # 导出 AuditLogger
│   └── logger.py          # AuditLogger — 审计日志记录器
├── agent/
│   └── tools/
│       └── registry.py    # ToolRegistry — 新增审计拦截逻辑
```

### 6.3 AuditLogger 设计

```python
# nanobot/audit/logger.py

@dataclass
class AuditEntry:
    """一条审计日志记录。"""
    timestamp: str
    session_key: str
    channel: str
    chat_id: str
    tool: str              # 工具名
    action: str            # 操作类型: read/write/edit/list/exec/search/fetch/spawn/cron/message/mcp
    params: dict           # 工具参数（敏感内容可截断）
    result: dict           # 结果摘要
    resolved_path: str | None  # 解析后的绝对路径（文件操作工具）
    error: str | None      # 错误信息（如果失败）
    duration_ms: float     # 执行耗时（毫秒）

class AuditLogger:
    """文件访问审计日志记录器。
    
    按天分文件写入 JSONL 格式的审计日志。
    """
    
    def __init__(self, log_dir: Path | None = None, enabled: bool = True):
        if log_dir is None:
            log_dir = Path.home() / ".nanobot" / "workspace" / "audit-logs"
        self.log_dir = log_dir
        self.enabled = enabled
    
    def log(self, entry: AuditEntry) -> None:
        """写入一条审计日志（同步 append）。"""
        if not self.enabled:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        date_str = entry.timestamp[:10]  # YYYY-MM-DD
        path = self.log_dir / f"{date_str}.jsonl"
        line = json.dumps(asdict(entry), ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
```

### 6.4 ToolRegistry 审计拦截

```python
# registry.py — execute() 改造

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._audit_logger: AuditLogger | None = None
        self._audit_context: dict = {}  # session_key, channel, chat_id
    
    def set_audit_logger(self, logger: AuditLogger) -> None:
        self._audit_logger = logger
    
    def set_audit_context(self, **kwargs) -> None:
        """设置审计上下文（session_key, channel, chat_id）。"""
        self._audit_context.update(kwargs)
    
    async def execute(self, name: str, params: dict) -> str:
        # ... 现有的工具查找和参数校验逻辑 ...
        
        start_time = time.monotonic()
        result = await tool.execute(**params)
        duration_ms = (time.monotonic() - start_time) * 1000
        
        # 审计日志
        if self._audit_logger is not None:
            entry = self._build_audit_entry(name, params, result, duration_ms)
            self._audit_logger.log(entry)
        
        return result
    
    def _build_audit_entry(self, tool_name, params, result, duration_ms) -> AuditEntry:
        """根据工具类型构建审计条目。"""
        # 针对不同工具提取有意义的字段
        ...
```

### 6.5 各工具的审计字段提取规则

| 工具 | action | params 提取 | result 提取 | resolved_path |
|------|--------|------------|-------------|---------------|
| read_file | read | `{"path": ...}` | `{"success": bool, "size": int}` | 解析后的绝对路径 |
| write_file | write | `{"path": ...}` | `{"success": bool, "bytes_written": int, "is_new_file": bool}` | 解析后的绝对路径 |
| edit_file | edit | `{"path": ..., "old_text_preview": ..., "new_text_preview": ...}` | `{"success": bool}` | 解析后的绝对路径 |
| list_dir | list | `{"path": ...}` | `{"success": bool, "entry_count": int}` | 解析后的绝对路径 |
| exec | exec | `{"command": ..., "working_dir": ...}` | `{"success": bool, "exit_code": int, "blocked": bool}` | null |
| web_search | search | `{"query": ...}` | `{"success": bool}` | null |
| web_fetch | fetch | `{"url": ...}` | `{"success": bool, "status_code": int}` | null |
| spawn | spawn | `{"task_preview": ...}` | `{"success": bool}` | null |
| cron | cron | `{"action": ..., "message_preview": ...}` | `{"success": bool}` | null |
| message | message | `{"channel": ..., "chat_id": ...}` | `{"success": bool}` | null |
| mcp_* | mcp | `{参数摘要}` | `{"success": bool}` | null |

### 6.6 上下文传递流程

```
_process_message(msg)
    │
    ├── self.tools.set_audit_context(
    │       session_key=key,
    │       channel=msg.channel,
    │       chat_id=msg.chat_id
    │   )
    │
    └── _run_agent_loop(messages)
            │
            └── self.tools.execute(name, params)
                    │
                    ├── tool.execute(**params)  ← 实际执行
                    │
                    └── audit_logger.log(entry)  ← 审计记录
```

### 6.7 初始化集成

```python
# cli/commands.py + sdk/runner.py

audit_logger = AuditLogger()  # 默认 ~/.nanobot/workspace/audit-logs/
agent_loop = AgentLoop(..., audit_logger=audit_logger)

# AgentLoop.__init__ 中
self.tools.set_audit_logger(audit_logger)
```

### 6.8 存储与查询

**日志目录**: `~/.nanobot/workspace/audit-logs/`

**查询示例**:
```bash
# 查看今天所有写操作
grep '"action":"write"' audit-logs/2026-02-27.jsonl | jq .

# 查看某 session 的所有文件操作
grep '"session_key":"webchat:123"' audit-logs/2026-02-27.jsonl | jq .

# 查看所有失败的操作
grep '"success":false' audit-logs/2026-02-27.jsonl | jq .

# 查看对特定路径的访问
grep 'MEMORY.md' audit-logs/2026-02-27.jsonl | jq .

# 统计每个工具的调用次数
cat audit-logs/2026-02-27.jsonl | jq -r '.tool' | sort | uniq -c | sort -rn
```

---

## 七、ProviderPool — 运行时 Provider 动态切换（Phase 16）

### 7.1 设计原则

1. **引入 ProviderPool 代理类**：实现 `LLMProvider` 接口，作为所有已配置 Provider 的统一门面
2. **AgentLoop 无感知**：AgentLoop 仍调用 `self.provider.chat()`，ProviderPool 内部路由到当前 active provider
3. **纯运行时状态**：切换 Provider 不修改 `config.json`，仅改变内存中的 active 指针
4. **向后兼容**：单 Provider 场景下，ProviderPool 退化为只有一个条目的 Pool，行为不变

### 7.2 架构图

```
config.json (静态声明)
  └── providers
        ├── anthropic       = { apiKey, apiBase }
        ├── anthropic_proxy = { apiKey, apiBase }
        ├── deepseek        = { apiKey }
        └── ...

启动时:
  _make_provider(config)
    → 遍历所有 providers，为每个有 apiKey 的构建 LLMProvider 实例
    → ProviderPool(
        providers={
          "anthropic": (LiteLLMProvider(...), "claude-opus-4-6"),
          "deepseek":  (LiteLLMProvider(...), "deepseek-chat"),
          ...
        },
        active_provider="anthropic",
        active_model="claude-opus-4-6",
      )

AgentLoop.__init__(provider=pool)
  → self.provider = pool
  → self.model = pool.active_model
```

### 7.3 ProviderPool 类设计

```python
class ProviderPool(LLMProvider):
    """运行时 Provider 动态切换池。

    实现 LLMProvider 接口，AgentLoop 将其视为普通 Provider。
    内部维护多个 Provider 实例，通过 active 指针路由请求。
    """

    def __init__(
        self,
        providers: dict[str, tuple[LLMProvider, str]],  # name → (provider, default_model)
        active_provider: str,
        active_model: str,
    ):
        self._providers = providers          # 所有已构建的 Provider
        self._active_provider = active_provider
        self._active_model = active_model

    # ── Properties ──────────────────────────────────────────

    @property
    def active_provider(self) -> str:
        """当前激活的 Provider 名称。"""
        return self._active_provider

    @property
    def active_model(self) -> str:
        """当前激活的模型名称。"""
        return self._active_model

    @property
    def available(self) -> dict[str, str]:
        """所有可用的 Provider 及其默认模型。"""
        return {name: model for name, (_, model) in self._providers.items()}

    # ── Methods ─────────────────────────────────────────────

    def switch(self, provider: str, model: str | None = None) -> None:
        """切换到指定 Provider（可选指定模型）。

        Args:
            provider: Provider 名称，必须在 available 中
            model: 可选模型名；不传则使用该 Provider 的默认模型
        """
        if provider not in self._providers:
            raise ValueError(f"Unknown provider: {provider}")
        self._active_provider = provider
        _, default_model = self._providers[provider]
        self._active_model = model or default_model

    async def chat(self, messages, **kwargs) -> "LLMResponse":
        """路由到当前 active Provider 的 chat 方法。

        忽略调用方传入的 model 参数，始终使用 active_model。
        """
        provider_instance, _ = self._providers[self._active_provider]
        kwargs["model"] = self._active_model
        return await provider_instance.chat(messages, **kwargs)
```

### 7.4 `/provider` 斜杠命令

在 `AgentLoop._process_message` 中处理，与现有斜杠命令（`/history`、`/compact` 等）同级：

| 命令 | 行为 | 示例 |
|------|------|------|
| `/provider` | 显示当前状态：active provider、active model、所有可用 provider | `/provider` |
| `/provider <name>` | 切换到指定 Provider，使用其默认模型 | `/provider deepseek` |
| `/provider <name> <model>` | 切换到指定 Provider 和模型 | `/provider anthropic claude-sonnet-4-20250514` |

**实现要点**：

```python
# agent/loop.py — _process_message 中

if content.startswith("/provider"):
    parts = content.split()
    if len(parts) == 1:
        # 显示状态
        status = f"Active: {self.provider.active_provider} / {self.provider.active_model}\n"
        status += "Available:\n"
        for name, model in self.provider.available.items():
            marker = " ←" if name == self.provider.active_provider else ""
            status += f"  {name}: {model}{marker}\n"
        return status
    else:
        name = parts[1]
        model = parts[2] if len(parts) > 2 else None
        self.provider.switch(name, model)
        self.model = self.provider.active_model  # 同步更新 AgentLoop.model
        return f"Switched to {self.provider.active_provider} / {self.provider.active_model}"
```

**多端支持**：CLI 交互模式、Gateway（IM 渠道）、Web Chat 均可使用 `/provider` 命令，因为它在 `_process_message` 层处理，所有调用路径都经过此方法。

### 7.5 `_make_provider` 改造

```python
# cli/commands.py — _make_provider 改造

PROVIDER_DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-6",
    "anthropic_proxy": "claude-opus-4-6",
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o",
    "gemini": "gemini-2.5-pro",
    # ... 其他 well-known providers
}

def _make_provider(config) -> ProviderPool:
    """构建 ProviderPool，包含所有已配置的 Provider。"""
    providers = {}

    for name, spec in config.providers.items():
        if not spec.get("apiKey"):
            continue  # 跳过未配置 apiKey 的 provider

        provider_instance = _build_single_provider(name, spec)
        default_model = PROVIDER_DEFAULT_MODELS.get(name, spec.get("defaultModel", "unknown"))
        providers[name] = (provider_instance, default_model)

    # 确定初始 active provider：从 config.agents.defaults.model 推断
    configured_model = config.agents.get("defaults", {}).get("model", "")
    active_provider, active_model = _resolve_active(providers, configured_model)

    return ProviderPool(
        providers=providers,
        active_provider=active_provider,
        active_model=active_model,
    )
```

**向后兼容**：如果只配置了一个 Provider，ProviderPool 只有一个条目，行为与改造前完全一致。

### 7.6 模块结构

```
nanobot/providers/
├── __init__.py          # 导出 ProviderPool
├── pool.py              # ProviderPool 类
├── base.py              # LLMProvider 基类 + LLMResponse
├── litellm_provider.py  # LiteLLM 统一 Provider
├── custom_provider.py   # 自定义 OpenAI 兼容 Provider
├── registry.py          # Provider 注册表（新增 anthropic_proxy ProviderSpec）
```

### 7.7 文件变更清单

| 文件 | 改动类型 | Phase | 说明 |
|------|----------|-------|------|
| `providers/pool.py` | 新增 | 16 | ProviderPool 类，实现 LLMProvider 接口 |
| `providers/registry.py` | 修改 | 16 | 新增 `anthropic_proxy` ProviderSpec |
| `providers/__init__.py` | 修改 | 16 | 导出 ProviderPool |
| `config/schema.py` | 修改 | 16 | 支持多 Provider 声明的 schema 校验 |
| `cli/commands.py` | 修改 | 16 | `_make_provider` 改造，构建 ProviderPool；`PROVIDER_DEFAULT_MODELS` 字典 |
| `agent/loop.py` | 修改 | 16 | `/provider` 斜杠命令处理；`self.model` 同步更新 |
| `tests/test_provider_pool.py` | 新增 | 16 | ProviderPool 单元测试（switch、chat 路由、边界情况） |

---

## 八、Gateway 并发执行架构（Phase 19）

### 8.1 设计原则

1. **Per-session 并发，同 session 串行**：不同 session 的消息并行处理，同一 session 内通过 inject 机制串行化
2. **最小侵入**：只改 `run()` 调度层和 tool context 层，`_process_message()` 核心逻辑基本不变
3. **向后兼容**：`process_direct()` / SDK 调用路径不受影响

### 8.2 架构图

```
                    ┌─────────────────────────────────────────────────┐
                    │              AgentLoop.run()                     │
                    │           (Concurrent Dispatcher)                │
                    │                                                 │
  bus.inbound ──────┤   ┌─ session_key_1 ─┐                          │
                    │   │  SessionWorker   │                          │
                    │   │  - task          │ ← asyncio.Task           │
                    │   │  - callbacks     │ ← GatewayCallbacks       │
                    │   │  - provider/model│ ← per-session            │
                    │   │  - tools (clone) │ ← isolated ToolRegistry  │
                    │   └──────────────────┘                          │
                    │                                                 │
                    │   ┌─ session_key_2 ─┐                          │
                    │   │  SessionWorker   │                          │
                    │   │  - task          │                          │
                    │   │  - callbacks     │                          │
                    │   │  - provider/model│                          │
                    │   │  - tools (clone) │                          │
                    │   └──────────────────┘                          │
                    │                                                 │
                    │   Message routing:                               │
                    │   - new session → create task                    │
                    │   - active session → inject to callbacks         │
                    │   - /stop → cancel session task                  │
                    │   - /provider → per-session switch               │
                    └─────────────────────────────────────────────────┘
```

### 8.3 Dispatcher 流程

```python
async def run(self):
    active_sessions: dict[str, SessionWorker] = {}
    
    while self._running:
        msg = await bus.consume_inbound()  # or timeout
        session_key = resolve_session_key(msg)
        
        # 1. /stop → 精确取消
        if msg == "/stop":
            if worker := active_sessions.get(session_key):
                worker.task.cancel()
            continue
        
        # 2. /provider → per-session 切换
        if msg.startswith("/provider"):
            handle_provider_command(msg, session_key)
            continue
        
        # 3. Active session → inject
        if session_key in active_sessions:
            worker = active_sessions[session_key]
            await worker.callbacks.inject(msg.content)
            continue
        
        # 4. New/idle session → start task
        callbacks = GatewayCallbacks()
        provider, model = pool.get_for_session(session_key)
        tools_clone = self.tools.clone_for_session(session_key, msg)
        task = asyncio.create_task(
            self._process_message_concurrent(msg, provider, model, tools_clone, callbacks)
        )
        active_sessions[session_key] = SessionWorker(task, callbacks, ...)
        task.add_done_callback(lambda t, k=session_key: active_sessions.pop(k, None))
```

### 8.4 Tool Context 隔离方案

```
共享（只读）:                    Per-task 克隆（有状态）:
┌──────────────┐                ┌──────────────────┐
│ ReadFileTool │ ←── shared ──→ │ MessageTool      │ ← clone per task
│ WriteFileTool│                │ SpawnTool        │ ← clone per task
│ EditFileTool │                │ CronTool         │ ← clone per task
│ ListDirTool  │                │ AuditContext     │ ← clone per task
│ ExecTool     │                └──────────────────┘
│ WebSearchTool│
│ WebFetchTool │
│ MCP Tools    │
└──────────────┘
```

**ToolRegistry.clone_for_session()** 方法：
- 创建新的 ToolRegistry 实例
- 共享无状态 tool 的引用（ReadFile、WriteFile 等）
- 为有状态 tool（Message、Spawn、Cron）创建新实例
- 设置独立的 audit context

### 8.5 Per-Session Provider 方案

```
ProviderPool
├── _providers: {"anthropic": (instance, model), "deepseek": (instance, model)}
├── _active_provider: "anthropic"          ← 全局默认
├── _active_model: "claude-opus-4-6"    ← 全局默认
└── _session_overrides: {                  ← per-session 覆盖
        "feishu.ST:ou_xxx": ("deepseek", "deepseek-chat"),
    }

get_for_session("feishu.lab:ou_yyy")  → 无 override → 返回全局 (anthropic, claude-opus-4-6)
get_for_session("feishu.ST:ou_xxx")   → 有 override → 返回 (deepseek, deepseek-chat)
```

### 8.6 GatewayCallbacks 设计

```python
class GatewayCallbacks(DefaultCallbacks):
    """Per-session callbacks with user injection support."""
    
    def __init__(self, bus: MessageBus, channel: str, chat_id: str):
        self._inject_queue: asyncio.Queue[str] = asyncio.Queue()
        self._bus = bus
        self._channel = channel
        self._chat_id = chat_id
    
    async def check_user_input(self) -> str | None:
        """Called by _run_agent_loop after each tool round."""
        try:
            return self._inject_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
    
    async def inject(self, text: str):
        """Called by dispatcher when same session receives new message."""
        await self._inject_queue.put(text)
    
    async def on_progress(self, text: str, *, tool_hint: bool = False):
        """Forward progress to bus as outbound message."""
        meta = {"_progress": True, "_tool_hint": tool_hint}
        await self._bus.publish_outbound(OutboundMessage(
            channel=self._channel, chat_id=self._chat_id,
            content=text, metadata=meta,
        ))
```

### 8.7 _process_message 参数化

```python
async def _process_message(
    self,
    msg: InboundMessage,
    session_key: str | None = None,
    on_progress: Callable | None = None,
    callbacks: DefaultCallbacks | None = None,
    # ── Phase 19 新增 ──
    provider: LLMProvider | None = None,    # per-session provider
    model: str | None = None,               # per-session model
    tools: ToolRegistry | None = None,      # per-session tools clone
) -> OutboundMessage | None:
    # 使用传入的 provider/model/tools，否则 fallback 到 self.*
    _provider = provider or self.provider
    _model = model or self.model
    _tools = tools or self.tools
    ...
```

### 8.8 文件变更清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `agent/loop.py` | 重构 | run() 并发 dispatcher; _process_message 参数化; GatewayCallbacks; /stop /provider per-session |
| `agent/tools/registry.py` | 新增方法 | `clone_for_session()` 浅拷贝 + 有状态 tool 克隆 |
| `agent/tools/message.py` | 新增方法 | `clone()` 方法，创建独立实例 |
| `agent/tools/spawn.py` | 新增方法 | `clone()` 方法 |
| `agent/tools/cron.py` | 新增方法 | `clone()` 方法 |
| `providers/pool.py` | 新增方法 | `get_for_session()`, `switch_for_session()` |
| `agent/callbacks.py` | 可能微调 | 确保 check_user_input 接口兼容 |

---

## 九、/session 状态查询命令（Phase 20）

> 需求：REQUIREMENTS.md §十九

### 9.1 概述

`/session` 斜杠命令提供当前 session 的只读状态查询，包括 session key、执行状态、provider/model、Token 累计用量、消息统计、时间信息。

### 9.2 实现

```python
def _handle_session_command(
    self,
    msg: InboundMessage,
    session_key: str | None = None,
    active_sessions: dict | None = None,  # Gateway 并发模式传入
) -> OutboundMessage:
```

**状态判断逻辑**：
- `active_sessions` 不为 None 且包含当前 session key → 检查 `worker.task.done()`
  - `not done` → 🔄 执行中
  - `done` → 💤 空闲
- `active_sessions` 为 None（CLI/直接调用模式） → 💤 空闲

**Provider 信息获取**：
- `ProviderPool` 实例 → `get_session_provider_name(key)` + `get_session_model(key)`
- 非 ProviderPool → `type(self.provider).__name__` + `self.model`

**Token 用量获取**：
- 通过 `self.usage_recorder.get_session_usage(key)` 从 `analytics.db` 聚合
- 返回 `prompt_tokens`, `completion_tokens`, `total_tokens`, `llm_calls`
- 若 `usage_recorder` 为 None（未配置），显示 "N/A"

### 9.3 命令路由

在两个入口点添加 `/session` 路由：

1. **`run()` 并发 dispatcher**：在 `/provider` 之后、inject 逻辑之前，传入 `active_sessions`
2. **`_process_message()` 直接模式**：在 `/provider` 之后、`/stop` 之前，不传 `active_sessions`

---

## 十、迭代预算软限制提醒 + exec 动态超时（Phase 25）

> 需求：REQUIREMENTS.md §二十二 + §二十三

### 10.1 概述

Phase 25 包含两个独立的轻量改进，均源自 eval-bench 批量构造的复盘：

| 子项 | 改动文件 | 改动量 |
|------|----------|--------|
| 25a: 迭代预算提醒 | `agent/loop.py` | ~15 行 |
| 25b: exec 动态超时 | `agent/tools/shell.py` | ~10 行 |

### 10.2 迭代预算软限制提醒（25a）

#### 10.2.1 注入位置

在 `_run_agent_loop()` 的 `while iteration < self.max_iterations` 循环内，每次 iteration 递增后、调用 `provider.chat()` 之前检查：

```python
while iteration < self.max_iterations:
    iteration += 1

    # ── Budget alert ──
    remaining = self.max_iterations - iteration
    if remaining == _budget_alert_threshold(self.max_iterations):
        messages.append({
            "role": "system",
            "content": (
                f"⚠️ Budget alert: You have {remaining} tool call iterations remaining "
                f"(out of {self.max_iterations}). Please prioritize saving your work state "
                f"and wrapping up gracefully."
            ),
        })

    response = await self._chat_with_retry(...)
    # ... 后续逻辑不变 ...
```

#### 10.2.2 阈值函数

```python
def _budget_alert_threshold(max_iterations: int) -> int:
    """计算 budget alert 的触发阈值（剩余迭代数）。
    
    - max_iterations >= 20: threshold = 10
    - max_iterations < 20:  threshold = max(3, max_iterations // 4)
    """
    if max_iterations >= 20:
        return 10
    return max(3, max_iterations // 4)
```

#### 10.2.3 不持久化

budget alert 消息只存在于当前 turn 的 `messages` 列表中，不会被 `append_message()` 写入 JSONL（因为它是在循环内动态注入的，不经过持久化路径）。

如果 turn 结束后用户再发消息，新 turn 的 `get_history()` 不会包含此消息。

### 10.3 exec 动态超时（25b）

#### 10.3.1 参数扩展

```python
class ExecTool(Tool):
    MAX_TIMEOUT = 600  # 安全上限: 10 分钟

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory for the command",
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Optional timeout in seconds for this command "
                        "(overrides default). Use for long-running commands "
                        "like git clone, large file operations, etc."
                    ),
                },
            },
            "required": ["command"],
        }
```

#### 10.3.2 执行逻辑

```python
async def execute(
    self, command: str, working_dir: str | None = None, timeout: int | None = None
) -> str:
    # 动态超时：调用时传入 > 实例默认值，硬上限保护
    effective_timeout = self.timeout
    if timeout is not None:
        effective_timeout = min(timeout, self.MAX_TIMEOUT)

    # ... 安全检查逻辑不变 ...

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=effective_timeout
        )
    except asyncio.TimeoutError:
        # ... kill process ...
        return f"Error: Command timed out after {effective_timeout} seconds"
```

#### 10.3.3 安全考量

- **硬上限 600s**：`min(timeout, MAX_TIMEOUT)` 防止 LLM 设置过大的超时
- **不影响安全检查**：deny_patterns / allow_patterns 等检查在超时逻辑之前执行
- **向后兼容**：`timeout=None` 时行为完全不变

### 10.4 测试设计

#### 25a 测试（`tests/test_budget_alert.py`）

| 测试 | 说明 |
|------|------|
| `test_threshold_normal` | `max_iterations=40` → threshold=10 |
| `test_threshold_small` | `max_iterations=12` → threshold=3 |
| `test_threshold_minimum` | `max_iterations=8` → threshold=3 (下限) |
| `test_alert_injected_once` | 验证 alert 消息只注入一次 |
| `test_alert_content` | 验证消息内容包含 remaining 和 max_iterations |

#### 25b 测试（`tests/test_exec_timeout.py`）

| 测试 | 说明 |
|------|------|
| `test_dynamic_timeout` | 传入 `timeout=5`，验证命令使用 5s 超时 |
| `test_default_fallback` | 不传 `timeout`，验证使用实例默认值 |
| `test_max_timeout_cap` | 传入 `timeout=9999`，验证被限制为 600s |
| `test_timeout_error_message` | 超时后错误消息包含实际使用的超时值 |

### 10.5 文件变更清单

| 文件 | 改动类型 | 子项 |
|------|----------|------|
| `nanobot/agent/loop.py` | 修改 | 25a: budget alert 注入 |
| `nanobot/agent/tools/shell.py` | 修改 | 25b: timeout 参数 + MAX_TIMEOUT |
| `tests/test_budget_alert.py` | 新增 | 25a: 5 个测试 |
| `tests/test_exec_timeout.py` | 新增 | 25b: 4 个测试 |

---

*本文档将随开发进展持续更新。*
