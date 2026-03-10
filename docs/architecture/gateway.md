# Gateway 并发执行架构

> 本文件包含 Gateway 并发执行、Dispatcher、Tool Context 隔离的架构设计。

## 本文件索引

| 章节 | 标题 |
|------|------|
| §八 | Gateway 并发执行架构 |

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
        self._inject_queue: asyncio.Queue[str | dict] = asyncio.Queue()
        self._bus = bus
        self._channel = channel
        self._chat_id = chat_id
    
    async def check_user_input(self) -> str | dict | None:
        """Called by _run_agent_loop after each tool round.
        
        Returns:
            str: user message (role="user")
            dict: structured message with role/content keys (e.g. from SessionMessenger)
            None: no pending input
        """
        try:
            return self._inject_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
    
    async def inject(self, text: str | dict):
        """Called by dispatcher when same session receives new message,
        or by SessionMessenger for inter-session communication.
        
        Args:
            text: plain str (user message) or dict with "role"/"content" keys
        """
        await self._inject_queue.put(text)
    
    async def on_progress(self, text: str, *, tool_hint: bool = False):
        """Forward progress to bus as outbound message."""
        meta = {"_progress": True, "_tool_hint": tool_hint}
        await self._bus.publish_outbound(OutboundMessage(
            channel=self._channel, chat_id=self._chat_id,
            content=text, metadata=meta,
        ))
```

### 8.7 Subagent 回报消息 Role 策略

> **§35 更新**：subagent 回报消息改回 `role="user"`，通过 prompt 指导防止误执行。

subagent 完成后通过 SessionMessenger 向父 session 发送回报。回报消息以 `role="user"` 注入，announce_content 末尾的 prompt 指导防止 agent 误将回报当成新用户指令：

| 路径 | 判定方式 | role |
|------|---------|------|
| inject（父 session 运行中） | `isinstance(injected, dict)` → 使用 dict 中的 `role` 字段 | `"user"` |
| trigger（父 session 空闲） | 正常 `build_messages` 流程 | `"user"` |
| 用户消息 inject | `isinstance(injected, str)` | `"user"` |
| 用户消息 trigger | `msg.channel != "session_messenger"` | `"user"` |

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

