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
| Phase 12: /new 命令重构 | ✅ 已完成 | feat/new-session → local |
| Phase 13: /stop 命令 | ✅ 已完成 | feat/stop-command → local |
| Phase 14: 大图片自动压缩 | ✅ 已完成 | feat/image-compress → local |
| Phase 15: 图片存储架构改进 | ✅ 已完成 | feat/image-storage → local |
| Phase 16: ProviderPool 动态切换 | ✅ 已完成 | feat/provider-pool → local |
| Phase 17: 飞书合并转发消息解析 | ✅ 已完成 | feat/merge-forward → local |
| Phase 18: 飞书通道文件附件发送修复 | ✅ 已完成 | local |
| Phase 19: Gateway 并发执行 + Per-Session Provider | ✅ 已完成 | feat/concurrent-gateway → local |
| Phase 20: /session 状态查询命令 | ✅ 已完成 | local |
| Phase 21: /new 归档方向反转 + Session 命名简化 | ✅ 已完成 | local |
| Phase 22: Merge main → local | ✅ 已完成 | local |
| Phase 23: LLM 错误响应持久化与前端展示 | ✅ 已完成 | local |
| Phase 25: 迭代预算软限制 + exec 动态超时 | ✅ 已完成 | local |
| Phase 26: spawn subagent 能力增强 | ✅ 已完成 | local |
| Phase 27: ProviderPool **kwargs 透传 + 接口一致性防护 | ✅ 已完成 | local |
| Phase 28: 弱网 LLM API 稳定性增强 | ✅ 已完成 | feat/weak-network-resilience → local |
| Phase 29: SpawnTool session_key 传递修复 | ✅ 已完成 | local |
| Phase 30: Session 间消息传递机制 (SessionMessenger) | ✅ 已完成 | local |

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

## Phase 13: /stop 命令 — 取消运行中的任务 ✅

### 需求来源
- 用户请求：飞书端需要 `/stop` 功能取消正在执行的长任务

### 目标
在 gateway channel（飞书/Telegram 等）中支持 `/stop` 命令，取消当前正在执行的 agent 任务。

### 设计要点

**核心挑战**: `AgentLoop.run()` 原本是顺序处理消息的——消费一条、处理完、再消费下一条。当 agent 正在处理长任务时，`/stop` 命令会排在队列中等待，无法及时响应。

**解决方案**: 将 `_process_message()` 包装为 `asyncio.Task`，在任务运行期间继续监听队列中的 `/stop` 命令：

```
run() 主循环:
  1. 消费消息
  2. 如果是 /stop → _handle_stop() 取消 active task
  3. 否则 → 创建 asyncio.Task 执行 _process_message_safe()
  4. _wait_with_stop_listener(): 等待 task 完成，同时监听 /stop
     - /stop → 取消 task
     - 其他消息 → 放回队列（下次处理）
```

**关键实现**:
- `_active_task`: 当前运行的 asyncio.Task
- `_active_task_msg`: 当前正在处理的 InboundMessage（用于匹配 channel+chat_id）
- `_handle_stop()`: 匹配 channel+chat_id 后取消任务
- `_process_message_safe()`: 捕获 CancelledError，发送 "⏹ Task stopped." 友好提示
- `/stop` 只取消**同一 chat** 的任务，不同 chat 的 `/stop` 返回 "No active task"

### 任务清单

- ✅ **T13.1** `agent/loop.py` — run() 改造为 Task-based 并发处理
  - `_active_task` / `_active_task_msg` / `_active_task_session_key` 追踪
  - run() 中 `/stop` 直接拦截，不进入 _process_message
  - `_handle_stop()`: 匹配 channel+chat_id，取消 active task
  - `_wait_with_stop_listener()`: 等待 task + 监听 /stop，其他消息放回队列
  - `_process_message_safe()`: 包装 _process_message，捕获 CancelledError

- ✅ **T13.2** `agent/loop.py` — /help 和 _process_message 更新
  - /help 输出增加 `/stop — Stop the currently running task`
  - _process_message 中 `/stop` fallback（process_direct 调用时返回 "No active task"）

- ✅ **T13.3** `channels/telegram.py` — Telegram 适配
  - BOT_COMMANDS 增加 `/stop`
  - CommandHandler("stop") 注册到 _forward_command
  - _on_help 输出增加 `/stop`

- ✅ **T13.4** 测试验证 — 9 项全部通过
  - TestStopCommandDirect: /stop 直接调用返回 "No active task"（2 项）
  - TestHelpIncludesStop: /help 包含 /stop（1 项）
  - TestStopCancelsTask: _handle_stop 无任务/匹配取消/不同 chat 忽略/CancelledError 捕获（4 项）
  - TestActiveTaskTracking: 处理中 _active_task 已设置（1 项）
  - TestStopEndToEnd: run() 循环中 /stop 取消长任务（1 项）
  - 现有测试无回归: 108 passed（99 + 9 新）

- ✅ **T13.5** Git 提交 + 合并 + Gateway 重启
  - commit `cbed25b` on feat/stop-command → merged to local
  - Gateway 已重启，飞书 WebSocket 已连接

### 影响范围

| 文件 | 改动 |
|------|------|
| `agent/loop.py` | run() 改造 + 新增 _handle_stop/_wait_with_stop_listener/_process_message_safe + /help 更新 |
| `channels/telegram.py` | BOT_COMMANDS + CommandHandler + help 文本 |
| `tests/test_stop_command.py` | 9 项新测试 |

### 注意事项
- **飞书 channel 无需改动**: 飞书用户发送 `/stop` 文本消息，由 FeishuChannel._on_message → bus → AgentLoop.run() 处理
- **Web-chat 已有 kill API**: web-chat 的 `/stop` 按钮通过 worker kill API 实现，不受此改动影响
- **CLI 交互模式**: `/stop` 在 CLI 中也可用（通过 run() 循环），但 CLI 用户更习惯 Ctrl+C

---

## Phase 14: 大图片自动压缩 (2026-02-27) ✅

### 需求来源
- nanobot REQUIREMENTS.md §十四
- 用户报告：飞书/Web 端发送大图片（>5MB）时 LLM API 拒绝

### 目标
图片超过 5MB 时自动压缩（缩小尺寸 + 降低 JPEG 质量），确保不超过 LLM API 限制。

### 设计要点
- **统一入口**: `ContextBuilder._build_user_content()` 中检查文件大小
- **压缩策略**: 最长边缩至 2048px + JPEG quality 从 85 递减至 30
- **格式处理**: RGBA/P/LA → RGB 转换（JPEG 不支持透明通道）
- **优雅降级**: Pillow 未安装时 log warning，原样发送
- **新增依赖**: Pillow>=10.0.0,<12.0.0

### 任务清单

- ✅ **T14.1** `agent/context.py` — `_build_user_content()` 增加大小检查
  - 读取文件后检查 `len(raw) > IMAGE_MAX_BYTES`
  - 超过阈值调用 `_compress_image()` 压缩

- ✅ **T14.2** `agent/context.py` — 新增 `_compress_image()` 静态方法
  - Step 1: 缩小尺寸（最长边 > max_dimension 时等比缩放）
  - Step 2: JPEG quality 递减编码（85 → 75 → ... → 30）
  - 返回 `(compressed_bytes, "image/jpeg")`
  - Pillow 未安装时 graceful fallback

- ✅ **T14.3** `pyproject.toml` — 新增 Pillow 依赖

- ✅ **T14.4** 测试验证 — 8 项全部通过
  - TestCompressImage: 5 项（小图不压缩、大图压缩、RGBA→RGB、大尺寸缩放、自定义 target_bytes）
  - TestBuildUserContent: 3 项（无 media、小图包含、非图片跳过）
  - 现有 context 测试无回归

- ✅ **T14.5** Git 提交 + 合并 + 文档更新
  - commit `2b9c260` on feat/image-compress → merged to local

### 影响范围

| 文件 | 改动 |
|------|------|
| `agent/context.py` | `_build_user_content()` 大小检查 + `_compress_image()` 静态方法 + import io/logger |
| `pyproject.toml` | 新增 `Pillow>=10.0.0,<12.0.0` |
| `tests/test_image_compress.py` | 8 项新测试 |

---

## Phase 15: 图片存储架构改进 (2026-02-27) ✅

### 需求来源
- nanobot REQUIREMENTS.md §七A
- 用户报告：飞书图片不在 workspace 下载目录中 + session JSONL 因 base64 膨胀

### 目标
1. 统一所有通道的媒体文件存储路径到 `workspace/uploads/<date>/`
2. Session JSONL 中用文件路径引用替代 base64，读取时按需还原

### 设计要点

**统一存储路径**：
- 飞书/Telegram/Discord 的 `media_dir` 从 `~/.nanobot/media/` 改为 `~/.nanobot/workspace/uploads/<date>/`
- 文件命名保持各通道原有逻辑（image_key、file_id 等）

**Session JSONL 去 base64**：
- `SessionManager._prepare_entry()` 中检测 `data:` base64 图片 URL
- 将 base64 解码后保存为文件，URL 替换为 `file:///path`（带 MIME 元数据）
- `Session.get_history()` 中检测 `file:///` URL，读取文件还原为 base64
- 向后兼容：旧 session 中已有的 `data:` base64 仍可正常加载和使用

**文件引用格式**：
```
file:///absolute/path/to/image.jpg?mime=image/jpeg
```

### 任务清单

- ✅ **T15.1** 统一媒体存储路径 — 修改三个通道 (commit `11b1298`)
  - `channels/feishu.py` — `_download_and_save_media()` 中 media_dir 改为 workspace/uploads/<date>/
  - `channels/telegram.py` — 媒体下载路径同步修改
  - `channels/discord.py` — 媒体下载路径同步修改

- ✅ **T15.2** `session/manager.py` — _prepare_entry() 增加 base64 提取与文件保存 (commit `11b1298`)
  - `_extract_and_save_images()`: 检测 content 为 list 且包含 `type: "image_url"` 的 item
  - `_save_base64_image()`: 对 `data:mime;base64,...` URL 解码 → 保存文件 → 替换为 `file:///` 引用
  - 文件保存到 `workspace/uploads/<date>/<hash>.<ext>`
  - 文件名用内容 hash（MD5 前 12 位）避免重复

- ✅ **T15.3** `session/manager.py` — get_history() 增加文件引用还原 (commit `11b1298`)
  - `_restore_image_refs()`: 检测 `file:///` URL → 读取文件 → base64 编码 → 还原为 `data:mime;base64,...`
  - `_load_file_as_data_url()`: 文件不存在时 graceful degradation（log warning，移除该图片 item）

- ✅ **T15.4** 测试验证 (commit `11b1298`)
  - 24 项测试全部通过:
    - _save_base64_image: 5 项（JPEG/PNG 保存、去重、无效 URL、目录创建）
    - _extract_and_save_images: 5 项（字符串不变、None 不变、提取 base64、file ref 透传、多图片）
    - _restore_image_refs: 5 项（字符串不变、还原 file ref、丢弃缺失文件、data URL 透传、混合 ref）
    - _prepare_entry 集成: 4 项（图片消息、文本消息、assistant 消息、tool 截断）
    - get_history 集成: 3 项（还原 file ref、向后兼容 data URL、缺失文件优雅处理）
    - 完整 round-trip: 2 项（保存→加载→还原、JSONL 大小验证）
  - 现有测试无回归: 184 passed / 20 failed（与改动前一致）

- ✅ **T15.5** Git 提交 + 合并 + 文档更新
  - commit `11b1298` on feat/image-storage → merged to local

### 影响范围

| 文件 | 改动 |
|------|------|
| `channels/feishu.py` | media_dir 路径改为 workspace/uploads/<date>/ |
| `channels/telegram.py` | media_dir 路径改为 workspace/uploads/<date>/ |
| `channels/discord.py` | media_dir 路径改为 workspace/uploads/<date>/ |
| `session/manager.py` | 新增 4 个模块级函数 + _prepare_entry 集成 + get_history 集成 |
| `tests/test_image_storage.py` | 24 项新测试 |

---

## Phase 16: ProviderPool — 运行时 Provider 动态切换 ✅

### 需求来源
- 用户需求：agent token 消耗量大，需要根据任务难度切换不同 API 源控制成本
- 不同 channel（webchat、gateway、命令行）独立维护 provider 状态
- 不修改 config.json 来切换，纯运行时状态

### 目标
1. 新增 `anthropic_proxy` provider 配置槽位
2. 引入 ProviderPool 类，实现 LLMProvider 接口，支持运行时切换 active provider + model
3. 新增 `/provider` 斜杠命令（全 channel 可用）
4. 任务执行中禁止切换

### 任务清单

- ✅ **T16.1** `providers/registry.py` — 新增 `anthropic_proxy` ProviderSpec
- ✅ **T16.2** `config/schema.py` — `ProvidersConfig` 增加 `anthropic_proxy` 字段
- ✅ **T16.3** `providers/pool.py` — **新建** ProviderPool 类（LLMProvider 接口 + 运行时切换）
- ✅ **T16.4** `providers/__init__.py` — 导出 ProviderPool
- ✅ **T16.5** `cli/commands.py` — `_make_provider` 改为构建 ProviderPool
- ✅ **T16.6** `agent/loop.py` — 新增 `/provider` 斜杠命令 + `/help` 更新
- ✅ **T16.7** 测试验证 — 22 项 ProviderPool 测试 + 5 项命令测试全部通过，无回归
- ✅ **T16.8** Git 提交
  - commit `e31c837` on feat/provider-pool → merged to local

### 影响范围

| 文件 | 改动 |
|------|------|
| `providers/registry.py` | 新增 `anthropic_proxy` ProviderSpec |
| `config/schema.py` | `ProvidersConfig` 增加 `anthropic_proxy` 字段 |
| `providers/pool.py` | **新建** ProviderPool 类（LLMProvider 接口代理 + 运行时切换） |
| `providers/__init__.py` | 导出 ProviderPool |
| `cli/commands.py` | `_make_provider` 改为构建 ProviderPool + PROVIDER_DEFAULT_MODELS |
| `agent/loop.py` | `/provider` 斜杠命令 + `/help` 更新 |
| `tests/test_provider_pool.py` | 22 项新测试 |

---

## Phase 17: 飞书合并转发消息（merge_forward）解析 ✅

### 需求来源
- nanobot REQUIREMENTS.md Backlog: 飞书合并转发消息解析
- 用户在飞书中将聊天记录通过「合并转发」发送给 nanobot 时，当前只显示 `[merged forward messages]` 占位文本

### 目标
解析 `merge_forward` 消息的子消息 ID 列表，调用飞书 API 逐条获取原始消息内容，拼接为可读文本格式传给 Agent。

### 技术方案
1. **merge_forward content 结构**: content JSON 包含 `message_id_list`（子消息 ID 数组）
2. **API 调用**: 使用 `lark_oapi` SDK 的 `GetMessageRequest` 获取单条消息详情
3. **子消息解析**: 支持 text / post / image / file / interactive / system 等多种类型
4. **格式化输出**: 将子消息拼接为 `--- forwarded messages ---` 包裹的格式
5. **错误处理**: 权限不足/消息不存在时 graceful degradation
6. **嵌套处理**: 嵌套 merge_forward 不递归，标记为 `[nested merged forward messages]`

### 任务清单

- ✅ **T17.1** `channels/feishu.py` — 新增 `_get_message_detail_sync()` 方法
  - 使用 `GetMessageRequest` 获取单条消息详情
  - 返回 dict(msg_type, content, sender_id, create_time, message_id) 或 None
  - 同步方法（在 executor 中调用）

- ✅ **T17.2** `channels/feishu.py` — 新增 `_resolve_merge_forward()` 异步方法
  - 解析 merge_forward 的 content JSON，提取 `message_id_list`
  - 逐条调用 `_get_message_detail_sync()` 获取子消息
  - 根据子消息 msg_type 提取文本内容（复用已有的 `_extract_post_content`, `_extract_share_card_content` 等）
  - 图片/文件类型调用 `_download_and_save_media()` 下载
  - 拼接为 `--- forwarded messages ---` 包裹格式返回

- ✅ **T17.3** `channels/feishu.py` — `_on_message()` 中 merge_forward 分支改为调用 `_resolve_merge_forward()`
  - 从 `share_chat/share_user/interactive/...` 联合分支中拆出 merge_forward
  - 单独处理，支持返回 media_paths

- ✅ **T17.4** 测试验证 — 18 项全部通过
  - _get_message_detail_sync: 7 项（成功、API失败、空items、None items、异常、无效JSON、无sender）
  - _resolve_merge_forward: 10 项（文本消息、空列表、无列表、API失败优雅降级、混合类型、图片子消息、嵌套转发、富文本、全部失败、跳过空ID）
  - _extract_share_card_content fallback: 1 项
  - 现有测试无回归: 236 passed / 20 failed（与改动前一致）

- ✅ **T17.5** Git 提交 + 合并
  - commit `67845aa` on feat/merge-forward → merged to local

### 影响范围

| 文件 | 改动 |
|------|------|
| `channels/feishu.py` | import `GetMessageRequest` + `_get_message_detail_sync()` + `_resolve_merge_forward()` + `_on_message()` merge_forward 分支 |
| `tests/test_merge_forward.py` | 18 项新测试 |

---

## Phase 18: 飞书通道文件附件发送修复 ✅

### 需求来源
- nanobot REQUIREMENTS.md §十七：飞书通道文件附件发送修复
- Backlog: 2026-03-01 用户在 feishu.lab 通道发送 docx 文件 3 次均失败

### 根因
LLM 调用 `message` 工具时传入 `channel: "feishu"` 覆盖了默认的 `"feishu.lab"`，导致 `_dispatch_outbound` 找不到匹配的 channel（注册名为 `feishu.lab`/`feishu.ST`），消息被丢弃。`MessageTool.execute()` 误报成功（fire-and-forget）。

### 任务清单

- ✅ **T18.1** `channels/manager.py` — `_resolve_channel()` channel 名称容错
  - 精确匹配失败时尝试前缀匹配（`name + "."` 前缀）
  - 唯一匹配 → 使用；多个匹配 → 丢弃并 warning
  - 添加 debug 日志

- ✅ **T18.2** `agent/tools/message.py` — 移除 `channel`/`chat_id` 参数暴露
  - 从 parameters schema 中移除 `channel` 和 `chat_id`
  - `execute()` 始终使用 `_default_channel` 和 `_default_chat_id`
  - 保留内部 `set_context()` 机制和 `**kwargs` 兼容

- ✅ **T18.3** 测试验证 — 13 项全部通过
  - _resolve_channel: 7 项（精确匹配、前缀单匹配、前缀歧义、无匹配、无点分隔符、精确优先）
  - MessageTool routing: 6 项（忽略 LLM channel、使用默认、media 传递、无 context 报错、sent_in_turn 追踪、schema 检查）
  - 回归测试：249 passed / 20 failed（与改动前一致）

- ✅ **T18.4** Git 提交 — commit `d650c10` on local

### 影响范围

| 文件 | 改动 |
|------|------|
| `channels/manager.py` | 新增 `_resolve_channel()` + `_dispatch_outbound` 使用它 |
| `agent/tools/message.py` | parameters schema 移除 channel/chat_id；execute() 忽略 LLM 传入值 |
| `tests/test_message_routing.py` | 13 项新测试 |

---

## Phase 19: Gateway 并发执行 + User Injection + Per-Session Provider

> 需求：REQUIREMENTS.md §十八 | 架构：ARCHITECTURE.md §八
> 分支：`feat/concurrent-gateway`
> 开始时间：2026-03-01

### 目标

1. 不同 session 的消息并行处理，互不阻塞
2. 同 session 执行中追加消息通过 inject 机制插入对话流
3. Provider/Model per-session 独立

### 任务清单

- ✅ **T19.1** `providers/pool.py` — Per-session provider override (commit `62ce01d`)
  - 新增 `_session_overrides: dict[str, tuple[str, str]]`
  - 新增 `get_for_session(session_key)` → `(LLMProvider, str)`
  - 新增 `switch_for_session(session_key, provider_name, model?)`
  - 新增 `clear_session_override(session_key)`
  - 单元测试

- ✅ **T19.2** Tool clone 机制 (commit `a5f4118`)
  - `agent/tools/message.py` — 新增 `clone()` 方法
  - `agent/tools/spawn.py` — 新增 `clone()` 方法
  - `agent/tools/cron.py` — 新增 `clone()` 方法
  - `agent/tools/registry.py` — 新增 `clone_for_session()` 方法
    - 共享无状态 tool 引用
    - 克隆有状态 tool（Message、Spawn、Cron）
    - 独立 audit context
  - 单元测试

- ✅ **T19.3** `agent/callbacks.py` — GatewayCallbacks (commit `fa26148`)
  - 新增 `GatewayCallbacks(DefaultCallbacks)` 类
    - `_inject_queue: asyncio.Queue[str]`
    - `check_user_input()` — 非阻塞检查 inject queue
    - `inject(text)` — 放入 inject queue
    - `on_progress()` — 转发到 bus outbound
  - 单元测试

- ✅ **T19.4** `agent/loop.py` — `_process_message()` 参数化 (commit `607bd6d`)
  - 新增 `provider`, `model`, `tools` 可选参数
  - 内部使用传入参数而非 `self.provider`/`self.model`/`self.tools`
  - `_run_agent_loop()` 同步改为使用传入的 provider/model
  - `_chat_with_retry()` 接收 provider 参数
  - `_consolidate_memory()` 使用传入的 provider/model
  - `_set_tool_context()` 操作传入的 tools 而非 `self.tools`
  - 确保 `process_direct()` 路径不受影响（fallback 到 self.*）
  - 回归测试

- ✅ **T19.5** `agent/loop.py` — 并发 Dispatcher `run()` 重构 (commit `346659e`)
  - `active_sessions: dict[str, SessionWorker]` 管理
  - 消息路由：new session → create task; active session → inject; /stop → cancel
  - `/provider` 改为 per-session switch
  - task done callback 清理 active_sessions
  - 删除 `_wait_with_stop_listener()` 和 `_active_task*` 全局指针
  - 集成测试

- ✅ **T19.6** 集成测试 + 回归测试 (commit `14b0221`)
  - 并发执行：两个 session 同时处理
  - User injection：执行中追加消息
  - Per-session provider：不同 session 不同 model
  - /stop 精确取消
  - CLI/SDK 模式回归
  - 现有测试全部通过

- ✅ **T19.7** Git 提交 + 文档更新 (merged to `local`)
  - commit to `feat/concurrent-gateway`
  - merge to `local`
  - 更新 MEMORY.md

---

## Phase 20: /session 状态查询命令 ✅

> 需求：REQUIREMENTS.md §十九 | 架构：ARCHITECTURE.md §九
> 分支：直接在 `local` 上开发（小功能）
> 完成时间：2026-03-01

### 目标

提供 `/session` 斜杠命令，让用户快速查看当前 session 的名称、执行状态、Provider/Model、消息统计等信息。

### 任务清单

- ✅ **T20.1** `agent/loop.py` — 新增 `_handle_session_command()` 方法 (commit `14e7738`)
  - 显示 session key
  - 显示执行状态：🔄 执行中 / 💤 空闲（基于 `active_sessions` 字典）
  - 显示当前 provider / model
  - 显示消息数（总数 + 未归档数）
  - 显示创建时间 / 最后更新时间
  - Gateway 并发模式：检查 `active_sessions` 中的 task 状态
  - CLI/直接调用模式：始终显示空闲（无 active_sessions）
  - 更新 `/help` 文本包含 `/session`
- ✅ **T20.2** `agent/loop.py` — `/session` 输出增加累计 Token 用量 (commit `a31eb07`)
  - 通过 `UsageRecorder.get_session_usage()` 查询 analytics.db
  - 显示 prompt / completion / total tokens + LLM 调用次数

---

## Phase 21: /new 归档方向反转 + Session 命名简化 ✅

> 需求：REQUIREMENTS.md §二十 | 分支：直接在 `local` 上开发
> 开始时间：2026-03-01

### 背景

`/new` 命令归档旧 session 文件并创建新 session，但新旧 session 共用同一个 session_key，导致 `analytics.db` 中的 usage 统计是所有历史 session 的累加。同时飞书 session 文件名包含完整 open_id，过于冗长。

### 任务清单

- ✅ **T21.1** `session/manager.py` — 重构 `create_new_session()` 方法 (commit `a2cd91a`)
  - 旧文件不动（保持原 key 和文件名）
  - 新文件使用 `{channel}.{timestamp}` 格式的新 key
  - routing 表更新：`natural_key → new_key`
  - invalidate 旧 key 和 natural_key 的缓存
- ✅ **T21.2** `agent/loop.py` — 适配新的 `create_new_session()` 返回值 (commit `a2cd91a`)
  - `/flush` 路径：注释更新
  - `/new` 路径：返回新 key 给用户
  - `run()` 并发 dispatcher 中 `resolve_session_key()` 无需改动（已完备）
- ✅ **T21.3** 测试验证 (commit `a2cd91a`)
  - 11 项测试全部通过（`test_new_session.py`）
  - 新增测试：旧文件保持原样、routing 更新、多次 /new、从 routed key 再 /new
- ✅ **T21.4** Git 提交 + 文档更新
- [ ] **T21.5** 重启 gateway 使改动生效

---

## Phase 22: Merge main → local (2026-03-02)

> 目的：同步 upstream main 的最新改动到 local 分支
> 合并提交：`fa3c817` | 修复提交：`6e34d60`, `59ce2bf`

### 背景

local 分支长期独立开发（Phase 1~21），积累了大量架构差异。upstream main 持续更新，需要定期合并以获取 bug fix 和新功能。

### 冲突文件与解决策略

共 **9 个文件** 产生冲突，以下记录每个冲突的取舍决策：

#### 1. `nanobot/agent/loop.py` (8 处冲突) — 核心差异最大

| 冲突点 | Upstream 方案 | Local 方案 | 取舍 |
|--------|--------------|-----------|------|
| 并发模型 | `_dispatch()` + `_processing_lock` 串行化 | `run()` 内 `SessionWorker` 并发 task | **保留 local** — 每 session 独立 task，性能更好 |
| `/stop` 实现 | `_handle_stop()` 操作 `_active_tasks` | `run()` 内 inline 取消 `SessionWorker.task` | **保留 local** — `_handle_stop` 保留为 legacy stub |
| `reasoning_effort` | 新增参数传递到 `_chat_with_retry` | 不存在 | **合入 upstream** — 新增到 `_chat_with_retry` 签名 |
| `finish_reason=error` | `_run_agent_loop` 中检测 error response | 不存在 | **合入 upstream** — 防止 error 污染 context（⚠️ Phase 23 进一步改进：改为存储到 JSONL 但由 get_history Phase 2 过滤） |
| `thinking_blocks` | `_run_agent_loop` 中处理 thinking blocks | 不存在 | **合入 upstream** — 支持 Claude thinking |
| `_consolidate_memory` 签名 | `(self, session, archive_all)` | `(self, session, archive_all, provider, model)` | **保留 local** — 支持 per-session provider |
| `/new` 行为 | 先 archive 再 clear | 直接 `create_new_session()` 不 archive | **保留 local** — 快速切换，archive 用 `/flush` |
| `_get_consolidation_lock` | 新增 helper 方法 | 使用 `_consolidation_locks.setdefault()` | **保留 local** — 修复残留调用 |

#### 2. `nanobot/cli/commands.py` (5 处冲突)

| 冲突点 | 取舍 |
|--------|------|
| Provider 初始化 | **保留 local** — 使用 `ProviderPool` 多 provider 支持 |
| Logging | **保留 local** — loguru + usage/detail/audit logger |
| AgentLoop 构造 | **保留 local** — 传入 `reasoning_effort` 等额外参数 |
| 输出格式 | **保留 local** — 自定义 welcome banner |
| Config 加载 | **保留 local** — 支持 `providers[]` 数组配置 |

#### 3. `nanobot/session/manager.py` (3 处冲突)

| 冲突点 | 取舍 |
|--------|------|
| `append_message` | **保留 local** — realtime persist + `_trim_incomplete_tool_tail` |
| `create_new_session` | **保留 local** — 反转归档方向（旧文件不动，新文件新 key） |
| Session routing | **保留 local** — `resolve_session_key` + routing table |

#### 4. `nanobot/agent/context.py` (1 处冲突)

- **保留 local** — `thinking_blocks` 过滤逻辑在 `build_messages` 中

#### 5. `nanobot/agent/tools/registry.py` (1 处冲突)

- **保留 local** — `clone()` / `clone_for_session()` 方法，支持并发 session 隔离

#### 6. `nanobot/agent/tools/shell.py` (1 处冲突)

- **保留 local** — `audit_logger` 集成，记录所有 shell 命令执行

#### 7. `nanobot/channels/feishu.py` (1 处冲突)

- **保留 local** — 飞书 SDK (`lark_oapi`) 集成，支持 WebSocket 长连接

#### 8. `nanobot/channels/manager.py` (1 处冲突)

- **合入 upstream** — Matrix channel 支持 + **保留 local** — ChannelManager 架构

#### 9. `nanobot/channels/telegram.py` (1 处冲突)

- **保留 local** — `session_key` 支持（区分群组/私聊）

### 从 Upstream 合入的新功能

| 功能 | 说明 |
|------|------|
| Matrix channel | 新增 `channels/matrix.py`，需要 `nh3` 依赖 |
| `thinking_blocks` 支持 | Claude 3.5+ 的 extended thinking 处理 |
| `finish_reason=error` 防护 | 检测 API error response，防止污染 session context（⚠️ Phase 23 改进为存储+过滤方案） |
| `reasoning_effort` 参数 | 传递给 provider，控制推理深度 |
| `_consolidate_memory` 返回值 | 返回 `bool` 表示成功/失败 |

### 测试修复

| 测试文件 | 修改内容 |
|----------|---------|
| `test_task_cancel.py` | 移除 `_dispatch` / `_processing_lock` 测试，新增并发 session 模型测试 |
| `test_message_tool_suppress.py` | 更新为 local 的 channel override 行为（MessageTool 忽略 LLM 指定的 channel） |
| `test_consolidate_offset.py` | 修复 mock 签名（`**kwargs`），替换 `/new` archival 测试为 local 的 `/new`（无 archive）+ `/flush` 测试 |

### 最终测试结果

```
329 passed, 0 failed (excluding test_matrix_channel.py — needs nh3 dep)
```

### 关键架构差异总结（供后续 merge 参考）

| 维度 | Upstream (main) | Local |
|------|----------------|-------|
| 并发模型 | `_dispatch` + `_processing_lock` | `SessionWorker` per-session task |
| Provider | 单 provider | `ProviderPool` 多 provider + per-session 切换 |
| `/new` 行为 | archive → clear | 直接 `create_new_session()`（无 archive） |
| MessageTool routing | 尊重 LLM 指定的 channel | 强制使用 context channel（防 misroute） |
| Session 持久化 | batch save | realtime persist (`append_message`) |
| Logging | stdlib logging | loguru + usage/detail/audit 三层 logger |
| 飞书 | HTTP polling | SDK WebSocket 长连接 |
| Tool 隔离 | 共享 registry | `clone_for_session()` 每 session 独立 |

---

## Phase 23: LLM 错误响应持久化与前端展示 (2026-03-03) ✅

> Phase 22 merge 后续修正 | 直接在 local 分支
> commit: `ea0ed02`

### 背景

Phase 22 合并 upstream 时引入了 `finish_reason="error"` 防护：不存储错误响应到 JSONL。但这导致 web-chat 前端看不到错误信息、SSE 无错误内容。

### 方案

利用 `get_history()` Phase 2 已有的 `"Error calling LLM:"` 前缀过滤机制：
- 存储到 JSONL（前端可展示） + 自动从 LLM context 过滤（防中毒）

### 任务清单

- ✅ **T23.1** `loop.py` — 错误响应持久化 + callback 通知（`on_message` + `on_progress`）
- ✅ **T23.2** web-chat `MessageItem.tsx` — 检测错误前缀 → ❌ 图标 + 红色气泡
- ✅ **T23.3** web-chat `MessageList.module.css` — `.errorBubble` 样式
- ✅ **T23.4** `test_error_response.py` — 5 个新测试（持久化、历史过滤、callback、默认消息、正常不受影响）
- ✅ **T23.5** 全量测试 334 passed + 前端构建 + 服务重启

### 影响文件

| 文件 | 改动 |
|------|------|
| `nanobot/agent/loop.py` | `finish_reason="error"` 分支重写 |
| web-chat `MessageItem.tsx` | 错误消息检测与样式化 |
| web-chat `MessageList.module.css` | 错误气泡 CSS |
| `tests/test_error_response.py` | 新增 5 个测试 |

---

## Phase 24: ProviderConfig preferred_model 字段 ✅

> 日期：2026-03-04
> 需求：web-chat REQUIREMENTS.md §三十三 (Issue #46)

### 改动

- `config/schema.py`: `ProviderConfig` 新增 `preferred_model: str | None = None`
- `cli/commands.py`: `_make_provider()` 优先使用 `p.preferred_model`，fallback 到硬编码默认值
- 适用于所有 provider 类型：LiteLLM、Custom、OAuth (Codex/Copilot)
- 向后兼容：`None` 表示使用现有默认行为

### 任务清单

- ✅ **T24.1** `config/schema.py` — 新增 `preferred_model` 字段
- ✅ **T24.2** `cli/commands.py` — `_make_provider()` 使用 `preferred_model`
- ✅ **T24.3** 334 tests passed
- ✅ **T24.4** `docs/LOCAL_CHANGES.md` 更新

### 影响文件

| 文件 | 改动 |
|------|------|
| `nanobot/config/schema.py` | `preferred_model: str \| None = None` |
| `nanobot/cli/commands.py` | OAuth/Custom/LiteLLM 三处 preferred_model 优先 |
| `docs/LOCAL_CHANGES.md` | Phase 24 记录 |

---

## Phase 25: 迭代预算软限制提醒 + exec 动态超时 (2026-03-06) ✅

> 来源: eval-bench batch_build + QA R2 复盘改进项 (B1 + exec timeout)
> 直接在 local 分支 | REQUIREMENTS.md §二十二 + §二十三 | ARCHITECTURE.md §十

### 背景

eval-bench 批量构造中两个最频繁的问题：
1. AgentLoop 迭代耗尽时 LLM 无预警，无法优雅收尾（调度 session 多次被截断）
2. exec 工具超时固定（默认 60s），git clone 大仓库等长命令频繁超时

### 任务清单

#### 25a: 迭代预算软限制提醒

- ✅ **T25a.1** `loop.py` — 新增 `_budget_alert_threshold()` 函数 + 循环内 budget alert 注入
- ✅ **T25a.2** `tests/test_budget_alert.py` — 8 个测试（阈值计算 5 + 注入逻辑 3）
- ✅ **T25a.3** 全量测试通过 (349 passed)

#### 25b: exec 动态超时参数

- ✅ **T25b.1** `shell.py` — `parameters` 新增 `timeout`；`execute()` 支持动态超时 + `MAX_TIMEOUT=600` 上限
- ✅ **T25b.2** `tests/test_exec_timeout.py` — 7 个测试（参数定义、动态超时、默认 fallback、上限保护、错误消息、None 处理、常量）
- ✅ **T25b.3** 全量测试通过 (349 passed)

#### 收尾

- ✅ **T25.4** Git commit: `a56e30e` (docs+code) + `1c438db` (tests) + `docs/LOCAL_CHANGES.md` 更新
- 🔜 **T25.5** 更新 MEMORY.md 项目状态

---

## Phase 26: spawn subagent 能力增强 (2026-03-06)

> 来源: eval-bench 复盘 B4 | REQUIREMENTS.md §二十四 | ARCHITECTURE.md §十一
> 直接在 local 分支 | 改动集中在 subagent.py + spawn.py

### 背景

spawn subagent 有 15 轮硬限制、无 session 持久化、无 LLM 重试、无 usage 记录，实际使用中几乎无法完成需要文件 I/O 的任务。增强后可简化批量编排架构。

### 任务清单

#### 26a: SubagentManager 核心改造

- ✅ **T26a.1** `subagent.py` — 构造函数扩展：接受 `default_max_iterations`, `usage_recorder`, `session_manager`
- ✅ **T26a.2** `subagent.py` — `spawn()` 签名扩展：接受 `max_iterations`, `persist` 参数
- ✅ **T26a.3** `subagent.py` — `_run_subagent()` max_iterations 可配 + 硬上限 100
- ✅ **T26a.4** `subagent.py` — `_run_subagent()` budget alert 注入（复用 `_budget_alert_threshold`）
- ✅ **T26a.5** `subagent.py` — `_chat_with_retry()` LLM 重试机制（3 次，5s/10s/20s）
- ✅ **T26a.6** `subagent.py` — usage 记录（每次 LLM 调用后写入 SQLite）
- ✅ **T26a.7** `subagent.py` — persist 模式：session 持久化到 JSONL

#### 26b: SpawnTool 参数扩展

- ✅ **T26b.1** `spawn.py` — parameters 新增 `max_iterations` 和 `persist`
- ✅ **T26b.2** `spawn.py` — execute() 透传新参数给 SubagentManager.spawn()

#### 26c: AgentLoop 集成

- ✅ **T26c.1** `loop.py` — SubagentManager 构造时传入 `usage_recorder` + `session_manager`（2 行）

#### 26d: 测试

- ✅ **T26d.1** `tests/test_subagent.py` — 27 个测试全部通过
  - _budget_alert_threshold: 3 项
  - _is_retryable: 4 项
  - SubagentManager defaults: 3 项
  - spawn max_iterations: 3 项
  - budget alert injection: 1 项
  - _chat_with_retry: 4 项
  - usage recording: 2 项
  - session persistence: 2 项
  - SpawnTool parameters: 3 项
  - announce result: 2 项
- ✅ **T26d.2** 全量回归: 376 passed, 0 failed（排除 test_matrix_channel.py 已知 dep 问题）

#### 26e: 收尾

- ✅ **T26e.1** Git commit: `3114b8d`
- ✅ **T26e.2** 更新 MEMORY.md 项目状态

---

## Phase 27: ProviderPool **kwargs 透传 + 接口一致性防护 (2026-03-07) ✅

> 来源: reasoning_effort bug 复盘 | 直接在 local 分支
> commit: `38d6bf8`

### 背景

Phase 22 merge upstream 时，`base.py` 和 `litellm_provider.py` 新增了 `reasoning_effort` 参数，但 `pool.py` 是 local 独有代码，merge 不会自动更新其签名。Phase 26 的 subagent `_chat_with_retry()` 直接传 `reasoning_effort` 给 `provider.chat()`，当 provider 是 ProviderPool 时报错。Bug 从 3月5日开始出现，3月7日才定位。

### 任务清单

- ✅ **T27.1** `providers/pool.py` — chat() 改为 `**kwargs` 透传
  - 签名从显式参数列表改为 `(self, messages, **kwargs)`
  - `kwargs["model"] = self._active_model` 覆盖 model
  - upstream 添加任何新参数时 ProviderPool 无需修改

- ✅ **T27.2** `agent/subagent.py` — `_chat_with_retry()` 改为条件传递
  - 与 `agent/loop.py` 的 `_chat_with_retry` 一致
  - `if self.reasoning_effort is not None: kwargs["reasoning_effort"] = ...`

- ✅ **T27.3** `tests/test_provider_pool.py` — 新增接口一致性测试
  - `TestProviderInterfaceConsistency` 类（3 项签名检查）
  - `test_chat_passes_reasoning_effort` 回归测试
  - `test_chat_forwards_unknown_kwargs` 前向兼容测试

- ✅ **T27.4** `docs/LOCAL_CHANGES.md` — Merge 后必检清单
  - LLMProvider 接口一致性
  - SubagentManager 参数透传
  - SDK AgentRunner 初始化同步
  - Web-chat Gateway/Worker 兼容性

- ✅ **T27.5** 全量测试: 384 passed, 0 failed

### 影响文件

| 文件 | 改动 |
|------|------|
| `providers/pool.py` | chat() → **kwargs 透传 |
| `agent/subagent.py` | _chat_with_retry() 条件传递 |
| `tests/test_provider_pool.py` | +5 新测试（3 签名 + 2 回归） |
| `docs/LOCAL_CHANGES.md` | Phase 27 记录 + Merge 必检清单 |

---

## Hotfix §26: LiteLLMProvider 错误吞没导致 Retry 失效 ✅

**日期**: 2026-03-08
**分支**: local (直接提交，hotfix 级别)

### 背景

用户在 webchat session 中频繁遇到 `Error calling LLM: litellm.RateLimitError` 错误，Phase 11 实现的 retry 机制未生效。排查发现 `LiteLLMProvider.chat()` 的 `except Exception` 吞掉了所有异常（包括 RateLimitError），将其包装为 `LLMResponse(finish_reason="error")` 返回，导致 `_chat_with_retry()` 永远收不到异常、无法触发重试。该 bug 自 Phase 11 引入以来一直存在。

### 任务清单

- ✅ **H26.1** `providers/litellm_provider.py` — 新增 `_is_retryable()` 静态方法
  - 镜像 `AgentLoop._is_retryable()` 逻辑
  - 可重试错误 re-raise，不可重试错误保持原行为

- ✅ **H26.2** 测试验证
  - 34 个 retry 测试通过
  - 39 个 provider 测试通过

- ✅ **H26.3** 文档补齐
  - REQUIREMENTS.md §26 追加 bug 记录
  - ARCHITECTURE.md §7.8 补充 provider 层错误传播策略
  - DEVLOG.md 本条记录

- ✅ **H26.4** Gateway 重启生效 (PID 91610)

### 影响文件

| 文件 | 改动 |
|------|------|
| `providers/litellm_provider.py` | 新增 `_is_retryable()` + except 分支改为条件 re-raise |
| `docs/REQUIREMENTS.md` | §26 bug 记录 |
| `docs/ARCHITECTURE.md` | §7.8 provider 层错误传播策略 |
| `docs/DEVLOG.md` | 本条 |

---

## Phase 28: 弱网 LLM API 稳定性增强

> 日期: 2026-03-08 | 分支: feat/weak-network-resilience

### 背景

弱网条件下频繁出现 `Server disconnected` 和 `Connection timed out (600s)` 导致 session 中断。

### 诊断结果

| # | 问题 | 严重性 |
|---|------|--------|
| 1 | litellm 默认 timeout=6000s，弱网下单次请求卡太久 | 🔴 高 |
| 2 | AgentLoop 重试延迟 10/20/40/80/160s，对 disconnected 等待过久 | 🟡 中 |
| 3 | subagent `_is_retryable()` 遗漏 `Timeout` 类名 | 🟡 中 |
| 4 | `_is_retryable()` 缺少 disconnected/connection reset 匹配 | 🟡 中 |
| 5 | AgentLoop 和 subagent 各自维护重复的 `_is_retryable()` | 🟡 中 |

### 实现

- ✅ **H28.1** 新建 `agent/retry.py` 共享模块
  - `is_retryable()` — 统一判断是否可重试（类名 + 状态码 + 消息模式）
  - `is_fast_retryable()` — 区分 fast（断连/超时 → 2/4/8s）vs slow（限流 → 10/20/40s）
  - `compute_retry_delay()` — 智能延迟计算

- ✅ **H28.2** 修改 `providers/litellm_provider.py`
  - 设置 `timeout=300s`（从默认 6000s 降低）
  - 启用 `num_retries=2`（litellm 层面连接级快速重试）
  - `_is_retryable()` 委托给共享模块

- ✅ **H28.3** 修改 `agent/loop.py`
  - `_is_retryable()` 委托给共享模块
  - `_chat_with_retry()` 使用智能延迟（fast vs slow）
  - 重试次数从 5 次增到 7 次
  - progress 消息区分"网络断连"和"API 限流"

- ✅ **H28.4** 修改 `agent/subagent.py`
  - `_is_retryable()` 委托给共享模块
  - `_chat_with_retry()` 使用智能延迟
  - 重试次数从 3 次增到 5 次

- ✅ **H28.5** 测试
  - 新增 `tests/test_retry.py`（18 个测试全部通过）
  - 全量回归测试 402 passed, 0 failed

### 影响文件

| 文件 | 改动 |
|------|------|
| `agent/retry.py` (新) | 共享重试工具模块 |
| `agent/loop.py` | 使用共享 retry，智能延迟，7 次重试 |
| `agent/subagent.py` | 使用共享 retry，智能延迟，5 次重试 |
| `providers/litellm_provider.py` | timeout=300s, num_retries=2, 共享 _is_retryable |
| `tests/test_retry.py` (新) | 18 个单元测试 |
| `docs/REQUIREMENTS.md` | §27 需求记录 |
| `docs/DEVLOG.md` | 本条 |

---

## Phase 30: Session 间消息传递机制 (SessionMessenger)

> 需求: REQUIREMENTS.md §29 | 分支: local
> 完成时间: 2026-03-08 | Commit: `6b29ac1`

### 背景

subagent 完成后通过 `_announce_result()` 发 `InboundMessage(channel="system")` 到 bus，存在 session_key 不匹配、web worker 消息丢失、CLI 消息丢失三个问题。

### 任务清单

- ✅ **T30.1** `callbacks.py` — 新增 `SessionMessenger` Protocol
- ✅ **T30.2** `subagent.py` — 新增 `session_messenger` 参数，改造 `_announce_result`
  - 新增 `parent_session_key` 参数传递链：spawn() → _run_subagent() → _announce_result()
  - 优先使用 SessionMessenger，fallback 到 bus publish（加 session_key_override 修复 key 不匹配 bug）
- ✅ **T30.3** `loop.py` — 新增参数透传，inject 前缀兜底，`GatewaySessionMessenger` 实现
  - AgentLoop.__init__ 新增 session_messenger 参数，透传给 SubagentManager
  - run() 中创建 GatewaySessionMessenger 并设置到 subagents
  - Gateway inject 加 `[Message from user during execution]` 前缀
  - _run_agent_loop inject checkpoint 加兜底前缀逻辑
- ✅ **T30.4** `worker.py` — `WorkerSessionMessenger` 实现，inject 前缀
  - WorkerSessionMessenger: running task → inject queue, idle → _run_task_sdk()
  - _handle_inject 加前缀
  - on_message user 解析改进（支持 source 提取）
- ✅ **T30.5** `tests/test_session_messenger.py` — 12 个新测试全部通过
  - Protocol 存在性 + runtime checkable: 2 项
  - GatewaySessionMessenger inject/trigger/prefix: 4 项
  - SubagentManager announce with/without messenger: 3 项
  - Inject prefix fallback: 2 项
  - End-to-end spawn with messenger: 1 项
- ✅ **T30.6** 全量测试 387 passed（排除 2 个已知 flaky: test_llm_retry + test_subagent retry_exhausted）

### 影响文件

| 文件 | 改动 |
|------|------|
| `agent/callbacks.py` | +SessionMessenger Protocol |
| `agent/subagent.py` | +session_messenger 参数, parent_session_key 传递, _announce_result 改造 |
| `agent/loop.py` | +session_messenger 参数透传, GatewaySessionMessenger, inject 前缀 |
| web-chat `worker.py` | +WorkerSessionMessenger, inject 前缀, on_message 解析改进 |
| `tests/test_session_messenger.py` | 12 个新测试 |
| `tests/test_concurrent_gateway.py` | 更新 inject 断言适配新前缀 |

---

*本文件随开发进展持续更新。*

---

## Phase 29: SpawnTool session_key 传递修复

**需求**: §28 | **状态**: ✅ 已完成 | **Commit**: `350286f`

### 问题

`SpawnTool.set_context()` 从 `channel:chat_id` 拼接 `_session_key`，在以下场景与实际 session_key 不一致：
- web worker: `channel='web'` 但 session_key 前缀为 `webchat:`
- 飞书/CLI routing 后: natural key 被映射为 timestamped key

导致 subagent session key 编码了错误的 parent，前端 `resolveParent()` 无法还原父子关系。

### 修复

- `spawn.py`: `set_context()` 新增 `session_key` 可选参数，优先使用传入值
- `loop.py`: `_set_tool_context()` 将已有的 `session_key` 透传给 `spawn_tool.set_context()`

### 影响范围

2 个文件，改动 3 行代码。向后兼容（`session_key` 参数有默认值，不传则 fallback 到原逻辑）。
