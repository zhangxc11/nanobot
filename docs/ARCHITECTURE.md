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

*本文档将随开发进展持续更新。*
