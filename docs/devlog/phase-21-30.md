# Phase 21-30 归档

## 本文件索引

| Phase | 标题 | 日期 |
|-------|------|------|
| 21 | /new 归档方向反转 + Session 命名简化 | 2026-03-01 |
| 22 | Merge main → local | 2026-03-02 |
| 23 | LLM 错误响应持久化与前端展示 | 2026-03-03 |
| 24 | ProviderConfig preferred_model 字段 | 2026-03-04 |
| 25 | 迭代预算软限制提醒 + exec 动态超时 | 2026-03-06 |
| 26 | spawn subagent 能力增强 | 2026-03-06 |
| 27 | ProviderPool **kwargs 透传 + 接口一致性防护 | 2026-03-07 |
| Hotfix §26 | LiteLLMProvider 错误吞没导致 Retry 失效 | 2026-03-08 |
| 28 | 弱网 LLM API 稳定性增强 | 2026-03-08 |
| 30 | Session 间消息传递机制 (SessionMessenger) | 2026-03-08 |

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
- ✅ **T25.5** 更新 MEMORY.md 项目状态 → ✅ 完成

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

