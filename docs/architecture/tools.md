# 工具相关架构

> 本文件包含文件访问审计日志、/session 状态查询、exec 动态超时、read_file 大文件保护的架构设计。

## 本文件索引

| 章节 | 标题 |
|------|------|
| §六 | 文件访问审计日志架构 |
| §九 | /session 状态查询命令 |
| §十 | 迭代预算软限制提醒 + exec 动态超时 |
| §十四 | read_file 大文件保护 |

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


## 十四、read_file 大文件保护 (§34)

### 14.1 概述

`ReadFileTool` 新增双重默认限制（≤100 行 且 ≤20KB），防止 agent 自主运行时意外读取大文件导致 context 膨胀。

### 14.2 保护机制

```
检查顺序:
1. stat() 获取文件字节大小 → 超过硬上限(1MB) → 直接报错（不读文件）
2. read_text() → 检查行数和字节数 → 任一超过软限制 → 报错（附建议）
3. 两个软限制都满足 → 返回内容
```

### 14.3 参数设计

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_lines` | int (optional) | 100 | 最大行数限制，模型可自行扩大 |
| `max_size` | int (optional) | 20000 (20KB) | 最大字节数限制，模型可自行扩大 |

- `max_size` 会被 clamp 到硬上限（`config.tools.read_file_hard_limit`，默认 1MB）
- `max_lines` 无硬上限（行数不影响内存安全）

### 14.4 配置

`config.json`:
```json
{
  "tools": {
    "readFileHardLimit": 1048576
  }
}
```

对应 `ToolsConfig.read_file_hard_limit: int = 1048576`（snake_case，alias generator 自动转 camelCase）。

### 14.5 硬上限传递链路

```
config.json → Config.tools.read_file_hard_limit
  → AgentLoop.__init__(read_file_hard_limit=...)
    → _register_default_tools() → ReadFileTool(hard_limit=...)
    → SubagentManager(read_file_hard_limit=...)
      → _run_subagent() → ReadFileTool(hard_limit=...)
  → cli/commands.py: 3 处 AgentLoop 实例化
  → sdk/runner.py: 1 处 AgentLoop 实例化
```

### 14.6 文件变更清单

| 文件 | 改动 |
|------|------|
| `config/schema.py` | `ToolsConfig` 新增 `read_file_hard_limit` 字段 |
| `agent/tools/filesystem.py` | `ReadFileTool` 双重限制 + 参数扩展 + `_human_size()` 辅助函数 |
| `agent/loop.py` | 构造函数新增参数 + `_register_default_tools` 传递硬上限 + SubagentManager 传递 |
| `agent/subagent.py` | 构造函数新增参数 + `_run_subagent` 传递硬上限 |
| `cli/commands.py` | 3 处 AgentLoop 实例化传递配置值 |
| `sdk/runner.py` | 1 处 AgentLoop 实例化传递配置值 |
| `tests/test_read_file_limit.py` | 39 项新测试 |

---

