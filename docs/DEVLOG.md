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
| 41 | §40 SubagentManager 单例化 + 跨进程 follow_up 恢复 | ✅ | *主文件* |
| 42 | §41-§45 核心层基础改动 (Phase 42) | ✅ | *主文件* |
| 43 | Spawn 并发限制 (§46) | ✅ | *主文件* |

---

## Phase 41: §40 SubagentManager 单例化 + 跨进程 follow_up 恢复 ✅

**日期**: 2026-03-11
**需求**: §40（`requirements/s40-s49.md`）

### 任务清单

- [x] 子需求 1: AgentLoop.__init__ 新增 `subagent_manager` 可选参数
- [x] 子需求 2: web-chat worker.py SubagentManager 单例化（`_get_subagent_manager()`）
- [x] 子需求 3: `_recover_meta` + `_check_ownership` disk fallback
- [x] 子需求 4: `_load_disk_subagents` + `list_subagents` 增强
- [x] 测试: `test_spawn_singleton.py` — 25 项全通过
- [x] 全量回归通过: 591 passed, 1 skipped

### 改动文件

| 文件 | 改动 |
|------|------|
| `nanobot/agent/subagent.py` | `_recover_meta()`, `_load_disk_subagents()`, `_check_ownership()` disk fallback, `list_subagents()` 增强 |
| `nanobot/agent/loop.py` | `__init__` 新增 `subagent_manager: SubagentManager | None = None` 参数 |
| `web-chat/worker.py` | `_get_subagent_manager()` 单例 + `_create_runner()` 透传 |
| `tests/test_spawn_singleton.py` | 新测试文件（25 项测试） |
| `docs/architecture/spawn.md` | §十八 SubagentManager 单例化 + 跨进程恢复 |
| `docs/ARCHITECTURE.md` | 章节索引更新 |

### 设计要点

- **进程内单例化**: web worker 中 `SubagentManager` 提升为模块级单例，跨 HTTP 请求共享 `_task_meta`
- **跨进程恢复**: `_recover_meta()` 按确定性命名规则构造 session key，O(1) 文件 stat 检查
- **批量恢复**: `_load_disk_subagents()` glob 匹配前缀，用于 `list_subagents()`
- **Gateway 模式不受影响**: `subagent_manager=None`（默认）走原有路径

### §40 Hotfix: subagent usage_recorder 丢失 (2026-03-11)

**根因**: `_get_subagent_manager()` 初始实现未传 `usage_recorder`，singleton 的 `usage_recorder=None`，导致所有 subagent 的 usage 数据不再记录到 SQLite。

**修复**:
1. web-chat `worker.py`: `_get_subagent_manager()` 传入 `usage_recorder=UsageRecorder()`
2. nanobot 核心 `loop.py`: 外部传入 `subagent_manager` 且 `usage_recorder=None` 时打 warning 日志
3. 新增 4 项测试（`test_spawn_singleton.py` 25→29）:
   - `test_external_with_usage_recorder` — 外部 manager 带 recorder 时正确使用
   - `test_external_without_usage_recorder_warns` — 外部 manager 无 recorder 时代码不报错
   - `test_default_inherits_usage_recorder` — 默认模式正确传递 recorder
   - `test_default_no_usage_recorder` — 默认模式无 recorder 时 subagents.usage_recorder=None
4. 全量回归: 595 passed, 1 skipped

---

## Phase 42: §41-§45 核心层基础改动 ✅

**日期**: 2026-03-11
**需求**: §41-§45（`requirements/s40-s49.md`）

### 任务清单

- [x] §41 Usage 日志记录补充 provider name 字段
- [x] §42 LLM 连接超时优化 — 拆分 connect/read timeout
- [x] §43 修复轮次超限软提醒 — budget alert 改 user role
- [x] §44 Spawn status 异常诊断字段
- [x] §45 Subagent 返回内容标记隐藏 system prompt
- [x] 新增测试: `tests/test_phase42.py` — 28 项全通过
- [x] 全量回归: 623 passed, 1 skipped

### 改动文件

| 文件 | 改动 |
|------|------|
| `nanobot/usage/recorder.py` | §41: SCHEMA_SQL + _MIGRATION_SQL + record() 新增 provider 字段 |
| `nanobot/providers/litellm_provider.py` | §41: `self.provider_name` 属性; §42: `_LLM_TIMEOUT` → `httpx.Timeout` |
| `nanobot/agent/loop.py` | §41: record() 传 provider; §43: budget alert 改 user role |
| `nanobot/agent/subagent.py` | §41: record() 传 provider; §43: budget alert 改 user role; §44: SubagentMeta 新增 error 字段 + _chat_with_retry task_id + get_status 输出 + resume 重置; §45: _announce_result 添加 `<!-- nanobot:system -->` |
| `tests/test_phase42.py` | 新测试文件（28 项测试） |
| `tests/test_budget_alert.py` | §43: 更新 budget alert 测试（role + content 格式） |
| `tests/test_subagent.py` | §43: 更新 budget alert 检测逻辑（system → user） |

### 设计要点

- **§41**: `getattr(_provider, "provider_name", "")` 兼容 ProviderPool 和 LiteLLMProvider
- **§42**: `httpx.Timeout` 对象拆分 connect(30s)/read(120s)，LiteLLM 原生支持
- **§43**: user role 在对话尾部，与 §32 cache breakpoint #3 兼容；`[System Notice]` 前缀区分
- **§44**: 只记录 LLM 调用异常（_chat_with_retry 层），不记录工具执行异常
- **§45**: HTML 注释 `<!-- nanobot:system -->` 不影响 LLM 行为

---

## Phase 43: Spawn 并发限制 (§46) ✅

**日期**: 2026-03-11
**需求**: §46（`requirements/s40-s49.md`）

### 任务清单

- [x] **T43.1** `nanobot/config/schema.py` — 新增 `SpawnConfig` with `max_concurrency: int = 4`；`Config` 新增 `spawn` 字段
- [x] **T43.2** `nanobot/agent/subagent.py` — 新增 `QueuedSpawn` dataclass；SubagentManager 新增 `_queue`, `_max_concurrency`
- [x] **T43.3** `nanobot/agent/subagent.py` — `spawn()` 检查并发 → 超限入队返回 queued 消息
- [x] **T43.4** `nanobot/agent/subagent.py` — `_try_dequeue()` 出队方法 + task done callback 触发
- [x] **T43.5** `nanobot/agent/subagent.py` — `stop_subagent()` 支持 queued 任务
- [x] **T43.6** `nanobot/agent/subagent.py` — `get_status()` 和 `list_subagents()` 自动显示 queued 状态
- [x] **T43.7** `nanobot/agent/loop.py` + `cli/commands.py` + `sdk/runner.py` — 传递 `max_concurrency` 参数
- [x] **T43.8** `tests/test_spawn_concurrency.py` — 37 项测试全部通过
- [x] **T43.9** 全量回归: 660 passed, 1 skipped
- [x] **T43.10** Git commit

### 改动文件

| 文件 | 改动 |
|------|------|
| `nanobot/config/schema.py` | 新增 `SpawnConfig(Base)` with `max_concurrency: int = 4`；`Config` 新增 `spawn` 字段 |
| `nanobot/agent/subagent.py` | 新增 `QueuedSpawn` dataclass；SubagentManager 新增 `_queue`, `_max_concurrency`, `_running_count` 属性；`spawn()` 并发检查+入队；`_start_subagent_task()` 提取；`_try_dequeue()` 出队方法；`stop_subagent()` 支持 queued；done callback 触发 dequeue |
| `nanobot/agent/loop.py` | 新增 `spawn_max_concurrency` 参数，传递给 SubagentManager |
| `nanobot/cli/commands.py` | 3 处 AgentLoop 实例化传递 `spawn_max_concurrency=config.spawn.max_concurrency` |
| `nanobot/sdk/runner.py` | AgentLoop 实例化传递 `spawn_max_concurrency` |
| `tests/test_spawn_concurrency.py` | 新测试文件（37 项测试） |

### 设计要点

- **并发粒度**: SubagentManager 实例级别（单个父 session 维度）
- **`_running_count` 属性**: 统计 `_running_tasks` 中未 done 的 task 数，比 `len(_running_tasks)` 更准确
- **`_start_subagent_task()`**: 从 `spawn()` 提取出来，供 `_try_dequeue()` 复用
- **done callback 触发 dequeue**: task 完成时 cleanup callback 调用 `_try_dequeue()`，包括 follow_up resume 的 task
- **queued 任务 stop**: 直接从 `_queue` 移除，不创建 asyncio.Task，设置 stopped 状态
- **线程安全**: asyncio 单线程模型，`_queue` 和 `_running_tasks` 无竞态

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
