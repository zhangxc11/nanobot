# nanobot 核心 — 开发工作日志

> 本文件是开发过程的唯一真相源。每次新 session 从这里恢复上下文。
> 找到 🔜 标记的任务，直接继续执行。

---

## 项目状态总览

| 阶段 | 状态 | 分支 |
|------|------|------|
| 历史改动 (2.1-2.5) | ✅ 已完成 | local |
| Phase 1: 实时 Session 持久化 | ✅ 已完成 | feat/realtime-persist → local |
| Phase 2: 统一 Token 记录 | ✅ 已完成 | feat/unified-usage → local |
| Phase 3: SDK 化改造 | ✅ 已完成 | feat/sdk → local |
| Phase 4: 实时 Token 用量记录 | ✅ 已完成 | feat/realtime-usage → local |

---

## 历史改动记录

> 以下改动在创建文档体系之前完成，从 LOCAL_CHANGES.md 迁移。

- ✅ 消息 timestamp 精确化 (commit `81d4947`)
- ✅ Token usage tracking v1-v3 (commits `18f39a7`, `9a10747`, `8f0cc2d`)
- ✅ Max iterations 消息持久化 (commit `dae3b53`)
- ✅ 防止孤立 tool_result (commit `c14804d`)
- ✅ exec 工具拒绝后台命令 (commit `d2a5769`)
- ✅ 文档体系建立: LOCAL_CHANGES.md (commit `e06958f`)

---

## Phase 1: 实时 Session 持久化 (Backlog #7)

### 需求来源
- web-chat REQUIREMENTS.md Backlog #7
- nanobot REQUIREMENTS.md §四

### 目标
每条消息在产生时立即追加到 session JSONL，中途异常退出不丢失已执行的消息。

### 任务清单

- 🔜 **T1.1** SessionManager.append_message() 方法 → ✅ 完成 (commit `5528969`)
  - 追加写入 JSONL（不重写整个文件）
  - 同时更新内存中的 session.messages
  - 文件不存在时先写 metadata 行
  - 包含 fsync 确保写入磁盘

- ⏳ **T1.2** SessionManager.update_metadata() 方法 → ✅ 完成 (commit `5528969`)
  - 只更新 metadata（last_consolidated 等）
  - 在 turn 结束时调用（频率低，可重写整个文件）

- ⏳ **T1.3** _run_agent_loop 注入实时写入 → ✅ 完成 (commit `5528969`)
  - 每条 assistant/tool 消息产生后调用 append_message
  - 需要将 session 引用传入 _run_agent_loop（或通过回调）

- ⏳ **T1.4** _process_message 适配 → ✅ 完成 (commit `5528969`)
  - user 消息在构建后立即追加写入
  - 移除 _save_turn 调用（消息已实时写入）
  - turn 结束后调用 update_metadata

- ⏳ **T1.5** 测试验证 → ✅ 完成
  - 单元测试：6 项全部通过（append_message, truncation, reasoning strip, reload）
  - CLI 简单对话：metadata + user + assistant 正确写入
  - CLI 工具调用：metadata + user + assistant(tool_calls) + tool + assistant(final) 正确写入
  - Web UI 兼容：JSONL 格式不变，Gateway 和 Worker 无需修改

- ⏳ **T1.6** Git 提交 + 文档更新 → ✅ 完成
  - commit `5528969` on feat/realtime-persist, merged to local

---

## Phase 2: 统一 Token 记录 (Backlog #8)

### 需求来源
- web-chat REQUIREMENTS.md Backlog #8
- nanobot REQUIREMENTS.md §五

### 目标
所有调用方式（CLI/Web/IM/Cron）的 token usage 统一写入 SQLite。

### 任务清单

- ⏳ **T2.1** 创建 usage/recorder.py → ✅ 完成 (commit `863b9f0`)
  - UsageRecorder 类，封装 SQLite 操作
  - 复用 web-chat analytics.py 的 schema
  - 线程安全（SQLite WAL 模式）
  - 支持 :memory: 用于测试

- ⏳ **T2.2** AgentLoop 集成 UsageRecorder → ✅ 完成 (commit `863b9f0`)
  - 构造函数接受 usage_recorder 参数
  - _run_agent_loop 末尾调用 recorder.record()
  - 保留 stderr JSON 输出（向后兼容）
  - stderr JSON 新增 session_key 字段

- ⏳ **T2.3** CLI commands.py 初始化 → ✅ 完成 (commit `863b9f0`)
  - agent 命令创建 UsageRecorder 并传入 AgentLoop
  - gateway 命令同样
  - cron-run 命令同样

- ⏳ **T2.4** web-chat 适配 → ✅ 完成
  - Gateway _try_record_usage() 改为 no-op（核心层已写入）
  - Gateway /api/usage 读取路由不变（仍查询同一 SQLite）
  - Worker stderr 解析不变（兼容新增的 session_key 字段）

- ⏳ **T2.5** 测试验证 → ✅ 完成
  - UsageRecorder 单元测试：5 项全部通过（record, global_usage, session_usage, empty session）
  - CLI 简单对话：analytics.db 新增 1 条记录，session_key/model/tokens 正确
  - CLI 工具调用：llm_calls=2，tokens 累加正确
  - stderr JSON 输出包含 session_key，Worker 解析兼容

- ⏳ **T2.6** Git 提交 + 文档更新 → ✅ 完成
  - commit `863b9f0` on feat/unified-usage, merged to local

---

## Phase 3: SDK 化改造 (Backlog #6)

### 需求来源
- web-chat REQUIREMENTS.md Backlog #6
- nanobot REQUIREMENTS.md §三

### 目标
提供 Python SDK，让 Worker 在进程内直接调用 Agent。

### 任务清单

- ✅ **T3.1** 定义 AgentCallbacks 协议 — `agent/callbacks.py`
  - AgentCallbacks Protocol: on_progress, on_message, on_usage, on_done, on_error
  - DefaultCallbacks 基类（no-op 默认实现）
  - AgentResult dataclass

- ✅ **T3.2** _run_agent_loop 接受 callbacks
  - 新增 `callbacks: DefaultCallbacks | None` 参数
  - callbacks.on_progress 替代 on_progress（当提供时）
  - 每条消息持久化后调用 on_message
  - Usage 记录后调用 on_usage
  - process_direct() / _process_message() 透传 callbacks

- ✅ **T3.3** 创建 AgentRunner — `sdk/runner.py`
  - from_config() 工厂方法（复用 CLI _make_provider）
  - run() 调用 process_direct(callbacks=...)
  - close() 释放 MCP 连接

- ✅ **T3.4** 改造 web-chat Worker
  - 从 subprocess.Popen 改为 AgentRunner.run() in-process 调用
  - asyncio event loop 在专用线程中运行
  - AgentRunner 作为单例，复用 MCP 连接
  - WorkerCallbacks 桥接 agent 事件到 SSE 客户端
  - Kill 机制从 os.kill(pid) 改为 future.cancel()

- ✅ **T3.5** 集成测试
  - SDK smoke test: 简单回复 + 工具调用场景
  - Worker health check: `mode: sdk`
  - SSE 流式端点: progress + done + usage
  - Blocking 端点: 正常返回
  - Gateway → Worker 端到端: 正常工作

- ✅ **T3.6** Git 提交 + 文档更新
  - nanobot commit: `2315216` (feat/sdk 分支)
  - LOCAL_CHANGES.md §8 更新
  - DEVLOG.md 更新

### 关键设计决策
1. **callbacks vs on_progress**: 保留 on_progress 向后兼容，callbacks 优先级更高
2. **AgentRunner 复用 CLI _make_provider**: 避免重复实现 provider 创建逻辑
3. **Worker asyncio 线程**: HTTP 仍用 ThreadingMixIn，agent 执行在独立 asyncio loop
4. **AgentRunner 单例**: Worker 启动时初始化一次，所有请求共享

---

## Phase 4: 实时 Token 用量记录

### 需求来源
- nanobot REQUIREMENTS.md §七

### 目标
每次 LLM 调用后立即将 usage 写入 SQLite，中途异常退出不丢失已记录的 usage。

### 任务清单

- ✅ **T4.1** 修改 `_run_agent_loop` — 每次 LLM 调用后立即写入 SQLite (commit `17cdef8`)
  - `provider.chat()` 返回后，如果有 `response.usage`，立即调用 `usage_recorder.record()`
  - 时间戳取 `datetime.now().isoformat()`（与 assistant 消息 timestamp 一致）
  - 每条记录 `llm_calls=1`
  - `accumulated_usage` 内存累加保留（用于 stderr 汇总 + callbacks.on_usage）
  - 循环结束后不再调用 `usage_recorder.record()`（已逐次写入）

- ✅ **T4.2** 测试验证 (commit `17cdef8`)
  - UsageRecorder 单元测试：5 项全部通过（单次记录、多次记录聚合、全局聚合、时间戳独立、空 session）
  - CLI 简单对话：analytics.db 新增 1 条记录（llm_calls=1），正确
  - CLI 工具调用（2 次 LLM）：analytics.db 新增 2 条独立记录，每条 llm_calls=1，时间戳不同
  - 现有测试无回归（20 failed / 63 passed，与改动前一致）

- ✅ **T4.3** Git 提交 + 合并 + 文档更新
  - commit `17cdef8` on feat/realtime-usage, merged to local
  - LOCAL_CHANGES.md §9 更新
  - DEVLOG.md 更新

---

## Bug Fix: SessionManager 路径双重嵌套 (2026-02-26)

### 问题
- **现象**: web-chat 发送消息后能执行，但 session 不记录消息，刷新/切换后消息消失
- **根因**: `AgentRunner.from_config()` 传入 `config.workspace_path / "sessions"` 给 `SessionManager`，但 `SessionManager.__init__` 内部又追加 `/sessions`，导致实际写入路径变成 `~/.nanobot/workspace/sessions/sessions/`（双重嵌套）
- **影响**: 所有通过 SDK（web-chat worker）执行的 session 数据写入了错误目录，gateway 读取正确目录时找不到消息

### 修复
- `sdk/runner.py`: `SessionManager(config.workspace_path)` 改为传入 workspace root，而非 sessions_dir
- 恢复 `sessions/sessions/` 下的误写数据到正确位置
- 清理错误的嵌套目录

### 验证
- Worker 重启后发送测试消息，确认 JSONL 写入 `~/.nanobot/workspace/sessions/`（正确）
- `sessions/sessions/` 不再被创建

### Commit
- `aaaf81d` on local

---

*本文件随开发进展持续更新。*
