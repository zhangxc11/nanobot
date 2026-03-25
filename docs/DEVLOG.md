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
| Phase 47: Inject 队列 Drain 修复 (§50) | ✅ 已完成 | local |
| Phase 48: Runtime Context 注入 Session ID (§51) | ✅ 已完成 | local |
| Phase 49: Cron name 参数 + 消息格式优化 (§55) | ✅ 已完成 | feat/batch-20260313-plan-core-cron |
| Phase 50: Session 父子关系解析统一到核心 (§54) | ✅ 已完成 | feat/batch-20260313-plan-core-cron |
| Phase 52: Subagent Provider 继承 (§56) | ✅ 已完成 | feat/batch-20260313-plan-core-fixes |
| Phase 53: 飞书语音消息 recognition 提取 (§57) | ✅ 已完成 | feat/batch-20260313-plan-core-fixes |
| Phase 54: follow_up resume 缺少 event_callback (§58) | ✅ 已完成 | local |
| Phase 55: Turn 内 Consolidation + 截断预警/通知 (§59) | ✅ 已完成 | feat/turn-consolidation |
| Phase 56: Streaming Timeout 修复与鲁棒性增强 (§60) | ✅ 已完成 | fix/s60-timeout-and-robustness |
| Phase 57: Timeout 智能诊断与恢复 (§61) | ✅ 已完成 | local `c2eb217` |
| Phase 58: 系统 Hint 消息不落盘 (§62) | ✅ 已完成 | local `c2eb217` |
| Phase 59: Consolidation 孤儿修复 + 截断预警改进 (§63 + §64) | ✅ 已完成 | feat/batch-20260320-plan-core |
| Phase 60: Tool 配对切割修复 + Warning 频率控制 (§65) | ✅ 已完成 | feat/batch-20260320-plan-core |
| Phase 61: Consolidation Pipeline 重写 (§66) | ✅ 已完成 | local `2c60460` |
| Phase 62: Consolidation 统一重设计 (§70) | ✅ 已完成 | feat/consolidation-redesign → local `aee13b1` |

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
| 44 | SubagentEventCallback 协议 (§47) | ✅ | [devlog/phase-41-45.md](devlog/phase-41-45.md) |
| 45 | 日志增强 + 标记修正 + Budget 优化 (§48) | ✅ | [devlog/phase-41-45.md](devlog/phase-41-45.md) |
| 45h | Subagent 返回消息 prompt 精简 | ✅ | [devlog/phase-41-45.md](devlog/phase-41-45.md) |
| 46 | Tool Result 截断阈值扩大 (§49) | ✅ | [devlog/phase-46-50.md](devlog/phase-46-50.md) |
| 47 | Inject 队列 Drain 修复 (§50) | ✅ | [devlog/phase-46-50.md](devlog/phase-46-50.md) |
| 48 | Runtime Context 注入 Session ID (§51) | ✅ | [devlog/phase-46-50.md](devlog/phase-46-50.md) |
| 49 | Cron name 参数 + 消息格式优化 (§55) | ✅ | [devlog/phase-46-50.md](devlog/phase-46-50.md) |
| 50 | Session 父子关系解析统一到核心 (§54) | ✅ | [devlog/phase-46-50.md](devlog/phase-46-50.md) |
| 51 | CronTool 改用 session/parents.py 验证 target_session (§53 R2) | ✅ | [devlog/phase-51-55.md](devlog/phase-51-55.md) |
| 52 | Subagent Provider 继承 (§56) | ✅ | [devlog/phase-51-55.md](devlog/phase-51-55.md) |
| 53 | 飞书语音消息 recognition 提取 (§57) | ✅ | [devlog/phase-51-55.md](devlog/phase-51-55.md) |
| 54 | follow_up resume 缺少 event_callback (§58) | ✅ | *主文件* |
| 55 | Turn 内 Consolidation + 截断预警/通知 (§59) | ✅ | *主文件* |
| 56 | Streaming Timeout 修复与鲁棒性增强 (§60) | ✅ | *主文件* |
| 57 | Timeout 智能诊断与恢复 (§61) | 🔜 | *主文件* |
| 58 | 系统 Hint 消息不落盘 (§62) | ✅ | *主文件* |
| 59 | Consolidation 孤儿修复 + 截断预警改进 (§63 + §64) | ✅ | *主文件* |
| 60 | Tool 配对切割修复 + Warning 频率控制 (§65) | ✅ | *主文件* |
| 61 | Consolidation Pipeline 重写 (§66) | ✅ | *主文件* |
| 62 | Consolidation 统一重设计 (§70 R1+R2+R3) | ✅ | *主文件* |
| 69 | Session Summary 增量更新（防遗忘） | ✅ | *主文件* |

---

---

---

## Phase 54: follow_up resume 缺少 event_callback (§58) ✅

**日期**: 2026-03-17
**需求**: §58（`requirements/s50-s59.md`）
**Commit**: `a4a0776`

### 背景

`SubagentManager.follow_up()` 在 resume 分支中，设置 `meta.status = "running"` 后直接创建后台任务，但缺少 `on_subagent_spawned(meta)` 回调通知。对比 `spawn()` 新建路径（约 385-389 行）是有调用的。这导致 web-chat worker 的 `WorkerSubagentCallback._registry` 中 follow_up 恢复的 subagent 状态不会更新回 "running"。

### 任务清单

- [x] **T54.1** `nanobot/agent/subagent.py` — follow_up resume 分支添加 `on_subagent_spawned(meta)` 回调
- [x] **T54.2** `tests/test_subagent_event_callback.py` — 新增测试验证 follow_up resume 时 callback 被调用
- [x] **T54.3** 全量回归测试通过 (873 passed, 1 skipped)
- [x] **T54.4** Git commit `a4a0776`

### 改动文件

| 文件 | 改动 |
|------|------|
| `nanobot/agent/subagent.py` | follow_up() resume 分支添加 event_callback 调用（~4 行） |
| `tests/test_subagent_event_callback.py` | 新增 TestOnSubagentSpawnedFollowUp 测试类 |

---

## Phase 55: Turn 内 Consolidation + 截断预警/通知 (§59) ✅

**日期**: 2026-03-18
**需求**: §59（`requirements/s50-s59.md`）
**架构**: §三十（`architecture/core-loop.md`）
**分支**: `feat/turn-consolidation`

### 背景

Consolidation 触发检查只在 `_process_message()` 的 turn 入口，但消息增长主要在 turn 内的 tool call 循环中。`max_iterations` 最高 100，一个 turn 可产生 200+ 条消息，导致下一个 turn 的 `get_history()` 截断大量历史信息。

### 任务清单

- [x] **T55.0** `nanobot/agent/memory.py` — `consolidate()` 新增 `detail_logger` 和 `usage_recorder` 参数，LLM 调用后记录
- [x] **T55.1** `nanobot/agent/loop.py` — 新增实例变量 `_pending_consolidation_done`
- [x] **T55.2** `nanobot/agent/loop.py` — `_run_agent_loop` 循环头部插入 Step 1/2/3 检查
- [x] **T55.3** `nanobot/agent/loop.py` — 新增 `_do_mid_turn_consolidation()` 方法
- [x] **T55.4** `nanobot/agent/loop.py` — 新增 `_trim_consolidated_messages()` 方法
- [x] **T55.5** `nanobot/agent/loop.py` — 新增 `_find_tool_aligned_cut()` 方法
- [x] **T55.6** `nanobot/agent/loop.py` — 新增 `_build_truncation_notice()` 方法
- [x] **T55.7** `nanobot/agent/loop.py` — 移除 `_process_message` 中旧的 turn 入口 consolidation 逻辑
- [x] **T55.8** `nanobot/agent/loop.py` — `_consolidate_memory()` 传入 `detail_logger` 和 `usage_recorder`
- [x] **T55.9** `nanobot/session/manager.py` — `get_history()` 头部注入截断通知（`last_consolidated > 0` 时）
- [x] **T55.10** 测试验证（import 不报错、基本逻辑正确）
- [x] **T55.11** Git commit

### 实现结果

**5 commits**（经 rebase 整理）：
```
1efff1d fix: protect user messages from consolidation trim (§59.1)
7d77f87 fix: consolidation system prompt + detail_logger (§59)
811bef6 improve: consolidation reuses session context + config (§59)
e08061c fix: consolidation failure handling + hard-truncation notice (§59)
4399c0e feat: Turn-内 Consolidation + 截断预警/通知 (§59)
```

**改动统计**: 10 files changed, 655 insertions(+), 73 deletions(-)

**关键实现**:
- Turn 内 mid-turn consolidation（`_run_agent_loop` 循环头部 Step 1/2/3）
- 截断预警 + 截断通知（`_build_truncation_notice` + session summary 展开）
- Consolidation 复用 session context 提升 cache 命中率
- §59.1: 保护用户消息不被 trim 删除（v2 拼接策略 + 语义分隔符）
- Subagent 场景完整覆盖（`_MID_TURN_PREFIXES` 匹配 parent injection）

**已知遗留（P0/P1/P2）**:
- P0: consolidation max_tokens 可能不足（大 session）
- P1: consolidation 失败后重试策略待优化
- P2: consolidation 输入无上限

---

## Phase 56: Streaming Timeout 修复与鲁棒性增强 (§60) ✅

**日期**: 2026-03-20
**需求**: §60（`requirements/s60-s69.md`）
**分支**: `fix/s60-timeout-and-robustness`

### 背景

API 代理（apia/ppapi）均不支持 streaming 透传，全量缓冲模式下 Claude Opus 大请求 TTFB 可达 192s，超过原 `read=120s` timeout。同时存在截断死循环、spawn 无法自定义 max_tokens、retry 日志缺少诊断信息等鲁棒性问题。

### 任务清单

- [x] **T56.1** `litellm_provider.py` — read timeout 120→300s + usage missing warning
- [x] **T56.2** `loop.py` — 截断检测（finish_reason=length + tool_calls → skip + inject split hint）
- [x] **T56.3** `loop.py` — non-dict tool args 防御
- [x] **T56.4** `subagent.py` + `spawn.py` — spawn max_tokens 参数支持
- [x] **T56.5** `loop.py` — retry/error 日志增加 provider name/api_base/session_key
- [x] **T56.6** `registry.py` — non-dict params 防御 + audit 异常隔离
- [x] **T56.7** `detail_logger.py` — 增加 finish_reason/content/tool_calls 字段

### Commits

```
838ccbb fix: increase read timeout to 300s + usage missing warning (§60)
ad2d74a feat: truncation detection + spawn max_tokens (§60)
44c65b1 fix: enhanced retry/error logging + defensive tool registry (§60)
```

### 改动统计

**6 files changed, 173 insertions(+), 27 deletions(-)**

| 文件 | 改动 |
|------|------|
| `nanobot/providers/litellm_provider.py` | read timeout 300s + usage missing warning |
| `nanobot/agent/loop.py` | 截断检测 + non-dict args 防御 + retry 日志增强 |
| `nanobot/agent/subagent.py` | max_tokens 参数透传 |
| `nanobot/agent/tools/spawn.py` | max_tokens 参数定义 |
| `nanobot/agent/tools/registry.py` | non-dict params 防御 + audit 异常隔离 |
| `nanobot/usage/detail_logger.py` | 增加 finish_reason/content/tool_calls 字段 |

### 决策记录

- **关闭 streaming**（`stream=False` + `read=300s`）：代理都不支持 streaming 透传
- **截断时 skip 而非 retry**：retry 可能再次截断，skip + hint 让 LLM 主动拆分更有效
- **不保留 §60-diag 诊断代码**：临时诊断代码不适合长期保留
- **不保留 partial usage extraction**：usage 缺失时记录 warning 即可

### 已知遗留

- P1: 300s 是临时方案，后续需要更智能的 timeout 策略（如基于模型/请求大小动态调整）

---

## Phase 57: Timeout 智能诊断与恢复 (§61) ✅

**日期**: 2026-03-20
**需求**: §61（`requirements/s60-s69.md`）
**分支**: `local`（直接提交，改动跨多个 sub-phase）

### 背景

§60 的 300s timeout 是临时方案。Timeout 后盲目重试浪费时间，需要先诊断（ping API）再决策（内容太多→hint / 网络断→等恢复）。

### 任务清单

- [x] **T57.1** `agent/retry.py` — 新增 `is_timeout_error()` 函数
- [x] **T57.2** `agent/retry.py` — 新增 `ping_api()` 异步函数
- [x] **T57.3** `agent/retry.py` — 新增 `wait_for_recovery()` 异步函数
- [x] **T57.4** `agent/loop.py` — `_chat_with_retry` 中 timeout 诊断逻辑
- [x] **T57.5** `agent/loop.py` — `_run_agent_loop` 中 `finish_reason="timeout"` hint 注入
- [x] **T57.6** `agent/subagent.py` — `_chat_with_retry` 中 timeout 诊断逻辑
- [x] **T57.7** `agent/subagent.py` — iteration loop 中 `finish_reason="timeout"` hint 注入
- [x] **T57.8** 语法检查 + import 验证
- [x] **T57.9** Git commit `5db4066`
- [x] **T57.10** Phase 2-4: consecutive limit + dynamic timeout + merge — commits `c626875`→`6cb195b`→`aa97436`→`2f0068d`→`4e19413`
- [x] **T57.11** 网络中断修复: recovery 失败 fall through + "cannot connect" fast pattern + label — commit `57b984a`

### Commits

| Commit | 描述 |
|--------|------|
| `5db4066` | Phase 1: ping 诊断 + finish_reason="timeout" + hint 注入 |
| `c626875` | Phase 2: consecutive timeout limit (MAX=2) |
| `6cb195b` | Phase 3: hint 注入到 subagent |
| `aa97436` | Phase 4: dynamic timeout extension |
| `2f0068d`→`4e19413` | merge commits |
| `57b984a` | fix: 网络断连 recovery 失败 fall through + fast retry pattern |

---

## Phase 58: 系统 Hint 消息不落盘 (§62) ✅

**日期**: 2026-03-20
**需求**: §62 — 系统注入的 hint 消息（truncation hint / timeout hint）以 `role: "user"` 落盘到 JSONL，前端重新加载时被当作普通用户消息气泡显示。

### 背景

`loop.py` 中两类 hint 消息通过 `append_message` 落盘：
- **截断 hint** (L692): `[System] Your previous output was truncated...`（finish_reason=length 时注入）
- **超时 hint** (L748): `[System] ⚠️ Your previous LLM call timed out...`（§61 timeout 时注入）

这些 hint 只需在当前 turn 的内存 messages 中存在（让 LLM 看到），不需要持久化。落盘后前端会错误地显示为用户消息。

### 分析

- 截断 hint: 即时效果，告诉 LLM 拆分输出，session 重建后不需要
- 超时 hint: 带计数器 `#1/2`，但 `consecutive_timeouts` 是内存变量，重建后重置，落盘的计数信息语义过期
- 其他 user role 系统消息（截断预警、budget alert、truncation notice、preserved messages）确认都是仅内存，不涉及

### 任务清单

- [x] **T58.1** `agent/loop.py` L692 — 删除截断 hint 的 `append_message` 调用
- [x] **T58.2** `agent/loop.py` L748 — 删除超时 hint 的 `append_message` 调用
- [x] **T58.3** Git commit `1517243`

### 改动文件

| 文件 | 改动 |
|------|------|
| `nanobot/agent/loop.py` | 删除 2 处 `self.sessions.append_message(session, hint_msg)`，保留 `messages.append`（内存）和 `callbacks.on_message`（SSE 推送） |

---

## Phase 59: Consolidation 孤儿修复 + 截断预警改进 (§63 + §64) ✅

**日期**: 2026-03-21
**需求**: §63 (dc7c3987) — Consolidation 孤儿 tool_result → 400 死循环
**需求**: §64 (4fad1354) — 截断预警 Prompt 改进 + 静默清理

### §63 背景

`memory.py` 的 `consolidate()` 按索引切片时，切片起始处可能包含孤儿 `tool_result`（对应的 assistant tool_use 在切片之前），导致 Anthropic 返回 400。而 `retry.py` 将 400 视为可重试错误，造成死循环。

### §63 任务清单

- [x] **T59.1** `agent/memory.py` — `consolidate()` 中 strip 切片开头的 orphan tool_result
- [x] **T59.2** `agent/retry.py` — `is_retryable()` 中 400 status code 提前返回 False
- [x] **T59.3** `tests/test_consolidation_orphan.py` — 新增 7 个 retry 测试 + 4 个 consolidation 测试

### §64 背景

§59 的截断预警 prompt 过于冗长，模型写完 summary 后 3 条消息留在内存中浪费上下文。

### §64 任务清单

- [x] **T59.4** `agent/loop.py` — `_TRUNCATION_WARNING_TEMPLATE` 改为简洁的系统提示风格
- [x] **T59.5** `agent/loop.py` — 新增 silent cleanup 逻辑（tool 执行后检测 summary write 并清理 3 条消息）
- [x] **T59.6** `tests/test_consolidation_orphan.py` — 新增 template 格式验证测试

### 改动文件

| 文件 | 改动 |
|------|------|
| `nanobot/agent/memory.py` | `consolidate()` 中切片后 while 循环 strip 开头 role=="tool" 的消息 |
| `nanobot/agent/retry.py` | `is_retryable()` 中在 class name 检查前增加 400 status 检查 |
| `nanobot/agent/loop.py` | 更新 `_TRUNCATION_WARNING_TEMPLATE`；tool 执行循环后新增 silent cleanup |
| `tests/test_consolidation_orphan.py` | 新增 12 个测试用例 |
| `docs/REQUIREMENTS.md` | 索引表增加 §63、§64 |
| `docs/requirements/s60-s69.md` | 新增 §63、§64 完整需求 |

### 自验收

- ✅ 全量测试通过（1 个 pre-existing 失败，与本次改动无关）
- ✅ §63: consolidation 输入不包含孤儿 tool_result
- ✅ §63: 400 BadRequestError 不再被 retry
- ✅ §64: `_TRUNCATION_WARNING_TEMPLATE` 已更新
- ✅ §64: 静默清理逻辑正确删除 3 条消息
- ✅ §64: 降级场景安全

### §64 Hotfix: Silent cleanup 无限循环修复

**日期**: 2026-03-21

Silent cleanup 代码块在删除 warning + assistant + tool_result 3 条消息后，将 `_warned_this_turn` 重置为 `False`。这导致同一 turn 内 warning 被重复注入 → LLM 重复写 summary → 无限循环。

**修复**: 移除 `_warned_this_turn = False` 赋值，让 `_warned_this_turn` 在 cleanup 后保持 `True`，阻止同一 turn 内再次注入截断预警。

**新增测试**: `test_silent_cleanup_does_not_reset_warned_this_turn` — 通过源码检查确保 cleanup 块不包含 `_warned_this_turn = False`。

---

## Phase 60: Tool 配对切割修复 + Warning 频率控制 (§65) ✅

**日期**: 2026-03-21
**需求**: §65
**分支**: feat/batch-20260320-plan-core

### 背景

两个问题在长 session 中叠加导致 consolidation 效率低下：
1. `_find_tool_aligned_cut` 把 tool result 当不合法切点回退，连续多个 tool results 时切点一路退到底
2. 截断预警没有频率控制，跨 turn 时重复注入

### 任务清单

- [x] **T60.1** `agent/loop.py` — `_find_tool_aligned_cut` 去掉 `role=="tool"` 回退
- [x] **T60.2** `agent/loop.py` — warning 增加 `last_warning_msg_index` 频率控制
- [x] **T60.3** 确认 Step 2 (warning) 在 Step 3 (consolidation) 之前
- [x] **T60.4** 检查其他 tool 配对逻辑（get_history、_trim_incomplete_tool_tail、memory.py）— 均无类似 bug
- [x] **T60.5** `tests/test_tool_aligned_cut.py` — 新增 15 个测试用例

### 改动文件

| 文件 | 改动 |
|------|------|
| `nanobot/agent/loop.py` | `_find_tool_aligned_cut` 修复 + warning 频率控制 |
| `tests/test_tool_aligned_cut.py` | 新增 15 个测试用例 |
| `docs/REQUIREMENTS.md` | 索引表增加 §65 |
| `docs/requirements/s60-s69.md` | 新增 §65 完整需求 |

### 自验收

- ✅ 全量测试通过（900 passed, 1 pre-existing failure unrelated）
- ✅ `_find_tool_aligned_cut`: tool result 作为最后一条不再回退
- ✅ `_find_tool_aligned_cut`: assistant(tool_calls) 作为最后一条仍正确回退
- ✅ warning 频率控制: `last_warning_msg_index` 正确存储和检查
- ✅ Step 2/3 顺序正确（先 warning 再 consolidation）
- ✅ 其他 tool 配对逻辑检查通过，无类似 bug

---

## Phase 61: Consolidation Pipeline 重写 (§66) ✅

**日期**: 2026-03-22
**需求**: §66（`requirements/s60-s69.md`）
**Commit**: `2c60460`

### 背景

对 4 天（03-18 ~ 03-21）的 127 次 consolidation 调用进行数据分析，发现成功率仅约 56.7%。主要失败原因：Primary path 传入 20+ 个 session tools 导致 LLM 调错工具或不调（52.7%）、Fallback path max_tokens 默认值混乱导致截断（25.5%）、消息切片 tool_use/tool_result 配对断裂导致 API 400（Fix 6/6b）、API 超时无重试（Fix 5/7/9）。

经历 v1~v10 共 10 轮测试迭代，从逐步叠 Fix（v1~v8）到综合 review + pipeline 架构重写（v9~v10），最终将 `consolidate()` 从单一函数重构为 8 步 pipeline + 5 个 helper。

### 任务清单

- [x] **T61.1** `agent/memory.py` — 完全重写 `consolidate()` 为 8 步 pipeline
- [x] **T61.2** `agent/memory.py` — 新增 `_select_messages()` helper (Step 1)
- [x] **T61.3** `agent/memory.py` — 新增 `_sanitize_slice()` helper (Step 2, 3 遍清理)
- [x] **T61.4** `agent/memory.py` — 新增 `_build_prompt()` helper (Step 3, 只传 save_memory)
- [x] **T61.5** `agent/memory.py` — 新增 `_call_with_retry()` helper (Step 4, 统一指数退避)
- [x] **T61.6** `agent/memory.py` — 新增 `_record_usage()` helper (Step 5, 日志修复)
- [x] **T61.7** `agent/memory.py` — Step 6-8: 响应验证 + 结果保存 + 书签更新
- [x] **T61.8** `agent/memory.py` — 模块级常量: `_CONSOLIDATION_TIMEOUT(read=600s)`, `_MAX_RETRIES=3`, `_CONSOLIDATION_DEFAULT_MAX_TOKENS=16384`
- [x] **T61.9** Dev 环境 v3/v8/v9/v10 验证全部通过（memory_window=15 快速触发）
- [x] **T61.10** Git commit `2c60460`

### 改动文件

| 文件 | 改动 |
|------|------|
| `nanobot/agent/memory.py` | 完全重写，237→529 行（+292 行）。8 步 pipeline + 5 helper + 6 常量 |

### 自验收

- ✅ Dev 环境成功率从 56.7% 提升至 100%（v3/v8/v9/v10 全部成功）
- ✅ LLM 只调 save_memory，不调其他工具
- ✅ `_sanitize_slice` Pass 3 pull-from-keep 正确补入 7 个 tool_results
- ✅ 大 payload 不超时（read=600s，实际耗时 ~200s）
- ✅ 重试机制正确区分 timeout vs rate-limit vs non-retryable

### 已知遗留

- §67 persist_metadata、§68 跨实例状态、Bug1~4 等结构性问题由 §70 统一解决

---

## Phase 62: Consolidation 统一重设计 (§70 R1+R2+R3) ✅

**日期**: 2026-03-23
**需求**: §70（`requirements/s70-s79.md`）
**分支**: `feat/consolidation-redesign`
**Commits**: `5ac6033` (R3) → `e444402` (R1) → `4e1e34f` (R2) → `d05de42` (tests) → `3ba0648` (fix) → `aee13b1` (merge)

### 背景

§66 重写后 consolidation LLM 调用成功率 100%，但 dev 长跑测试暴露了 **trim 操作**的 7 个 Bug。根本原因是异步状态散布在 4 处（模块级变量、Session 对象、JSONL 文件、pending info），且 trim 操作缺少事务性保证。综合分析报告（836 行）确认：补丁越打越多的原因是 "consolidation 异步化后，状态管理仍然是同步思维"。

设计方案用 3 个核心改动替代 7 个独立补丁：R1 ConsolidationState 单例、R2 Safe trim with sanitize、R3 step 8 tool-aligned。

### 任务清单

- [x] **T62.1** `agent/consolidation_state.py` — 新建 ConsolidationState 单例（~80 行）
- [x] **T62.2** `agent/loop.py` — 删除 4 个模块级变量，主循环 Step 1/2/3 改用 `consolidation_state` API
- [x] **T62.3** `agent/loop.py` — 重写 `_trim_consolidated_messages`：archive_cut 实时计算 + sanitize 兜底 + persist 内置
- [x] **T62.4** `agent/loop.py` — 增强 `_find_tool_aligned_cut` Pass 2：向前扩展包含孤儿 tool_results
- [x] **T62.5** `agent/loop.py` — 简化 `_do_mid_turn_consolidation`：成功/失败后立即 persist
- [x] **T62.6** `agent/memory.py` — step 8 tool-aligned 对齐（~5 行）
- [x] **T62.7** 单元测试适配 ConsolidationState 新接口（70 passed）
- [x] **T62.8** Phase 1 集成测试（memory_window=4）— 4 项全部通过
- [x] **T62.9** Phase 2 集成测试（memory_window=20）— 4 次 consolidation 正确（1 失败降级 + 3 成功）
- [x] **T62.10** Git merge `aee13b1`

### Commits

| Commit | 描述 |
|--------|------|
| `5ac6033` | R3: consolidate step 8 tool-aligned boundary |
| `e444402` | R1: ConsolidationState singleton for consolidation lifecycle |
| `4e1e34f` | R2: safe trim with sanitize fallback |
| `d05de42` | fix: adapt tests for ConsolidationState and Pass 2 forward-extension |
| `3ba0648` | fix: adapt test_consolidation_tasks_are_referenced for ConsolidationState |
| `aee13b1` | Merge feat/consolidation-redesign → local |

### 改动统计

**4 files changed, ~215 insertions**

| 文件 | 改动 |
|------|------|
| `nanobot/agent/consolidation_state.py` | **新建** ~80 行。ConsolidationState 单例 + PendingResult + _SessionState |
| `nanobot/agent/loop.py` | 修改 ~100 行。替换 4 个模块级变量；重写 `_trim_consolidated_messages`（实时计算 archive_cut + sanitize 兜底）；增强 `_find_tool_aligned_cut`（Pass 2 向前扩展）；简化 `_do_mid_turn_consolidation`（立即 persist） |
| `nanobot/agent/memory.py` | 修改 ~5 行。step 8 tool-aligned（跳过连续 tool_results） |
| `tests/` | 修改 ~30 行。适配 ConsolidationState 新接口 |

### 三阶段测试结果

| Phase | memory_window | 测试项 | 结果 |
|-------|---------------|--------|------|
| Phase 1 | 4 | 基本触发、multi-tool_call、连续 consolidation、/flush | ✅ 4/4 通过，0 orphan |
| Phase 2 | 20 | 4 次 consolidation（含 1 次失败降级）、异步不阻塞、跨 turn 传递 | ✅ 全部通过，0 orphan |
| Phase 3 | 100 | 生产级验证 | ✅ 通过 |

### 自验收

- ✅ ConsolidationState 单例正确管理跨实例状态（begin/complete/pending 对称）
- ✅ trim 后 sanitize 兜底：所有测试中 sanitized=0（说明 `_find_tool_aligned_cut` Pass 2 已足够精确）
- ✅ 失败降级路径正常：last_consolidated 推进 + trim 执行 + persist 持久化
- ✅ 多工具调用（2-tool、3-tool、40-tool）无 orphan
- ✅ 异步 consolidation 不阻塞后续消息处理
- ✅ 70 个 consolidation 相关单元测试全部通过

---

## Phase 69: Session Summary 增量更新（防遗忘）

> 分支: `feat/summary-preserve-history` | 基于: `local` (31721e9)

### 背景

Session summary 在多次 consolidation 后丢失早期信息（Phase C 3-way 对比验证，invisible facts 保留率从 36.9% 提升至 87.3%）。

### 任务

- [x] **T69.1** Warning 模板改为引导增量更新（方案 C）— commit `2d3d0c2`
- [x] **T69.2** `/session summary` 优先读 summary 文件 — commit `9f133a0`
- [x] **T69.3** Warning 模板拆分为首次/增量两个版本 — commit `f5efd12`
- [x] **T69.4** 需求文档 + DEVLOG 补全 + 合并

### 改动文件

| 文件 | 改动 |
|------|------|
| `nanobot/agent/loop.py` | 模板拆分 + 选择逻辑 + TN 强化 + /session summary 读文件 (+42/-4) |
| `docs/REQUIREMENTS.md` | 索引表增加 §69 |
| `docs/requirements/s60-s69.md` | 新增 §69 完整需求 |
| `docs/DEVLOG.md` | 新增 Phase 69 |

### 自验收

- ✅ Phase C 3-way 对比: 方案 C 最优（invisible facts 保留率 87.3%~100%）
- ✅ 三端（CLI/Web/飞书）行为一致（统一走 `_session_summary` 方法）
- ✅ 首次 summary / 增量更新两种场景模板正确选用
- ✅ 改动独立于 consolidation，无耦合风险

---

## Phase 64: Cron 分区重设计 (§72) ✅

**分支**: `feat/cron-partition`
**需求**: [requirements/cron-partition.md](requirements/cron-partition.md)
**设计文档**: `~/.nanobot/workspace/data/analysis/cron/cron-partition-redesign.md`

### 背景

§71 的分区实现基于 target_session 前缀推断 channel 类型，不可靠。§72 重新设计：
- CronPayload 新增 source_channel 字段，砍掉 deliver/channel/to
- 分区基于 source_channel 判定
- Bug4 防重入守卫
- CLI 拒绝创建 reminder

### 任务清单

- [x] **T64.1** CronPayload 简化 — 新增 source_channel，标记 deprecated 字段
- [x] **T64.2** 分区调度 — JobPartition + classify_job + process_role + 分区过滤
- [x] **T64.3** Bug4 防重入守卫 — _on_timer 过滤已执行 job
- [x] **T64.4** Poll 机制 — _ensure_poll_task 检测外部 jobs.json 变更
- [x] **T64.5** CronTool 改造 — source_channel 推断 + CLI 拒绝 reminder
- [x] **T64.6** GatewayCronExecutor 改造 — 前台/后台 session 切换逻辑
- [x] **T64.6a** _resolve_channel_for_session 三阶段匹配 — Pass 1 精确(跳过cron:) + Pass 2 prefix match + Pass 3 fallback
- [x] **T64.6b** 场景3/4 系统通知 — 切换通知含 reminder 内容，后台执行通知 + channel=cron 静默
- [x] **T64.7** _load_store / _save_store — source_channel 序列化 + 旧数据兼容
- [x] **T64.8** add_job API 改造 — 去掉 deliver/channel/to，新增 source_channel
- [x] **T64.8a** web-chat WorkerCronExecutor.execute_job — channel 改为 "cron"
- [x] **T64.9** Git commit 所有改动

---

## Phase 66: §75 Gateway progress 前缀 + §76 ASR 插件注册

**分支**: `feat/batch-20260325-plan-b`
**需求**: §75 [requirements/s75-gateway-progress-prefix.md](requirements/s75-gateway-progress-prefix.md) + §76 [requirements/s76-gateway-asr-plugin.md](requirements/s76-gateway-asr-plugin.md)

### 背景

- §75: Gateway 中间回复（progress callback）缺少视觉区分，用户难以分辨是中间思考还是最终回复
- §76: 飞书语音消息需要 agent 手动调用 transcribe 脚本，应在 gateway 层自动完成 ASR

### 改动

- `nanobot/agent/loop.py`: progress 回调加 💭 前缀（仅 display，不影响 dump jsonl）
- `nanobot/asr/__init__.py` + `nanobot/asr/registry.py`: ASR 插件注册加载器（新模块）
- `nanobot/agent/loop.py`: `_try_asr()` 方法 + `asr_registry` 参数（AgentLoop）
- `nanobot/cli/commands.py`: gateway 启动时加载 ASR registry

### 关联

- feishu-parser FR-6: `scripts/asr.py` ASR 插件脚本 + 注册 JSON

### 任务清单

- [x] **T66.1** §75 — progress callback 加 💭 前缀
- [x] **T66.2** §76 — ASR registry 模块 (`nanobot/asr/registry.py`)
- [x] **T66.3** §76 — `_try_asr()` 方法 + `asr_registry` 参数
- [x] **T66.4** §76 — gateway 启动时加载 ASR registry (`commands.py`)
- [x] **T66.5** FR-6 — feishu-parser `scripts/asr.py` + `--register`
- [x] **T66.6** 文档更新 (REQUIREMENTS.md, DEVLOG.md, SKILL.md)
