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
| 38 | Spawn follow_up — 向 subagent 追加消息 (§36) | ✅ | *主文件* |
| 39 | Spawn stop — 主动停止 subagent (§37) | ✅ | *主文件* |
| 40 | Spawn status — 查询 subagent 执行状态 (§38) | ✅ | *主文件* |

---

## Phase 38: Spawn follow_up — 向 subagent 追加消息 (§36) ✅

> 需求: DESIGN_SPAWN_FOLLOWUP.md | 分支: local
> 开始时间: 2026-03-10 | 完成时间: 2026-03-10

### 背景

spawn subagent 当前是"发射后不管"模式。需要支持向已有 subagent 追加消息：
- 运行中的 subagent → inject（注入到执行流，不触发新 turn）
- 已结束的 subagent → resume（从 session 历史恢复，启动新 turn）
- 调用者无需区分，spawn 内部自动判断

### 任务清单

- ✅ **T38.1** `agent/subagent.py` — 新增 `SubagentMeta` dataclass + `_task_meta` 字典
- ✅ **T38.2** `agent/subagent.py` — `spawn()` 创建 meta，`_cleanup` 保留 meta 和 `_session_tasks`
- ✅ **T38.3** `agent/subagent.py` — `_run_subagent()` 增加 `inject_queue` + `resume_messages` 参数，inject checkpoint
- ✅ **T38.4** `agent/subagent.py` — `_run_subagent()` 结束时更新 `meta.status`
- ✅ **T38.5** `agent/subagent.py` — 新增 `_check_ownership()` + `follow_up()` 方法
- ✅ **T38.6** `agent/tools/spawn.py` — 新增 `follow_up` 参数，`execute()` 路由
- ✅ **T38.7** `tests/test_spawn_follow_up.py` — 26 项测试全部通过
  - SubagentMeta: 2 项（默认值/自定义值）
  - 生命周期: 6 项（meta 创建/保留/session_tasks 保留/status completed/max_iterations/failed）
  - 鉴权: 3 项（合法/错误 session/未知 task_id）
  - Inject: 2 项（运行中注入/多条消息 drain）
  - Resume: 5 项（正常恢复/自定义 max_iterations/persist=False 报错/无 SessionManager 报错/多次 resume）
  - 安全: 2 项（错误 session/未知 task_id）
  - SpawnTool: 5 项（参数 schema/description/路由 follow_up/路由 spawn/max_iterations 传递）
  - TaskKeeper: 1 项（resume 注册 task_keeper）
- ✅ **T38.8** 全量回归: 513 passed, 1 skipped, 0 failed
- ✅ **T38.9** `docs/REQUIREMENTS.md` — 新增 §36 章节
- ✅ **T38.10** `docs/ARCHITECTURE.md` — 新增 §十五 章节
- ✅ **T38.11** DEVLOG.md 本条记录

### 影响文件

| 文件 | 改动 |
|------|------|
| `agent/subagent.py` | SubagentMeta、_task_meta、inject checkpoint、follow_up()、_check_ownership() |
| `agent/tools/spawn.py` | follow_up 参数、execute() 路由、description 更新 |
| `tests/test_spawn_follow_up.py` | 26 项新测试 |
| `docs/DESIGN_SPAWN_FOLLOWUP.md` | 设计文档 |
| `docs/REQUIREMENTS.md` | §36 章节 |
| `docs/ARCHITECTURE.md` | §十五 章节 |
| `docs/DEVLOG.md` | Phase 38 记录 |

---

## Phase 39: §37 Spawn stop — 主动停止 subagent

> 需求：§37 | 架构：§十六 | 日期：2026-03-10

### 任务清单

- [x] **T39.1** `agent/subagent.py` — 新增 `_stop_flags: set[str]`，在 `__init__` 初始化
- [x] **T39.2** `agent/subagent.py` — 新增 `stop_subagent()` 方法（鉴权 + 状态判断 + cancel + 持久化）
- [x] **T39.3** `agent/subagent.py` — `_run_subagent()` CancelledError 处理中区分 stop vs 其他 cancel，stop 时跳过 announce
- [x] **T39.4** `agent/tools/spawn.py` — 新增 `stop` 参数，`execute()` 路由，`parameters` / `description` 更新
- [x] **T39.5** `tests/test_spawn_stop.py` — 19 项测试全部通过
  - StopRunning: 2 项（运行中 stop / 空 reason）
  - StopAlreadyFinished: 3 项（completed / failed / already stopped）
  - StopOwnership: 2 项（错误 session / 未知 task_id）
  - StopNoAnnounce: 2 项（stop 不 announce / 普通 cancel 仍 announce）
  - StopThenResume: 1 项（stop 后 follow_up resume）
  - StopPersistence: 2 项（persist=True 写入 / persist=False 不写入）
  - StopFlagsCleanup: 1 项（flag 清理）
  - SpawnToolStop: 5 项（参数 schema / description / 路由 stop / 空 task / 互斥检查 / 正常 spawn）
- [x] **T39.6** 全量回归: 532 passed, 1 skipped, 0 failed
- [x] **T39.7** Git commit: `0fd78a4`

---

## Phase 40: Spawn status — 查询 subagent 执行状态 (§38)

> 需求：§38 | 架构：§十七 | 日期：2026-03-10

### 任务清单

- [x] **T40.1** `agent/subagent.py` — SubagentMeta 新增 4 字段：`created_at`, `finished_at`, `current_iteration`, `last_tool_name`
- [x] **T40.2** `agent/subagent.py` — `spawn()` 中设置 `created_at`；`_run_subagent()` 中同步 `current_iteration`、`last_tool_name`；状态变更时设置 `finished_at`
- [x] **T40.3** `agent/subagent.py` — 新增 `get_status()` 和 `list_subagents()` 方法
- [x] **T40.4** `agent/tools/spawn.py` — 新增 `status` 参数，`execute()` 路由，互斥检查扩展，`parameters` / `description` 更新
- [x] **T40.5** `tests/test_spawn_status.py` — 34 项测试全部通过
  - SubagentMeta: 2 项（默认值/自定义值）
  - get_status: 5 项（运行中/已完成/无 last_tool/鉴权错误 session/未知 task_id）
  - list_subagents: 5 项（空列表/单个/多个排序/过滤 session/无 last_tool/长 label 截断）
  - FieldUpdates: 6 项（created_at/iteration+last_tool/completed/failed/max_iterations/stopped 的 finished_at）
  - ResumeResets: 1 项（resume 重置 §38 字段）
  - SpawnToolStatus: 9 项（schema/description/路由 list/路由 task_id/未知 id/错误 session/互斥×3/正常 spawn）
  - UnknownParamRejection: 4 项（单个/多个/阻止 spawn/已知参数正常）
- [x] **T40.6** 全量回归: 566 passed, 1 skipped, 0 failed
- [x] **T40.7** Git commit: `16a4c96` (§38) + `15b1c7d` (§39)
- [x] **T40.8** Backlog §39: SpawnTool.execute() 未知参数检查（`**kwargs` 非空时报错）
- [x] **T40.9** 全量回归: 566 passed, 1 skipped, 0 failed; Git commit
