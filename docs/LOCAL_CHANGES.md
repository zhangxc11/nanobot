# nanobot 核心 — local 分支改动记录

> 本文档记录 `local` 分支相对于 `main`（上游）的所有自定义改动。
> `main` 分支跟上游同步，`local` 分支用于本地自定义修改。

---

## 分支策略

```
main     ← 跟上游 HKUDS/nanobot 同步
local    ← 本地自定义改动（基于 main）
```

定期从 main rebase/merge 到 local 保持同步。

---

## 改动总览

| Commit | 文件 | 改动类型 | 说明 |
|--------|------|----------|------|
| `81d4947` | `agent/context.py` | fix | 消息 timestamp 改为创建时记录 |
| `18f39a7` | `agent/loop.py` | feat | Token usage tracking — 累计 LLM 调用 usage |
| `9a10747` | `agent/loop.py` | feat | Usage 增加 started_at/finished_at 时间区间 |
| `8f0cc2d` | `agent/loop.py`, `session/manager.py` | refactor | 移除 JSONL usage 写入，改为 stderr JSON 输出 |
| `dae3b53` | `agent/loop.py` | fix | Max iterations 消息写入 JSONL（Web UI 可见） |
| `c14804d` | `session/manager.py` | fix | 防止 history 窗口截断产生孤立 tool_result |
| `d2a5769` | `agent/tools/shell.py` | fix | exec 工具拒绝含 `&` 后台操作符的命令 |
| `5528969` | `agent/loop.py`, `session/manager.py` | feat | 实时 Session 持久化 — 每条消息立即追加写入 JSONL |
| `863b9f0` | `agent/loop.py`, `cli/commands.py`, `usage/recorder.py` (新) | feat | 统一 Token 记录 — UsageRecorder 直接写入 SQLite |
| `2315216` | `agent/callbacks.py` (新), `agent/loop.py`, `sdk/__init__.py` (新), `sdk/runner.py` (新) | feat | SDK 层 — AgentCallbacks + AgentRunner + callbacks in AgentLoop |
| `17cdef8` | `agent/loop.py`, `tests/test_realtime_usage.py` (新) | feat | 实时 Token 用量记录 — 每次 LLM 调用立即写入 SQLite |
| TBD | `session/manager.py`, `tests/test_session_repair.py` (新) | fix | Session 自修复 — 未完成 tool_call 链移除 + 错误消息清理 |

---

## 详细改动说明

### 1. 消息 timestamp 精确化 (`81d4947`)

**文件**: `nanobot/agent/context.py`

**问题**: 所有消息（user/assistant/tool）的 timestamp 都是任务完成时批量保存的时间，而非各自实际发生的时间。对于长时间运行的任务（如 4 分钟），偏差显著。

**改动**: 在 `build_messages`、`add_assistant_message`、`add_tool_result` 三个消息创建函数中，`messages.append(...)` 时立即记录 `timestamp: datetime.now().isoformat()`。`_save_turn` 的 `setdefault` 作为兜底保留。

---

### 2. Token usage tracking (`18f39a7`, `9a10747`, `8f0cc2d`)

**文件**: `nanobot/agent/loop.py`, `nanobot/session/manager.py`

**演进历程**:
1. **v1** (`18f39a7`): 在 `_run_agent_loop` 中累计每次 `provider.chat()` 的 usage，保存为 session JSONL 中的 `_type: "usage"` 记录
2. **v2** (`9a10747`): 增加 `started_at`/`finished_at` 时间区间字段
3. **v3** (`8f0cc2d`): **移除 JSONL 写入**，改为将 usage JSON 输出到 stderr（标记 `__usage__: true`），由外部 Worker 解析

**当前行为** (v3):
```python
# agent/loop.py — _run_agent_loop 末尾
if accumulated_usage["llm_calls"] > 0:
    usage_record = {
        "__usage__": True,
        "session_key": session_key,
        "model": self.model,
        "prompt_tokens": accumulated_usage["prompt_tokens"],
        "completion_tokens": accumulated_usage["completion_tokens"],
        "total_tokens": accumulated_usage["total_tokens"],
        "llm_calls": accumulated_usage["llm_calls"],
        "started_at": loop_started_at,
        "finished_at": datetime.now().isoformat(),
    }
    print(json.dumps(usage_record), file=sys.stderr)
```

**数据流**:
```
agent loop → stderr JSON → Worker 解析 → SSE done 事件 → Gateway → SQLite analytics.db
```

**与上游的兼容性**: 上游 `main` 分支没有 usage tracking。`local` 分支的改动仅在 `_run_agent_loop` 末尾添加了 stderr 输出，不影响核心逻辑。`session/manager.py` 的 `_type` 过滤已在 v3 中移除。

---

### 3. Max iterations 消息持久化 (`dae3b53`)

**文件**: `nanobot/agent/loop.py`

**问题**: `_run_agent_loop` 在达到 `max_iterations` 时设置了 `final_content` 文本，但未将其作为 assistant 消息添加到 messages 列表。导致 `_save_turn` 不会保存到 JSONL，Web UI 从 JSONL 重载时看不到。

**改动**: 在设置 `final_content` 后，调用 `context.add_assistant_message` 将其追加到 messages 列表。

---

### 4. 防止孤立 tool_result (`c14804d`)

**文件**: `nanobot/session/manager.py`

**问题**: `get_history()` 的 `memory_window` 截断落在长工具调用链中间，导致孤立的 `tool_result` 消息（对应的 `assistant` 消息在窗口之外），触发 Anthropic API 错误 "unexpected tool_use_id found in tool_result blocks"。

**改动**: `get_history()` 对齐逻辑改为优先找 `user` 消息、回退到 `assistant` 消息，永不以 `tool` 消息开头。

---

### 5. exec 工具拒绝后台命令 (`d2a5769`)

**文件**: `nanobot/agent/tools/shell.py`

**问题**: 当 shell 命令包含 `&`（后台操作符）时，子进程继承 PIPE file descriptors。即使主 shell 退出，`communicate()` 仍在等待 PIPE EOF——因为后台进程持有 fd 不释放。导致 exec 工具永远阻塞直到超时。

**根因分析**:
```bash
# Shell 中 & 的优先级低于 &&
cd /dir && python3 server.py &
# 等价于: (cd /dir && python3 server.py) &
# 整个复合命令在后台执行，子 shell 继承 PIPE fd
```

**改动**: 新增 `_has_background_process()` 静态方法:
```python
@staticmethod
def _has_background_process(command: str) -> bool:
    # 1. 去除引号内字符串（避免误判 "echo 'a & b'"）
    stripped = re.sub(r"'[^']*'|\"[^\"]*\"", "", command)
    # 2. 去除合法的 & 模式：&&, >&, &>, 2>&1
    stripped = re.sub(r"&&|[0-9]*>&[0-9]*|&>", "", stripped)
    # 3. 剩余的 & 即为后台操作符
    return "&" in stripped
```

检测到后返回清晰的错误信息，建议使用：
1. `restart-gateway.sh` 等管理脚本（内部使用 `--daemonize`）
2. 程序的 `--daemonize`/`--background` 标志
3. `nohup ... >/dev/null 2>&1 & disown`（单独 exec 调用）
4. 去掉 `&` 直接前台运行

**与 Web Chat 的配合**: web-chat 的 `gateway.py` 和 `worker.py` 新增了 `--daemonize` 标志（double-fork daemon），以及 `restart-gateway.sh` 统一管理脚本。exec 工具可安全调用脚本而不会卡死。

---

### 6. 实时 Session 持久化 (`5528969`)

**文件**: `nanobot/agent/loop.py`, `nanobot/session/manager.py`

**问题**: `_save_turn()` + `sessions.save()` 只在 `_process_message()` 末尾调用。`_run_agent_loop()` 运行期间（可能数分钟的多轮工具调用），所有消息只在内存中。进程异常退出（crash/kill/OOM）= 全部丢失。

**改动**:

1. **SessionManager 新增方法**:
   - `append_message(session, message)`: 追加一条消息到 JSONL 文件（`open("a")` + `flush` + `fsync`），同时更新内存中的 `session.messages`
   - `update_metadata(session)`: 在 turn 结束时重写整个文件更新 metadata（低频调用）
   - `_prepare_entry(message)`: 统一的消息预处理（strip reasoning_content, truncate tool results）
   - `_write_metadata_line(path, session)`: 为新文件写入 metadata 头行

2. **AgentLoop 改动**:
   - `_run_agent_loop()` 新增 `session` 参数
   - User 消息在 `_process_message()` 中构建后立即调用 `append_message`
   - 每条 assistant/tool 消息在 `_run_agent_loop()` 中产生后立即调用 `append_message`
   - `_process_message()` 末尾调用 `update_metadata()` 替代 `_save_turn()` + `save()`
   - `_save_turn()` 标记为 deprecated，不再在主流程中调用

---

## 测试验证

所有改动均通过以下方式验证：
- nanobot agent CLI 手动测试
- Web Chat UI 端到端测试
- 相关 session JSONL 检查

---

## 相关项目

- **Web Chat UI**: `~/.nanobot/workspace/web-chat/` — 前端 + gateway + worker
  - 文档: `docs/REQUIREMENTS.md`, `docs/ARCHITECTURE.md`, `docs/DEVLOG.md`
- **Analytics DB**: `~/.nanobot/workspace/analytics.db` — Token 用量 SQLite 数据库

---

### 7. 统一 Token 记录 (`863b9f0`)

**文件**: `nanobot/usage/__init__.py` (新), `nanobot/usage/recorder.py` (新), `nanobot/agent/loop.py`, `nanobot/cli/commands.py`

**问题**: Token usage 记录分散在各调用方式中：
- CLI/IM/Cron: agent loop 输出 stderr JSON → 丢弃（无持久化）
- Web: agent loop → stderr → Worker 解析 → SSE → Gateway → SQLite

只有 Web 模式有持久化，且链路长、容易丢失（SSE 断连、Worker 崩溃等）。

**改动**:

1. **新增 `usage/recorder.py`**:
   - `UsageRecorder` 类，封装 SQLite 写入
   - 复用 web-chat `analytics.py` 的 schema（共享 `analytics.db`）
   - WAL 模式保证线程安全
   - 支持 `:memory:` 用于单元测试

2. **AgentLoop 集成**:
   - 构造函数新增 `usage_recorder` 参数
   - `_run_agent_loop()` 末尾调用 `recorder.record()`
   - stderr JSON 输出保留（向后兼容 Worker 解析）
   - stderr JSON 新增 `session_key` 字段

3. **CLI commands.py**:
   - `agent`, `gateway`, `cron run` 三个命令都创建 `UsageRecorder()` 并传入 `AgentLoop`

4. **Web-chat Gateway 适配**:
   - `_try_record_usage()` 改为 no-op（核心层已写入，避免重复记录）
   - `/api/usage` 读取路由不变（仍查询同一 SQLite 文件）

**数据流对比**:
```
改造前:
  CLI/IM/Cron → stderr → 丢弃
  Web → stderr → Worker → SSE → Gateway → SQLite

改造后:
  所有模式 → UsageRecorder → SQLite（直接写入）
           → stderr（保留，调试/兼容用）
```

---

### 8. SDK 层 — AgentCallbacks + AgentRunner (`2315216`)

**文件**: `nanobot/agent/callbacks.py` (新), `nanobot/agent/loop.py`, `nanobot/sdk/__init__.py` (新), `nanobot/sdk/runner.py` (新)

**问题**: Web-chat Worker 通过 subprocess 调用 `nanobot agent` CLI 命令执行 agent。这带来多个问题：
- 每次调用都要启动新进程（慢，资源浪费）
- 进度信息通过 stdout 行解析（脆弱）
- Usage 数据通过 stderr JSON 解析（链路长，容易丢失）
- 无法在进程内共享 MCP 连接等资源

**改动**:

1. **`agent/callbacks.py`** — 回调协议:
   - `AgentCallbacks` Protocol: 定义 `on_progress`, `on_message`, `on_usage`, `on_done`, `on_error` 五个异步回调
   - `DefaultCallbacks` 基类: 所有回调的 no-op 默认实现，消费者只需覆盖关心的事件
   - `AgentResult` dataclass: 包含 `content`, `tools_used`, `usage`, `messages`

2. **`agent/loop.py`** — 回调集成:
   - `_run_agent_loop()` 新增 `callbacks: DefaultCallbacks | None` 参数
   - 当 callbacks 提供时，`callbacks.on_progress` 替代 `on_progress` 参数
   - 每条消息持久化后调用 `callbacks.on_message`
   - Usage 记录后调用 `callbacks.on_usage`
   - `_process_message()` 和 `process_direct()` 透传 callbacks
   - `process_direct()` 在完成时调用 `callbacks.on_done`

3. **`sdk/runner.py`** — 高层封装:
   - `AgentRunner.from_config()` 工厂方法: 镜像 CLI commands.py 的 agent 命令初始化逻辑
   - `AgentRunner.run()`: 调用 `process_direct(callbacks=...)`，返回最终文本
   - `AgentRunner.close()`: 释放 MCP 连接等资源
   - 复用 `cli/commands.py` 的 `_make_provider()` 函数，支持所有 provider 类型

4. **Web-chat Worker 改造** (在 web-chat 仓库):
   - 从 subprocess 改为 SDK in-process 调用
   - asyncio event loop 在专用线程中运行
   - AgentRunner 作为单例，复用 MCP 连接
   - WorkerCallbacks 桥接 agent 事件到 SSE 客户端
   - Kill 机制从 `os.kill(pid)` 改为 `future.cancel()`

**向后兼容**: 所有现有调用方（CLI、gateway、IM）不受影响 — callbacks 默认为 None，所有新代码路径都有 `if callbacks` 守卫。

---

### 9. 实时 Token 用量记录 (`17cdef8`)

**文件**: `nanobot/agent/loop.py`, `tests/test_realtime_usage.py` (新)

**问题**: Phase 2 的 UsageRecorder 在 `_run_agent_loop` 末尾一次性写入 SQLite。如果 agent 执行中途异常退出（crash/kill/OOM），`accumulated_usage`（内存字典）全部丢失。与 Phase 1 解决的 session 持久化问题完全类似。

**改动**:

1. **`_run_agent_loop()` — 逐次写入**:
   - 每次 `provider.chat()` 返回后，如果有 `response.usage`，立即调用 `usage_recorder.record()`
   - 每条记录 `llm_calls=1`，时间戳取 `datetime.now().isoformat()`
   - 时间戳与同一次 LLM 调用产生的 assistant 消息的 `timestamp` 一致
   - `accumulated_usage` 内存累加保留（用于 stderr 汇总 + `callbacks.on_usage`）

2. **循环结束后**:
   - 不再调用 `usage_recorder.record()`（已逐次写入）
   - stderr JSON 汇总输出保留（向后兼容 Worker 解析）
   - `callbacks.on_usage` 汇总通知保留

**对聚合查询的影响**: 无。Web-chat 的 UsageIndicator 和 UsagePage 使用 `SUM()` 聚合，多条细粒度记录的 SUM 等于原来一条汇总记录。

**数据流对比**:
```
Phase 2:
  LLM call 1 → 累加到内存
  LLM call 2 → 累加到内存
  循环结束 → 一次性写入 SQLite（1 条记录）
  中途崩溃 → 全部丢失 ❌

Phase 4:
  LLM call 1 → 立即写入 SQLite（1 条记录）
  LLM call 2 → 立即写入 SQLite（1 条记录）
  循环结束 → stderr/callback 输出汇总
  中途崩溃 → 已写入的记录保留 ✅
```

---

## §10 Bug Fix: SessionManager 路径双重嵌套 (commit `aaaf81d`)

**问题**: `AgentRunner.from_config()` 传入 `config.workspace_path / "sessions"` 给 `SessionManager`，但 `SessionManager.__init__` 内部再追加 `/sessions`，导致 SDK 模式下 session 数据写入 `~/.nanobot/workspace/sessions/sessions/`。

**修复**: `sdk/runner.py` 改为 `SessionManager(config.workspace_path)`。

---

## §11 工具调用间隙用户消息注入 (commit `94598cb`)

**改动文件**: `agent/callbacks.py`, `agent/loop.py`

**AgentCallbacks 新增方法**:
```python
async def check_user_input(self) -> str | None:
    """Non-blocking check for pending user injection messages."""
```

**Agent Loop 注入检查点**: 在 `_run_agent_loop` 中，所有工具执行完毕后、下一轮 LLM 调用前：
1. 调用 `callbacks.check_user_input()`
2. 如返回非 None 文本，构造 user 消息 `[User interjection during execution]\n{text}`
3. 追加到 messages 列表 + 实时持久化 JSONL + on_message 回调 + progress 通知

**DefaultCallbacks**: `check_user_input()` 默认返回 None（不影响现有调用方）。

---

## §12 LLM 调用详情日志 (feat/llm-detail-log)

**改动文件**: `usage/detail_logger.py` (新), `usage/__init__.py`, `agent/loop.py`, `sdk/runner.py`, `cli/commands.py`, `tests/test_detail_logger.py` (新)

**问题**: `analytics.db` 只记录 token 数量（prompt_tokens / completion_tokens），无法分析具体的 token 消耗来源（系统 prompt 占比、历史消息占比、工具结果占比等）。

**改动**:

1. **新增 `usage/detail_logger.py`**:
   - `LLMDetailLogger` 类
   - 日志目录: `~/.nanobot/workspace/llm-logs/`
   - 按天分文件: `YYYY-MM-DD.jsonl`
   - `log_call()` 方法: 写入完整 messages + response + 统计字段
   - 返回 `(filename, line_number)` 供关联
   - 支持 `enabled=False` 禁用

2. **`agent/loop.py`** — 集成:
   - `AgentLoop.__init__` 新增 `detail_logger` 参数
   - `_run_agent_loop()` 每次 `provider.chat()` 返回后调用 `detail_logger.log_call()`

3. **`sdk/runner.py` + `cli/commands.py`** — 初始化:
   - 所有入口（agent, gateway, cron-run, SDK）创建 `LLMDetailLogger()` 并传入 `AgentLoop`

**JSONL 记录格式**:
```json
{
  "timestamp": "ISO8601",
  "session_key": "webchat:xxx",
  "model": "claude-opus-4-6",
  "iteration": 1,
  "prompt_tokens": 7085,
  "completion_tokens": 8,
  "total_tokens": 7093,
  "messages_count": 2,
  "system_prompt_chars": 13715,
  "messages": [ ... ],
  "response": { "content": "...", "tool_calls": [...], "finish_reason": "stop", "usage": {...} }
}
```

---

## §13 文件访问审计日志 (feat/audit-log)

**改动文件**: `audit/__init__.py` (新), `audit/logger.py` (新), `agent/tools/registry.py`, `agent/loop.py`, `cli/commands.py`, `sdk/runner.py`, `tests/test_audit.py` (新)

**问题**: nanobot 拥有文件系统完整读写权限，但缺乏统一的审计机制。无法事后追溯 agent 执行了哪些文件操作，无法进行安全分析。

**改动**:

1. **新增 `audit/logger.py`**:
   - `AuditEntry` dataclass: timestamp, session_key, channel, chat_id, tool, action, params, result, resolved_path, error, duration_ms
   - `AuditLogger`: 按天分文件写入 JSONL (`~/.nanobot/workspace/audit-logs/YYYY-MM-DD.jsonl`)
   - 支持 `enabled=False` 禁用

2. **改造 `agent/tools/registry.py`** — ToolRegistry 拦截层:
   - `set_audit_logger()` / `set_audit_context()` 方法
   - `execute()` 中统一拦截所有工具调用（零侵入，不修改具体工具代码）
   - `_extract_audit_fields()` 针对每种工具类型提取结构化审计字段:
     - 文件工具: path, size/bytes_written, success
     - exec: command, working_dir, exit_code, blocked
     - web: query/url, status_code
     - 其他: 参数摘要
   - `_truncate()` 辅助函数: 截断敏感内容用于日志

3. **`agent/loop.py`** — 集成:
   - `AgentLoop.__init__` 新增 `audit_logger` 参数
   - `_set_tool_context()` 扩展 `session_key` 参数，同步调用 `tools.set_audit_context()`

4. **`cli/commands.py` + `sdk/runner.py`** — 初始化:
   - 所有入口（agent, gateway, cron-run, SDK）创建 `AuditLogger()` 并传入 `AgentLoop`

**审计日志格式**:
```json
{
  "timestamp": "2026-02-27T12:30:45.123456",
  "session_key": "webchat:1772126509",
  "channel": "feishu",
  "chat_id": "ou_xxx",
  "tool": "write_file",
  "action": "write",
  "params": {"path": "/path/to/file"},
  "result": {"success": true, "bytes_written": 1234},
  "resolved_path": "/absolute/path/to/file",
  "error": null,
  "duration_ms": 12.34
}
```

**查询示例**:
```bash
grep '"action":"write"' audit-logs/2026-02-27.jsonl | jq .
grep '"session_key":"webchat:123"' audit-logs/2026-02-27.jsonl | jq .
grep '"success":false' audit-logs/2026-02-27.jsonl | jq .
```

---

## §14 Session 自修复 — 未完成 tool_call 链 + 错误消息清理 (feat/session-repair)

**改动文件**: `session/manager.py`, `tests/test_session_repair.py` (新)

**问题**: Agent 在飞书 session 中通过 `exec` 执行 `kill` 杀掉 nanobot gateway 自身进程。由于实时持久化（Phase 1），assistant 的 `tool_calls` 消息已写入 JSONL，但 `tool_result` 永远不会写入（进程已死）。重启后，Anthropic API 严格要求每个 `tool_use` 后面紧跟 `tool_result`，报错 `"tool_use ids were found without tool_result blocks"`。后续用户消息的回复也变成 `"Error calling LLM: ..."` 错误消息，堆积在 session 中，形成恶性循环。

**改动**:

1. **`session/manager.py` — `get_history()` 三阶段清理**:
   - **Phase 1: 开头对齐**（已有，§4）— 跳过开头的孤立 tool 消息
   - **Phase 2: 错误消息剥离**（新增）— 移除 `content.startswith("Error calling LLM:")` 的 assistant 消息
   - **Phase 3: 未完成 tool_call 链移除**（新增）— `_trim_incomplete_tool_tail()` 静态方法

2. **`_trim_incomplete_tool_tail()` 算法**:
   - 正向扫描所有消息
   - 遇到 assistant+tool_calls 时，收集其后的 tool_result ids
   - 如果 expected_ids ≠ actual_ids，移除该 assistant 及其 partial tool results
   - 保留链前后的有效消息（user、普通 assistant 等）
   - 关键区别：**精确移除**而非截断，不会丢失后续有效消息

**与 §4 的区别**:
```
§4（Phase 0）: 处理开头的孤立 tool_result（窗口截断导致）
§14（Phase 8）: 处理任意位置的未完成 tool_call 链（崩溃/自杀导致）+ 错误消息清理
```

**测试**: 9 项测试（test_session_repair.py）:
- 开头对齐回归: 2 项
- 末尾修复: 4 项（完整链保留、全缺失、部分缺失、用户消息保留、多链堆叠）
- 错误剥离: 1 项
- 真实场景模拟: 1 项

---

## §15 大图片自动压缩 (feat/image-compress, commit `2b9c260`)

**改动文件**: `agent/context.py`, `pyproject.toml`, `tests/test_image_compress.py` (新)

**问题**: LLM API 对图片大小有限制（~5MB），用户通过飞书/Telegram/Web 发送的高清照片经常超过此限制，导致 API 拒绝请求。

**改动**:

1. **`agent/context.py` — `_build_user_content()` 增加大小检查**:
   - 读取图片文件后检查 `len(raw) > IMAGE_MAX_BYTES`（5MB）
   - 超过阈值调用 `_compress_image()` 压缩后再 base64 编码
   - 新增 `IMAGE_MAX_BYTES` 类常量（5 * 1024 * 1024）

2. **`agent/context.py` — 新增 `_compress_image()` 静态方法**:
   - Step 1: 最长边超过 `max_dimension`（默认 2048px）时等比缩放
   - Step 2: JPEG quality 从 85 递减至 30（每次 -10），直到文件大小 ≤ target_bytes
   - 格式转换: RGBA/P/LA/PA → RGB（JPEG 不支持透明通道，白色背景填充）
   - 优雅降级: Pillow 未安装时 log warning，原样返回
   - 返回 `(compressed_bytes, "image/jpeg")`

3. **`pyproject.toml` — 新增 Pillow 依赖**:
   - `Pillow>=10.0.0,<12.0.0`

**压缩策略**:
```
原始图片 (>5MB)
  ↓
缩放到 2048px（如果更大）
  ↓
JPEG quality=85 → 检查大小
  ↓ (仍然 >5MB)
quality=75 → 检查大小
  ↓ (仍然 >5MB)
...
quality=30 → 返回（best effort）
```

**测试**: 8 项测试 (test_image_compress.py):
- TestCompressImage: 5 项（小图不压缩、大图压缩、RGBA→RGB、大尺寸缩放、自定义 target_bytes）
- TestBuildUserContent: 3 项（无 media、小图包含、非图片跳过）

---

*本文档随 local 分支改动持续更新。*
