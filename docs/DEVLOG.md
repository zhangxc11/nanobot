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
| Phase 5: 工具调用间隙用户注入 | ✅ 已完成 | feat/user-inject → local |
| Phase 6: LLM 调用详情日志 | ✅ 已完成 | feat/llm-detail-log → local |
| Phase 7: 文件访问审计日志 | ✅ 已完成 | feat/audit-log → local |
| Phase 8: Session 自修复 | ✅ 已完成 | feat/session-repair → local |
| Phase 9: 多飞书租户支持 | ✅ 已完成 | feat/multi-feishu → local |
| Phase 10: media 参数支持 | ✅ 已完成 | feat/image-media → local |
| Phase 11: LLM API 重试机制 | ✅ 已完成 | feat/llm-retry → local |

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

## Phase 5: 工具调用间隙用户消息注入 (2026-02-26)

### 需求来源
- web-chat REQUIREMENTS.md Backlog #10 / Issue #25

### 目标
在 agent 执行工具调用循环过程中，用户可在工具调用间隙输入补充信息，作为 user 消息注入到 LLM 消息列表，影响后续决策。

### 任务清单

- ✅ **T5.1** callbacks.py: 新增 `check_user_input() -> str | None`
  - AgentCallbacks Protocol 定义
  - DefaultCallbacks 默认返回 None
  - 非阻塞调用，在工具调用间隙检查

- ✅ **T5.2** loop.py: agent loop 集成注入检查点
  - 在所有工具执行完毕后、下一轮 LLM 调用前调用 `callbacks.check_user_input()`
  - 有注入文本时构造 `[User interjection during execution]` user 消息
  - 消息实时持久化到 JSONL + on_message 回调 + progress 通知

### Git
- commit `94598cb` on feat/user-inject → merged to local

---

## Phase 6: LLM 调用详情日志 (web-chat Backlog #15) ✅

### 需求来源
- web-chat REQUIREMENTS.md Backlog #15
- nanobot REQUIREMENTS.md §八

### 目标
每次 LLM 调用时，将完整的 messages（prompt）和 response 记录到 JSONL 日志文件，供后续分析 token 消耗优化。

### 任务清单

- 🔜 **T6.1** 创建 `usage/detail_logger.py` 模块 → ✅ 完成
  - `LLMDetailLogger` 类
  - 日志目录: `~/.nanobot/workspace/llm-logs/`
  - 按天分文件: `YYYY-MM-DD.jsonl`
  - `log_call()` 方法: 写入 messages + response + 统计字段
  - 返回 (file_name, line_number) 供 SQLite 关联
  - 支持 enabled=False 禁用

- ⏳ **T6.2** `agent/loop.py` 集成 → ✅ 完成
  - `AgentLoop.__init__` 接受 `detail_logger: LLMDetailLogger | None` 参数
  - `_run_agent_loop` 中每次 `provider.chat()` 返回后调用 `detail_logger.log_call()`
  - 传入: messages, response (content + tool_calls + finish_reason + usage), session_key, model, iteration

- ⏳ **T6.3** `sdk/runner.py` + `cli/commands.py` 初始化 → ✅ 完成
  - `AgentRunner.from_config()` 创建 LLMDetailLogger 并传入 AgentLoop
  - CLI agent/gateway/cron-run 命令同样创建并传入
  - 默认开启，无需配置

- ⏳ **T6.4** 测试验证 → ✅ 完成
  - 单元测试: 6 项全部通过 (test_detail_logger.py)
  - SDK 端到端: 确认 JSONL 文件生成，内容完整
  - 记录包含: system_prompt_chars=13715, messages_count=2, 完整 response
  - 现有 usage 测试无回归 (11 passed)

- ⏳ **T6.5** Git 提交 + 文档更新 → ✅ 完成
  - commit `5ab4ce8` on feat/llm-detail-log, merged to local
  - LOCAL_CHANGES.md §12
  - DEVLOG.md 更新

---

## Phase 7: 文件访问审计日志 (2026-02-27)

### 需求来源
- nanobot REQUIREMENTS.md §九
- 用户安全需求：对所有文件读写操作进行审计记录

### 目标
在 ToolRegistry 层统一拦截所有工具调用，记录审计日志到 JSONL 文件，供事后安全分析。

### 设计要点
- **拦截层**: ToolRegistry.execute() 统一拦截，零侵入（不修改具体工具代码）
- **存储**: `~/.nanobot/workspace/audit-logs/YYYY-MM-DD.jsonl`
- **上下文**: 通过 set_audit_context() 传递 session_key/channel/chat_id
- **字段提取**: 针对不同工具类型提取有意义的审计字段

### 任务清单

- 🔜 **T7.1** 创建 `audit/logger.py` — AuditLogger + AuditEntry → ✅ 完成 (commit `3eb2786`)

- ✅ **T7.2** 改造 `tools/registry.py` — 审计拦截 (commit `3eb2786`)
  - set_audit_logger() / set_audit_context() 方法
  - execute() 中统一拦截所有工具调用
  - _extract_audit_fields() 针对每种工具类型提取审计字段
  - _build_audit_entry() 构建 AuditEntry

- ✅ **T7.3** `agent/loop.py` — 审计上下文设置 (commit `3eb2786`)
  - AgentLoop.__init__ 接受 audit_logger 参数，传入 ToolRegistry
  - _set_tool_context() 扩展 session_key 参数，同步设置审计上下文

- ✅ **T7.4** `cli/commands.py` + `sdk/runner.py` — 初始化 AuditLogger (commit `3eb2786`)
  - agent, gateway, cron-run 命令创建 AuditLogger
  - AgentRunner.from_config() 创建 AuditLogger

- ✅ **T7.5** 测试验证 (commit `3eb2786`)
  - AuditLogger 单元测试: 5 项通过（创建文件、追加、禁用、多日期、嵌套目录）
  - 字段提取测试: 15 项通过（read_file, write_file, edit_file, list_dir, exec, web_search, web_fetch, spawn, cron, message, mcp, unknown）
  - 截断辅助函数测试: 3 项通过
  - ToolRegistry 集成测试: 5 项通过（上下文设置、带审计执行、无审计执行、错误审计、工具不存在审计）
  - 共 28 项测试全部通过，现有测试无回归（97 passed / 20 failed 与改动前一致）

- ✅ **T7.6** Git 提交 + 文档更新
  - commit `3eb2786` on feat/audit-log, merged to local

---

## Phase 8: Session 自修复 — 未完成 tool_call 链 + 错误消息清理 (2026-02-27)

### 需求来源
- 飞书 session 崩溃事故：agent 在飞书 session 中执行 `kill` 杀掉 nanobot gateway 自身进程
- tool_call 已写入 JSONL（实时持久化），但 tool_result 永远不会写入
- 重启后 Anthropic API 报错 "tool_use ids were found without tool_result blocks"
- 后续用户消息的回复也变成 error 消息，堆积在 session 中

### 问题分析
`get_history()` 原有的防护只处理了**开头的孤立 tool_result**（§4，Phase 0），没有处理：
1. **末尾/中间的未完成 tool_call 链**：assistant 有 tool_calls 但缺少对应 tool_result
2. **错误消息堆积**：`"Error calling LLM: ..."` 类型的 assistant 消息是诊断产物，不应送入 LLM

### 设计要点
`get_history()` 改为三阶段清理：
1. **Phase 1: 开头对齐**（已有） — 跳过开头的孤立 tool 消息
2. **Phase 2: 错误消息剥离**（新增） — 移除 `"Error calling LLM:"` 开头的 assistant 消息
3. **Phase 3: 未完成 tool_call 链移除**（新增） — 正向扫描，移除 assistant+tool_calls 中缺少完整 tool_result 的链，保留链前后的有效消息

### 关键设计决策
- **移除而非截断**：不完整的 tool_call 链被精确移除（assistant + partial results），后续的 user 消息被保留
- **正向扫描**：从头到尾扫描所有 assistant+tool_calls，不仅是末尾的
- **幂等安全**：多次调用结果一致

### 任务清单

- ✅ **T8.1** `session/manager.py` — get_history() 三阶段清理
  - Phase 2: 错误消息剥离
  - Phase 3: _trim_incomplete_tool_tail() 方法
  - 正向扫描 + 精确移除（保留后续有效消息）

- ✅ **T8.2** 测试验证 — test_session_repair.py
  - 9 项测试全部通过：
    - 开头对齐: 2 项（已有行为回归）
    - 末尾修复: 4 项（完整链保留、全缺失、部分缺失、用户消息保留、多链堆叠）
    - 错误剥离: 1 项
    - 真实场景: 1 项（模拟飞书崩溃全流程）
  - 现有测试无回归（106 passed / 20 failed 与改动前一致）

- ✅ **T8.3** 修复损坏的飞书 session 文件
  - 备份原文件为 .bak
  - 移除 3 条问题消息：1 条未完成 tool_call + 2 条 error artefact
  - 验证修复后文件无问题

- ✅ **T8.4** Git 提交 + 文档更新

---

## Phase 9: 多飞书租户支持 (2026-02-27)

### 需求来源
- nanobot REQUIREMENTS.md §十
- 用户有多个飞书租户，需要 gateway 同时接入多个飞书机器人

### 目标
config.json 支持飞书多租户配置（数组形式），ChannelManager 为每个租户创建独立的 FeishuChannel 实例，session 互不干扰。

### 设计要点
- **Config**: `feishu` 字段支持 `FeishuConfig | list[FeishuConfig]`，向后兼容
- **FeishuConfig 新增 `name` 字段**: 用于区分不同租户
- **Channel name**: 单租户 `feishu`，多租户 `feishu.{name}`
- **Session key**: 单租户 `feishu:{chat_id}`，多租户 `feishu.{name}:{chat_id}`
- **Outbound 路由**: InboundMessage.channel 设为实例 key，精确路由回复

### 任务清单

- 🔜 **T9.1** `config/schema.py` — FeishuConfig 新增 name + ChannelsConfig 类型变更 → ✅ 完成 (commit `34cec58`)

- ✅ **T9.2** `channels/feishu.py` — FeishuChannel 支持自定义 channel name (commit `34cec58`)

- ✅ **T9.3** `channels/manager.py` — _init_channels() 支持飞书多实例 (commit `34cec58`)

- ✅ **T9.4** 测试验证 (commit `34cec58`)
  - Config 解析: 单对象 / 列表 / 默认值 / 完整 config.json 加载 — 全部通过
  - FeishuChannel name: 无 name → "feishu"，有 name → "feishu.{name}" — 通过
  - Session key: "feishu.personal:ou_123" 正确生成 — 通过
  - ChannelManager 多实例: 创建 / 部分禁用 / 全部禁用 — 全部通过
  - Outbound 路由: 精确匹配 channel key — 通过
  - 现有测试无回归: 106 passed / 20 failed（与改动前一致）

- ✅ **T9.5** Git 提交 + 合并 + 文档更新
  - commit `34cec58` on feat/multi-feishu, merged to local

---

## Phase 10: media 参数支持 (2026-02-27) ✅

### 需求来源
- nanobot REQUIREMENTS.md §十一
- web-chat Phase 32 图片输入功能的核心层依赖

### 目标
为 `process_direct()` 和 `AgentRunner.run()` 增加 `media` 参数，支持传入图片附件。

### 任务清单

- ✅ **T10.1** `agent/loop.py` — `process_direct()` 新增 `media` 参数
  - 传入 `InboundMessage(media=media or [])`
  - 已有的 `_build_user_content()` 自动处理 base64 编码

- ✅ **T10.2** `sdk/runner.py` — `AgentRunner.run()` 新增 `media` 参数
  - 透传给 `process_direct(media=media)`

- ✅ **T10.3** 端到端验证
  - web-chat 上传蓝色 PNG → 发送 "什么颜色" → Claude 回复 "蓝色" ✅

### Git
- commit `684fc1b` on feat/image-media → merged to local

---

## Phase 11: LLM API 速率限制重试机制 ✅

### 需求来源
- nanobot REQUIREMENTS.md §十二
- 用户报告 Anthropic API 偶发 RateLimitError 导致任务中断

### 目标
在 `_run_agent_loop` 中为 `provider.chat()` 调用增加指数退避重试，自动处理暂时性错误。

### 任务清单

- ✅ **T11.1** `agent/loop.py` — 新增 `_is_retryable()` 静态方法
  - 判断异常是否为暂时性可重试错误
  - 支持: RateLimitError, APIConnectionError, APITimeoutError, HTTP 429/5xx
  - 字符串回退: "rate limit", "overloaded", "capacity"

- ✅ **T11.2** `agent/loop.py` — 新增 `_chat_with_retry()` 异步方法
  - 包裹 `provider.chat()` 调用
  - 指数退避: 10s → 20s → 40s → 80s → 160s
  - 最多重试 5 次
  - 重试时通过 progress_fn 通知用户（best-effort，不影响重试逻辑）
  - 重试时记录 warning 日志

- ✅ **T11.3** `_run_agent_loop` 中替换 `provider.chat()` 为 `_chat_with_retry()`

- ✅ **T11.4** 单元测试 — 26 项全部通过
  - `_is_retryable()`: 19 项（6 类名匹配 + 5 状态码 + 3 消息匹配 + 5 非重试）
  - `_chat_with_retry()`: 7 项（首次成功、重试成功、指数退避、超限失败、非重试即抛、进度通知、进度错误不影响重试）
  - 现有测试无回归: 132 passed / 20 failed（与改动前一致）

- ✅ **T11.5** Git 提交 + 合并 + 文档更新
  - commit `777c2d5` on feat/llm-retry → merged to local

---

## Phase 12: /new 命令重构 — 新建 Session ✅

### 需求来源
- nanobot REQUIREMENTS.md §十三
- 用户希望 `/new` 语义更直观：创建全新 session，而非归档+清空

### 目标
- `/new` → `/flush`：归档当前 session 记忆（原 `/new` 行为）
- 新 `/new`：创建新 session，后续对话不带之前的记录

### 任务清单

- ✅ **T12.1** `agent/loop.py` — 将原 `/new` 逻辑改为 `/flush`
  - 命令名从 `/new` 改为 `/flush`
  - 更新 `/help` 输出

- ✅ **T12.2** `session/manager.py` — 新增 `create_new_session()` 和路由映射
  - `create_new_session(channel, chat_id, old_key)`: 归档旧 session 文件（加时间戳后缀），创建新空 session
  - `resolve_session_key(natural_key)`: 通过路由表解析实际 session key
  - 路由映射持久化到 `sessions/_routing.json`

- ✅ **T12.3** `agent/loop.py` — 新增 `/new` 处理逻辑
  - 调用 `sessions.create_new_session()` 归档旧文件 + 创建新 session
  - `_process_message` 中通过 `resolve_session_key()` 解析路由
  - Gateway/CLI 通道统一处理

- ✅ **T12.4** web-chat 前端 — `/new` 改为前端拦截
  - `/new`: 调用 `createSession()` API + 切换到新 session（纯前端）
  - `/flush`: 发送到后端（原 `/new` 的归档行为）
  - 更新 HELP_TEXT

- ✅ **T12.5** 测试验证 + Git 提交
  - 11 项新测试全部通过（resolve_session_key、create_new_session、routing persistence）
  - 前端 build 成功
  - nanobot commit `d26b27e` on feat/new-session → merged to local
  - web-chat commit `8155561` on main

### Git
- nanobot: commit `d26b27e` on feat/new-session → merged to local
- web-chat: commit `8155561` on main

---

*本文件随开发进展持续更新。*
