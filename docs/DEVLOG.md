# nanobot 核心 — 开发工作日志

> 本文件是开发过程的唯一真相源。每次新 session 从这里恢复上下文。
> 找到 🔜 标记的任务，直接继续执行。

---

## 项目状态总览

| 阶段 | 状态 | 分支 |
|------|------|------|
| 历史改动 (2.1-2.5) | ✅ 已完成 | local |
| Phase 1: 实时 Session 持久化 | ✅ 已完成 | feat/realtime-persist → local |
| Phase 2: 统一 Token 记录 | 🔜 待开始 | feat/unified-usage |
| Phase 3: SDK 化改造 | ⏳ 待 Phase 2 | feat/sdk |

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

- ⏳ **T2.1** 创建 usage/recorder.py
  - UsageRecorder 类，封装 SQLite 操作
  - 复用 web-chat analytics.py 的 schema
  - 线程安全（SQLite WAL 模式）

- ⏳ **T2.2** AgentLoop 集成 UsageRecorder
  - 构造函数接受 usage_recorder 参数
  - _run_agent_loop 末尾调用 recorder.record()
  - 保留 stderr JSON 输出（向后兼容）

- ⏳ **T2.3** CLI commands.py 初始化
  - agent 命令创建 UsageRecorder 并传入 AgentLoop
  - gateway 命令同样

- ⏳ **T2.4** web-chat 适配
  - Gateway 移除 usage 写入逻辑（核心层已写入）
  - Gateway 的 /api/usage 路由不变（仍查询 SQLite）
  - Worker 的 stderr 解析可简化（usage 已由核心层记录）

- ⏳ **T2.5** 测试验证
  - CLI 单次模式：验证 SQLite 有记录
  - CLI 交互模式：验证 SQLite 有记录
  - Web UI：验证 UsageIndicator 正常显示

- ⏳ **T2.6** Git 提交 + 文档更新

---

## Phase 3: SDK 化改造 (Backlog #6)

### 需求来源
- web-chat REQUIREMENTS.md Backlog #6
- nanobot REQUIREMENTS.md §三

### 目标
提供 Python SDK，让 Worker 在进程内直接调用 Agent。

### 任务清单

- ⏳ **T3.1** 定义 AgentCallbacks 协议
  - agent/callbacks.py
  - on_progress, on_message, on_usage, on_done, on_error

- ⏳ **T3.2** _run_agent_loop 接受 callbacks
  - 替换现有的 on_progress 参数
  - 在每个关键节点调用对应回调

- ⏳ **T3.3** 创建 AgentRunner
  - sdk/runner.py
  - from_config() 工厂方法
  - run() 异步执行方法

- ⏳ **T3.4** 改造 web-chat Worker
  - 从 subprocess.Popen 改为 AgentRunner.run()
  - WorkerCallbacks 实现 SSE 通知
  - 需要处理异步事件循环（Worker 当前是线程模型）

- ⏳ **T3.5** 集成测试
  - Web UI 端到端验证
  - 验证进度、usage、消息持久化全链路

- ⏳ **T3.6** Git 提交 + 文档更新

---

*本文件随开发进展持续更新。*
