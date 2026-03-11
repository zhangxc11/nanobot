# nanobot 核心 — 开发工作日志

<!-- 📖 文档组织说明
本开发日志采用"主文件 + 归档子文件"结构：
- **本文件（主文件）**：项目状态总览 + 全量 Phase 索引 + 最近 3 个 Phase 完整正文
- **devlog/ 子目录**：按 Phase 编号分组的历史开发记录归档

🔍 如何查找历史 Phase：
1. 在"全量 Phase 索引"表中按编号/标题找到归档文件链接
2. 最近 3 个 Phase 的完整正文直接在本文件底部

📝 如何记录新 Phase：
1. 在本文件底部追加新 Phase 正文（保持最近 3 个 Phase 在主文件中）
2. 将第 4 旧的 Phase 移入最新的归档文件
3. 更新"全量 Phase 索引"表
4. 更新"项目状态总览"表

⚠️ 维护规则：
- 主文件始终只保留最近 3 个 Phase 的完整正文
- 归档文件中的内容一旦写入不再删减
- 新 Phase 完成后及时更新状态总览表（🔜 → ✅）
- 全量索引表必须涵盖所有 Phase，一个不漏
-->

> 本文件是开发过程的唯一真相源。每次新 session 从这里恢复上下文。
> 找到 🔜 标记的任务，直接继续执行。
> 历史 Phase 详情见 `devlog/` 目录下的归档文件。

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
| Phase 31: Subagent 回报消息 role 修正 + announce 模板优化 | ✅ 已完成 | local |
| §33 Hotfix: ServiceUnavailableError 误判为可重试错误 | ✅ 已完成 | local |
| Phase 35: Subagent 回报消息 role 回归 user (§35) | ✅ 已完成 | local |
| Phase 37: read_file 大文件保护 (§34) | ✅ 已完成 | local |
| Phase 42: 核心层基础改动 (§41-§45) | ✅ 已完成 | local |
| Phase 43: Spawn 并发限制 (§46) | ✅ 已完成 | local |
| Phase 44: SubagentEventCallback 协议 (§47) | ✅ 已完成 | local |
| Phase 45: 日志增强 + 标记修正 + Budget 优化 (§48) | ✅ 已完成 | local |
| Phase 46: Tool Result 截断阈值扩大 (§49) | ✅ 已完成 | local |

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

## 全量 Phase 索引

| Phase | 标题 | 状态 | 归档文件 |
|-------|------|------|---------|
| 1 | 实时 Session 持久化 (Backlog #7) | ✅ | [devlog/phase-01-10.md](devlog/phase-01-10.md) |
| 2 | 统一 Token 记录 (Backlog #8) | ✅ | [devlog/phase-01-10.md](devlog/phase-01-10.md) |
| 3 | SDK 化改造 (Backlog #6) | ✅ | [devlog/phase-01-10.md](devlog/phase-01-10.md) |
| 4 | 实时 Token 用量记录 | ✅ | [devlog/phase-01-10.md](devlog/phase-01-10.md) |
| Bug Fix | SessionManager 路径双重嵌套 | ✅ | [devlog/phase-01-10.md](devlog/phase-01-10.md) |
| 5 | 工具调用间隙用户消息注入 | ✅ | [devlog/phase-01-10.md](devlog/phase-01-10.md) |
| 6 | LLM 调用详情日志 (web-chat Backlog #15) | ✅ | [devlog/phase-01-10.md](devlog/phase-01-10.md) |
| 7 | 文件访问审计日志 | ✅ | [devlog/phase-01-10.md](devlog/phase-01-10.md) |
| 8 | Session 自修复 — 未完成 tool_call 链 + 错误消息清理 | ✅ | [devlog/phase-01-10.md](devlog/phase-01-10.md) |
| 9 | 多飞书租户支持 | ✅ | [devlog/phase-01-10.md](devlog/phase-01-10.md) |
| 10 | media 参数支持 | ✅ | [devlog/phase-01-10.md](devlog/phase-01-10.md) |
| 11 | LLM API 速率限制重试机制 | ✅ | [devlog/phase-11-20.md](devlog/phase-11-20.md) |
| 12 | /new 命令重构 — 新建 Session | ✅ | [devlog/phase-11-20.md](devlog/phase-11-20.md) |
| 13 | /stop 命令 — 取消运行中的任务 | ✅ | [devlog/phase-11-20.md](devlog/phase-11-20.md) |
| 14 | 大图片自动压缩 | ✅ | [devlog/phase-11-20.md](devlog/phase-11-20.md) |
| 15 | 图片存储架构改进 | ✅ | [devlog/phase-11-20.md](devlog/phase-11-20.md) |
| 16 | ProviderPool — 运行时 Provider 动态切换 | ✅ | [devlog/phase-11-20.md](devlog/phase-11-20.md) |
| 17 | 飞书合并转发消息（merge_forward）解析 | ✅ | [devlog/phase-11-20.md](devlog/phase-11-20.md) |
| 18 | 飞书通道文件附件发送修复 | ✅ | [devlog/phase-11-20.md](devlog/phase-11-20.md) |
| 19 | Gateway 并发执行 + User Injection + Per-Session Provider | ✅ | [devlog/phase-11-20.md](devlog/phase-11-20.md) |
| 20 | /session 状态查询命令 | ✅ | [devlog/phase-11-20.md](devlog/phase-11-20.md) |
| 21 | /new 归档方向反转 + Session 命名简化 | ✅ | [devlog/phase-21-30.md](devlog/phase-21-30.md) |
| 22 | Merge main → local | ✅ | [devlog/phase-21-30.md](devlog/phase-21-30.md) |
| 23 | LLM 错误响应持久化与前端展示 | ✅ | [devlog/phase-21-30.md](devlog/phase-21-30.md) |
| 24 | ProviderConfig preferred_model 字段 | ✅ | [devlog/phase-21-30.md](devlog/phase-21-30.md) |
| 25 | 迭代预算软限制提醒 + exec 动态超时 | ✅ | [devlog/phase-21-30.md](devlog/phase-21-30.md) |
| 26 | spawn subagent 能力增强 | ✅ | [devlog/phase-21-30.md](devlog/phase-21-30.md) |
| 27 | ProviderPool **kwargs 透传 + 接口一致性防护 | ✅ | [devlog/phase-21-30.md](devlog/phase-21-30.md) |
| Hotfix §26 | LiteLLMProvider 错误吞没导致 Retry 失效 | ✅ | [devlog/phase-21-30.md](devlog/phase-21-30.md) |
| 28 | 弱网 LLM API 稳定性增强 | ✅ | [devlog/phase-21-30.md](devlog/phase-21-30.md) |
| 30 | Session 间消息传递机制 (SessionMessenger) | ✅ | [devlog/phase-21-30.md](devlog/phase-21-30.md) |
| 31 | Subagent 回报消息 role 修正 + announce 模板优化 | ✅ | [devlog/phase-31-40.md](devlog/phase-31-40.md) |
| §31 Hotfix | unhashable type 'slice' on subagent injection | ✅ | [devlog/phase-31-40.md](devlog/phase-31-40.md) |
| §33 Hotfix | ServiceUnavailableError 误判为可重试错误 | ✅ | [devlog/phase-31-40.md](devlog/phase-31-40.md) |
| 35 | Subagent 回报消息 role 回归 user — Cache 友好改造 | ✅ | [devlog/phase-31-40.md](devlog/phase-31-40.md) |
| 29 | SpawnTool session_key 传递修复 | ✅ | [devlog/phase-31-40.md](devlog/phase-31-40.md) |
| 32 | Cache Control 策略优化 + Usage Cache 字段 | ✅ | [devlog/phase-31-40.md](devlog/phase-31-40.md) |
| 36 | 测试修复 + REQUIREMENTS.md Backlog 区域整理 | ✅ | [devlog/phase-31-40.md](devlog/phase-31-40.md) |
| 37 | read_file 大文件保护 (§34) | ✅ | [devlog/phase-31-40.md](devlog/phase-31-40.md) |
| 38 | Spawn follow_up — 向 subagent 追加消息 (§36) | ✅ | [devlog/phase-31-40.md](devlog/phase-31-40.md) |
| 39 | Spawn stop — 主动停止 subagent (§37) | ✅ | [devlog/phase-31-40.md](devlog/phase-31-40.md) |
| 40 | Spawn status — 查询 subagent 执行状态 (§38) | ✅ | [devlog/phase-31-40.md](devlog/phase-31-40.md) |
| 41 | §40 SubagentManager 单例化 + 跨进程 follow_up 恢复 | ✅ | [devlog/phase-41-45.md](devlog/phase-41-45.md) |
| 42 | §41-§45 核心层基础改动 (Phase 42) | ✅ | [devlog/phase-41-45.md](devlog/phase-41-45.md) |
| 43 | Spawn 并发限制 (§46) | ✅ | [devlog/phase-41-45.md](devlog/phase-41-45.md) |
| 44 | SubagentEventCallback 协议 (§47) | ✅ | *主文件* |
| 45 | 日志增强 + 标记修正 + Budget 优化 (§48) | ✅ | *主文件* |
| 45h | Subagent 返回消息 prompt 精简 | ✅ | *主文件* |
| 46 | Tool Result 截断阈值扩大 (§49) | ✅ | *主文件* |

---

## Phase 44: SubagentEventCallback 协议 (§47) ✅

**日期**: 2026-03-11

### 目标

为 subagent 生命周期定义 4 个回调点（spawned/progress/retry/done），
供 web-chat Worker 等外部消费者实时追踪 subagent 状态。

### 任务

- [x] **T44.1** `nanobot/agent/subagent.py` — 新增 `SubagentEventCallback` Protocol（@runtime_checkable，4 个方法）
- [x] **T44.2** `nanobot/agent/subagent.py` — `SubagentManager.__init__()` 新增 `event_callback` 参数
- [x] **T44.3** `nanobot/agent/subagent.py` — `spawn()` 调用 `on_subagent_spawned`（running + queued）
- [x] **T44.4** `nanobot/agent/subagent.py` — `_run_subagent()` 每次 iteration 调用 `on_subagent_progress`
- [x] **T44.5** `nanobot/agent/subagent.py` — `_chat_with_retry()` 重试前调用 `on_subagent_retry`
- [x] **T44.6** `nanobot/agent/subagent.py` — 所有终态调用 `on_subagent_done`（completed/failed/stopped/max_iterations + queued stop）
- [x] **T44.7** `nanobot/agent/loop.py` — `AgentLoop.__init__()` 新增 `on_iteration` 回调参数，主循环每次迭代调用
- [x] **T44.8** `tests/test_subagent_event_callback.py` — 18 项测试全部通过
- [x] **T44.9** 全量回归: 678 passed, 1 skipped
- [x] **T44.10** Git commit

### 改动文件

| 文件 | 改动 |
|------|------|
| `nanobot/agent/subagent.py` | 新增 `SubagentEventCallback` Protocol；`SubagentManager` 新增 `event_callback` 参数；spawn/iteration/retry/done 4 处回调 |
| `nanobot/agent/loop.py` | `AgentLoop` 新增 `on_iteration` 参数，主循环每次迭代调用 |
| `tests/test_subagent_event_callback.py` | 新测试文件（18 项测试） |

---

## Phase 45: 日志增强 + 标记修正 + Budget 优化 (§48) ✅

**日期**: 2026-03-11
**需求**: §48（`requirements/s40-s49.md`）
**Commit**: `d53513a`

### 任务清单

- [x] **T45.1** SubagentManager 接入 detail_logger
- [x] **T45.2** LLM logs + session JSONL 增加 provider 字段
- [x] **T45.3** Subagent 返回内容标记修正 + 闭合标签
- [x] **T45.4** Budget alert 公共函数 build_budget_alert()
- [x] **T45.5** 新增测试 (`tests/test_phase45.py`, 257 行)
- [x] **T45.6** 全量回归通过
- [x] **T45.7** Git commit

### 改动文件

| 文件 | 改动 |
|------|------|
| `nanobot/agent/subagent.py` | detail_logger 接入 + 返回内容改为闭合标签 `<!-- nanobot:system -->` |
| `nanobot/agent/loop.py` | LLM 日志增加 provider 字段 |
| `nanobot/agent/budget.py` | 新文件：`build_budget_alert()` 公共函数 |
| `nanobot/usage/detail_logger.py` | 增加 provider 字段支持 |
| `docs/ARCHITECTURE.md` | §48 架构说明索引 |
| `docs/architecture/core-loop.md` | 日志增强架构说明 |
| `docs/architecture/spawn.md` | 标记修正 + budget alert 架构说明 |
| `docs/requirements/s40-s49.md` | §48 需求正文 |
| `tests/test_phase45.py` | 新测试文件 |

---

## Phase 45 Hotfix: Subagent 返回消息 prompt 精简 ✅

**日期**: 2026-03-12
**Commit**: `8b6d1d5` (nanobot core)

### 问题

Subagent 返回消息的 `<!-- nanobot:system -->` 标签内，引导 prompt 存在冗余：3 条 bullet 和最后一段括号解释说的是同一件事。

### 修复

删除括号段落 `(This is an automated system notification...)`，保留 3 条精简 bullet。

### 改动文件

| 文件 | 改动 |
|------|------|
| `nanobot/agent/subagent.py` | 删除冗余括号解释段落（-2 行） |

---

## Phase 46: Tool Result 截断阈值扩大 (§49) ✅

**日期**: 2026-03-12
**需求**: §49（`requirements/s40-s49.md`）
**Commit**: `89e8e49`

### 背景

`_TOOL_RESULT_MAX_CHARS = 500` 导致约 40-44% 的 tool result 被截断，
后续 turn 的 LLM 历史上下文丢失有用信息。经分析，扩大到 2000 是成本与信息保留的平衡点。

### 任务清单

- [x] **T46.1** `nanobot/session/manager.py` — `_TOOL_RESULT_MAX_CHARS` 从 500 改为 2000
- [x] **T46.2** `tests/test_image_storage.py` — 测试用长内容从 1000→3000（确保仍触发截断）
- [x] **T46.3** 测试通过
- [x] **T46.4** Git commit

### 改动文件

| 文件 | 改动 |
|------|------|
| `nanobot/session/manager.py` | `_TOOL_RESULT_MAX_CHARS = 2000`（原 500） |
| `tests/test_image_storage.py` | `test_tool_result_truncation_still_works` 长内容 1000→3000 |

### 决策记录

- **为什么 2000 而非 5000？** 分析显示 5000 会导致 session 文件增长 30-60%，历史上下文 token 从 ~6K 膨胀到 ~60K（最坏情况）。2000 是平衡点：覆盖大多数有意义输出，同时控制膨胀在 15-30%。
- **截断只影响跨 turn 历史**：当前 turn 内 LLM 始终看到完整原始 tool result（截断发生在持久化到 JSONL 时）。
- **后续可考虑**：token 级 context 安全阀（当前仅按消息条数 memory_window=100 限制）。
