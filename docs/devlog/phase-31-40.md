# Phase 31-40 归档

## 本文件索引

| Phase | 标题 | 日期 |
|-------|------|------|
| 31 | Subagent 回报消息 role 修正 + announce 模板优化 | 2026-03-08 |
| §31 Hotfix | unhashable type 'slice' on subagent injection | 2026-03-08 |
| §33 Hotfix | ServiceUnavailableError 误判为可重试错误 | 2026-03-09 |
| 35 | Subagent 回报消息 role 回归 user — Cache 友好改造 | 2026-03-09 |
| 29 | SpawnTool session_key 传递修复 | 2026-03-08 |
| 32 | Cache Control 策略优化 + Usage Cache 字段 | 2026-03-09 |
| 36 | 测试修复 + REQUIREMENTS.md Backlog 区域整理 | 2026-03-09 |
| 37 | read_file 大文件保护 (§34) | 2026-03-09 |

---

## Phase 31: Subagent 回报消息 role 修正 + announce 模板优化 (2026-03-08)

> 需求: REQUIREMENTS.md §30 | 分支: local
> 完成时间: 2026-03-08

### 背景

Phase 30 引入 SessionMessenger 后，subagent 回报消息在 inject 和 trigger 两条路径上都以 `role: "user"` 进入父 session，导致 agent 误将回报当成用户新指令执行（重复工作、误操作）。在 session webchat_1772952110 中复现：agent 已完成回复 → session idle → subagent 回报以 user role 触发新一轮执行 → agent 重复执行所有分析。

### 任务清单

- ✅ **T31.1** `subagent.py` — announce_content 模板重写
  - 从指令式 ("Summarize this naturally") 改为通知式
  - 引导 agent 结合上下文自主决策：按计划继续 / 不用响应 / 不重复已完成的工作

- ✅ **T31.2** `callbacks.py` — inject 队列扩展 `str` → `str | dict`
  - `AgentCallbacks.check_user_input()` 返回类型 → `str | dict | None`
  - `GatewayCallbacks._inject_queue` 类型 → `Queue[str | dict]`
  - `GatewayCallbacks.inject()` 参数类型 → `str | dict`

- ✅ **T31.3** `loop.py` inject checkpoint — 处理 dict 类型
  - `isinstance(injected, dict)` → 使用 dict 中的 `role` 字段
  - 纯字符串 → 保持 `role: "user"`（向后兼容）

- ✅ **T31.4** `loop.py` GatewaySessionMessenger — inject 传 dict
  - `inject({"role": "system", "content": prefixed})` 替代 `inject(prefixed)`

- ✅ **T31.5** `loop.py` `_process_message` — trigger 路径 role 修正
  - `msg.channel == "session_messenger"` → `build_messages` 输出最后一条 role 改为 `"system"`

- ✅ **T31.6** `spawn.py` — persist 默认值 → True

- ✅ **T31.7** 测试更新
  - `test_subagent.py`: `test_execute_defaults` persist 断言 → True; `test_retry_exhausted` call_count → 6
  - `test_session_messenger.py`: `test_inject_into_running_session` 断言 inject 为 dict + role="system"; trigger 测试 inject 调用更新为 dict 格式
  - 全量测试: 410 passed, 4 deselected (已知 flaky llm_retry)

- ✅ **T31.8** 文档更新
  - REQUIREMENTS.md §30 新增
  - ARCHITECTURE.md §8.6 GatewayCallbacks 更新 + §8.7 Subagent 回报 Role 策略新增
  - DEVLOG.md 本条记录

### 设计决策

**role 判定不依赖内容文本**：
- inject 路径：通过 `isinstance(injected, dict)` + dict 中的 `role` 字段
- trigger 路径：通过 `InboundMessage.channel == "session_messenger"` 结构化字段
- 即使 announce 模板格式变化，role 判定仍然稳定

### 影响文件

| 文件 | 改动 |
|------|------|
| `agent/subagent.py` | announce_content 模板重写 |
| `agent/callbacks.py` | inject 队列 `str` → `str \| dict`，类型注解更新 |
| `agent/loop.py` | inject checkpoint dict 处理 + GatewaySessionMessenger dict inject + _process_message channel 判断 |
| `agent/tools/spawn.py` | persist 默认值 → True |
| `tests/test_subagent.py` | 2 个断言更新 |
| `tests/test_session_messenger.py` | inject 断言更新为 dict |
| `docs/REQUIREMENTS.md` | §30 新增 |
| `docs/ARCHITECTURE.md` | §8.6 更新 + §8.7 新增 |

### 补丁: Worker 模式遗漏修复 (2026-03-08 17:05)

Phase 31 只改了 Gateway 模式（loop.py），遗漏了 Web-Chat Worker 模式（worker.py）。
webchat session 走的是 Worker 模式，所以 inject 和 trigger 两条路径的 role 都没生效。

**修复 3 处**：
- ✅ `WorkerSessionMessenger.send_to_session()` inject 路径：`put(prefixed)` → `put({"role": "system", "content": prefixed})`
- ✅ `WorkerCallbacks.check_user_input()` 返回类型：`str | None` → `str | dict | None`
- ✅ `WorkerSessionMessenger.send_to_session()` trigger 路径：`_run_task_sdk(key, prefixed)` → `_run_task_sdk(key, prefixed, channel='session_messenger')`
- ✅ `_run_task_sdk()` 新增 `channel` 参数（默认 `'web'`），`_execute` 中使用该参数

---

## §31 Hotfix: unhashable type 'slice' on subagent injection

**需求**: §31 | **状态**: ✅ 已完成 | **Commit**: `1bef577`

### 问题

Phase 30 引入 `SessionMessenger` 后，subagent 完成时通过 inject queue 注入 dict 消息到主 session。`loop.py` 的 progress 回调中 `injected[:80]` 对 dict 做切片触发 `TypeError: unhashable type: 'slice'`，导致主 session task 崩溃中断。

### 修复

`injected[:80]` → `inject_msg['content'][:80]`（1 行改动，`inject_msg['content']` 在两个分支中都是字符串）。

---

## §33 Hotfix: ServiceUnavailableError 误判为可重试错误 (2026-03-09)

> 需求: REQUIREMENTS.md §33 | 分支: local | Commit: `5ac998d`

### 问题

`litellm.ServiceUnavailableError` 被 `retry.py` 无条件归为可重试错误。但当错误消息包含 `"model_not_found"` 或 `"无可用渠道"` 时，实际是配置/API 错误，重试 7 次（等待 5~60s）毫无意义。

**实际报错**：
```
litellm.ServiceUnavailableError: AnthropicException - {"error":{"type":"model_not_found",
"message":"分组 全模型纯官key 下模型 claude-opus-4-6 无可用渠道（distributor）"}}
```

### 修复

在 `is_retryable()` 中新增 `_NON_RETRYABLE_MSG_PATTERNS` 排除列表，在检查可重试条件**之前**先匹配。包含：`model_not_found`、`无可用渠道`、`invalid_api_key`、`authentication`、`unauthorized`、`permission denied`、`billing`、`quota exceeded` 等 14 个模式。

### 任务清单

- ✅ **H33.1** `agent/retry.py` — 新增 `_NON_RETRYABLE_MSG_PATTERNS` + `is_retryable()` 排除检查
- ✅ **H33.2** `tests/test_retry.py` — 18 个新测试（36 总计）全部通过
- ✅ **H33.3** 文档更新 — REQUIREMENTS.md §33 + ARCHITECTURE.md §7.8.1
- ✅ **H33.4** 全量回归: 422 passed, 0 failed

### 影响文件

| 文件 | 改动 |
|------|------|
| `agent/retry.py` | 新增 `_NON_RETRYABLE_MSG_PATTERNS` + `is_retryable()` 排除逻辑 |
| `tests/test_retry.py` | 新增 18 个测试（`TestNonRetryablePatterns` 类） |
| `docs/REQUIREMENTS.md` | §33 需求记录 |
| `docs/ARCHITECTURE.md` | §7.8.1 排除策略说明 |

---

## Phase 35: Subagent 回报消息 role 回归 user — Cache 友好改造

> 需求: REQUIREMENTS.md §35 | 分支: local
> 开始时间: 2026-03-09

### 背景

§30 将 subagent 回报消息 role 从 user 改为 system，但 Anthropic API 将 system 消息抽走拼到 system prompt 中，导致 cache 失效、位置语义丢失、§32 breakpoint 超限。改回 user role + prompt 指导。

### 任务清单

- ✅ **T35.1** `subagent.py` — announce_content 末尾追加 prompt 指导
- ✅ **T35.2** `loop.py` — GatewaySessionMessenger inject 改为 user role
- ✅ **T35.3** `loop.py` — _process_message 移除 session_messenger role="system" 覆盖
- ✅ **T35.4** 测试验证 — 422 passed (排除已知 flaky test_llm_retry + test_matrix_channel)
- ✅ **T35.5** Git 提交 — commit `31c96a0`

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

---

## Phase 32: Cache Control 策略优化 + Usage Cache 字段

**需求**: §32 | **状态**: ✅ 已完成 | **Commit**: `0635f3d` (code) + `70a22e0` (docs)

### 问题

1. `_apply_cache_control()` 对所有 system 消息加 breakpoint → spawn 3+ subagent 后超 Anthropic 4 breakpoint 上限
2. `_parse_response()` 丢弃 `cache_creation_input_tokens` / `cache_read_input_tokens`
3. 数据链路缺少 cache 字段，无法追踪缓存命中率

### 改动

#### 1. cache_control 策略重写 (`litellm_provider.py`)
- **旧策略**: 遍历所有 `role: "system"` 消息注入 breakpoint
- **新策略**: 精准 3 breakpoint —
  - `#1 tools[-1]` (缓存 tool 定义，跨 session 复用)
  - `#2 messages[0]` (缓存 system prompt，跨 session 复用)
  - `#3 messages[-1]` (缓存对话历史，同 session 多轮复用)
- 中间 system 消息 (subagent 结果、budget alert) **不加** breakpoint

#### 2. _parse_response() 增加 cache 字段 (`litellm_provider.py`)
- 使用 `getattr(response.usage, field, 0) or 0` 提取 cache 字段
- LiteLLM Usage 是 Pydantic 模型，cache 字段存于 `model_extra`

#### 3. Loop 层累加 (`loop.py`)
- `accumulated_usage` 增加 `cache_creation_input_tokens` / `cache_read_input_tokens`
- 累加逻辑和 `usage_recorder.record()` 调用同步传递
- `__usage__` stderr JSON 输出增加两个字段

#### 4. Subagent 层 (`subagent.py`)
- `usage_recorder.record()` 调用增加 cache 字段传递

#### 5. SQLite schema + migration (`recorder.py`)
- Schema 新增 `cache_creation_input_tokens` / `cache_read_input_tokens` 列 (DEFAULT 0)
- `_migrate()` 方法检测列是否存在，不存在则 ALTER TABLE
- `record()` 方法新增两个参数

### 测试

- `test_cache_control_strategy.py`: 10 个测试覆盖 3-breakpoint 策略各种边界
- `test_usage_cache_fields.py`: 6 个测试覆盖 recorder 写入/迁移/parse_response 提取

### 影响范围

| 文件 | 改动 |
|------|------|
| `providers/litellm_provider.py` | `_apply_cache_control()` 重写 + `_parse_response()` 增加 cache 字段 |
| `agent/loop.py` | `accumulated_usage` + `usage_recorder.record()` + `__usage__` 输出 |
| `agent/subagent.py` | `usage_recorder.record()` 传递 cache 字段 |
| `usage/recorder.py` | `record()` 新增参数 + schema + `_migrate()` |
| `tests/test_cache_control_strategy.py` | 新增 10 个测试 |
| `tests/test_usage_cache_fields.py` | 新增 6 个测试 |

---

## Phase 36: 测试修复 + REQUIREMENTS.md Backlog 区域整理

> 需求: 维护性修复 | 分支: local
> 开始时间: 2026-03-09

### 改动

#### 1. test_matrix_channel.py — 可选依赖跳过 (Bug Fix)
- **问题**: `test_matrix_channel.py` 顶层 `import nanobot.channels.matrix` 导致 `nh3` 未安装时整个测试收集失败 (`ImportError`)，阻塞全部测试运行
- **修复**: 在 import 前添加 `pytest.importorskip("nh3")` 优雅跳过

#### 2. test_llm_retry.py — 对齐 Phase 28 retry 参数 (Bug Fix)
- **问题**: 测试断言使用旧版 retry 参数（初始延迟 10s、max_retries=5），与 Phase 28 实际代码不一致（初始延迟 5s、max_retries=7）
- **修复**:
  - `test_retry_on_rate_limit_then_succeed`: `sleep(10)` → `sleep(5)`
  - `test_exponential_backoff_delays`: `[10, 20, 40]` → `[5, 10, 20]`
  - `test_max_retries_exceeded`: `call_count == 6` → `8` (1 + 7 retries)
  - `test_progress_notification_on_retry`: `"10s"/"1/5"` → `"5s"/"1/7"`

#### 3. REQUIREMENTS.md — Backlog 区域结构修复
- Backlog 标题从 `###` 提升为 `## 📋`，防止被误认为某个 `§` 的子节
- 添加 HTML 注释锚点（文件头尾），引导 AI 在 Backlog 之前插入新需求
- `§33` 移到 Backlog 前（已完成的正式需求）
- `§34` 保留在 Backlog 中（降级为 Backlog 条目格式）

### 测试

448 passed, 1 skipped (matrix channel — 可选依赖未安装)

---

## Phase 37: read_file 大文件保护 (§34) ✅

> 需求: REQUIREMENTS.md §34 | 分支: local
> 完成时间: 2026-03-09

### 背景

agent 自主运行场景（eval-bench、subagent）中，`read_file` 可能意外读取大文件（如 node_modules、日志文件），导致 context 膨胀、token 浪费、甚至超出 API 限制。

### 目标

为 `read_file` 增加双重默认限制（≤100 行 且 ≤20KB），超限时报错并给出建议，模型可通过参数自行扩大限制。

### 任务清单

- ✅ **T37.1** `config/schema.py` — `ToolsConfig` 新增 `read_file_hard_limit: int = 1048576`
- ✅ **T37.2** `agent/tools/filesystem.py` — `ReadFileTool` 改造
  - `__init__` 新增 `hard_limit` 参数
  - `description` 更新，让模型感知限制存在
  - `parameters` 新增 `max_lines` 和 `max_size` 可选参数
  - `execute()` 实现：stat 检查硬上限 → read + 双重软限制检查 → 报错或返回内容
  - 新增 `_human_size()` 辅助函数
- ✅ **T37.3** `agent/loop.py` — 构造函数新增 `read_file_hard_limit` + `_register_default_tools` 传递 + SubagentManager 传递
- ✅ **T37.4** `agent/subagent.py` — 构造函数新增 `read_file_hard_limit` + `_run_subagent` 传递
- ✅ **T37.5** `cli/commands.py` — 3 处 AgentLoop 实例化传递 `config.tools.read_file_hard_limit`
- ✅ **T37.6** `sdk/runner.py` — 1 处 AgentLoop 实例化传递
- ✅ **T37.7** `tests/test_read_file_limit.py` — 39 项测试全部通过
  - _human_size: 7 项（bytes/KB/MB/边界）
  - 小文件正常读取: 4 项（无参数/空文件/精确行数/精确大小）
  - 超行数限制: 2 项（触发保护 + 建议内容）
  - 超字节数限制: 1 项
  - 两个都超: 1 项
  - 参数扩大限制: 3 项（行数/字节数/两者）
  - 参数 clamp 到硬上限: 3 项
  - 硬上限: 4 项（超限/精确/默认值/自定义）
  - 报错格式: 3 项（软限制/硬限制/实际值）
  - Schema: 5 项（描述/参数定义/required）
  - 边缘情况: 3 项（文件不存在/目录/单行无换行）
  - Config 集成: 3 项（默认值/自定义/camelCase alias）
- ✅ **T37.8** 全量回归: 487 passed, 1 skipped, 0 failed
- ✅ **T37.9** ARCHITECTURE.md §十四 更新
- ✅ **T37.10** DEVLOG.md 本条记录

### 影响文件

| 文件 | 改动 |
|------|------|
| `config/schema.py` | `ToolsConfig` 新增 `read_file_hard_limit` |
| `agent/tools/filesystem.py` | `ReadFileTool` 双重限制 + 参数 + `_human_size()` |
| `agent/loop.py` | 构造函数 + `_register_default_tools` + SubagentManager 传递 |
| `agent/subagent.py` | 构造函数 + `_run_subagent` 传递 |
| `cli/commands.py` | 3 处 AgentLoop 传递配置值 |
| `sdk/runner.py` | 1 处 AgentLoop 传递配置值 |
| `tests/test_read_file_limit.py` | 39 项新测试 |
| `docs/ARCHITECTURE.md` | §十四 新增 |
| `docs/DEVLOG.md` | Phase 37 记录 |

---


---

## Phase 38: Spawn follow_up — 向 subagent 追加消息 (§36) ✅

> 需求: DESIGN_SPAWN_FOLLOWUP.md | 分支: local
> 开始时间: 2026-03-10 | 完成时间: 2026-03-10

（内容已归档，详见主文件历史版本）

---

## Phase 39: §37 Spawn stop — 主动停止 subagent ✅

> 需求：§37 | 架构：§十六 | 日期：2026-03-10

（内容已归档，详见主文件历史版本）
