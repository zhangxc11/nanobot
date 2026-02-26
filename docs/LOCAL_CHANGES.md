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

*本文档随 local 分支改动持续更新。*
