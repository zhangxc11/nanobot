# nanobot 核心 — 开发工作日志（Phase 41-45 归档）

> 归档自 DEVLOG.md 主文件。

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
