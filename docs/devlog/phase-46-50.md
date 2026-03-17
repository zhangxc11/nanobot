# nanobot 核心 — 开发日志归档 (Phase 46-50)

> 本文件是从 `docs/DEVLOG.md` 主文件归档的历史 Phase 记录。

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

---

## Phase 47: Inject 队列 Drain 修复 (§50) ✅

**日期**: 2026-03-12
**需求**: inject 队列竞态导致用户消息丢失
**Commit**: `449cdd7`（核心）+ `7d61082`（web-chat 日志）

### 背景

用户在 webchat session 中发送 inject 消息时，恰好与 2 个 subagent 结果同时到达 inject 队列。
由于 `check_user_input()` 每次 checkpoint 只取一条消息，且 LLM 最终文本响应分支没有 checkpoint，
导致用户消息永久留在队列中，task 结束后丢失。

**时间线复原**：
```
10:45:25  LLM → sleep 60              队列: []
10:45:31  subagent_80d6 结果 → 队列    队列: [msg1]
10:45:51  subagent_0d46 结果 → 队列    队列: [msg1, msg2]
10:46:25  sleep完成 → checkpoint取msg1  队列: [msg2]
10:46:25  用户POST inject → 队列       队列: [msg2, msg3_user]
10:46:34  LLM tool_call → checkpoint取msg2  队列: [msg3_user]
10:47:21  LLM 纯文本 → break           队列: [msg3_user] ← 永久丢失
```

### 任务清单

- [x] **T47.1** `nanobot/agent/loop.py` — tool-call checkpoint 从单次改为 while-loop drain
- [x] **T47.2** `nanobot/agent/loop.py` — final-response 分支新增 drain checkpoint + continue
- [x] **T47.3** `tests/test_inject_drain.py` — 5 个新测试
- [x] **T47.4** 全量回归 706 passed ✅
- [x] **T47.5** web-chat 日志截断修复（webserver.py + worker.py，[:80] → 全量）

### 改动文件

| 文件 | 改动 |
|------|------|
| `nanobot/agent/loop.py` | tool-call checkpoint: `while True` drain；final-response: 新增 drain + continue |
| `tests/test_inject_drain.py` | 5 个测试：多消息 drain、单消息回归、late inject 继续、无 inject 正常退出、混合类型 |
| `web-chat/webserver.py` | 2 处日志 `[:80]` → 全量 |
| `web-chat/worker.py` | 3 处日志 `[:80]` → 全量 |

### 决策记录

- **final-response 有 pending 时 continue 而非 break**：用户发了消息就期望 LLM 处理，不能静默丢弃
- **日志全量记录**：日志成本远低于丢失消息后无法排查的代价

---

## Phase 48: Runtime Context 注入 Session ID (§51) ✅

**日期**: 2026-03-12
**需求**: §51（`requirements/s50-s59.md`）
**架构**: §二十八（`architecture/core-loop.md`）
**Commit**: `30ebaf5`

### 背景

Agent 需要可靠获取自己的 session 标识。当前 Runtime Context 只注入 Channel + Chat ID，agent 需手动拼接，但在飞书 routed session 和 subagent 场景下拼接结果不等于实际 session ID。

### 任务清单

- [x] **T48.1** `nanobot/agent/context.py` — `_build_runtime_context` 新增 `session_id` 参数，输出 `Session ID: {session_id}`
- [x] **T48.2** `nanobot/agent/context.py` — `build_messages` 新增 `session_id` 参数，透传到 `_build_runtime_context`
- [x] **T48.3** `nanobot/agent/loop.py` — 两个 `build_messages` 调用点传入 `session_id=key.replace(":", "_")`
- [x] **T48.4** `nanobot/agent/subagent.py` — `_build_subagent_prompt` 中调用 `_build_runtime_context` 传入 `session_id`
- [x] **T48.5** `tests/test_session_id_context.py` — 7 个新测试全部通过
- [x] **T48.6** 全量回归 713 passed, 1 skipped ✅
- [x] **T48.7** Git commit `30ebaf5`

### 改动文件

| 文件 | 改动 |
|------|------|
| `nanobot/agent/context.py` | `_build_runtime_context` + `build_messages` 新增 `session_id` 参数 |
| `nanobot/agent/loop.py` | 两个 `build_messages` 调用点传入 `session_id=key.replace(":", "_")` |
| `nanobot/agent/subagent.py` | `_build_subagent_prompt(session_key)` 传入 subagent session key |
| `tests/test_session_id_context.py` | 7 个新测试 |

---

## Phase 49: Cron name 参数 + 消息格式优化 (§55) ✅

**日期**: 2026-03-16
**需求**: §55（`requirements/s50-s59.md`）
**Commit**: nanobot `db5aaa0` / web-chat `e8bd58f`

### 背景

1. **G1 消息内容重复**：`_add_job()` 中 `name = message[:30]`，当 message ≤ 30 字符时 name == message，导致 executor 端消息中 job.name 和 message 重复显示
2. **G3 文档缺失**：`tz` 参数仅对 `cron_expr` 生效，不对 `at` 生效，但 SKILL.md 和 tool schema 未说明
3. **G5 session 列表无区分度**：所有 cron session 显示为 `[Scheduled Task] Timer finished.`

### 任务清单

- [x] **T49.1** CronTool schema 新增 `name` 参数（可选），LLM 传入时使用，未传时 fallback `message[:50]`
- [x] **T49.2** CronTool `tz` description 改为明确仅对 `cron_expr` 生效
- [x] **T49.3** SKILL.md 补充 tz + at 不兼容说明
- [x] **T49.4** web-chat WorkerCronExecutor `execute_job()` 消息格式简化
- [x] **T49.5** web-chat WorkerCronExecutor `send_to_session()` 前缀简化
- [x] **T49.6** web-chat 前端 `isCronNotification()` 检测逻辑改为 `⏰` 前缀
- [x] **T49.7** web-chat 前端 `parseCronNotification()` 适配新旧两种格式
- [x] **T49.8** 新增 7 个测试
- [x] **T49.9** 全量回归 780 passed, 1 skipped ✅

---

## Phase 50: Session 父子关系解析统一到核心 (§54) ✅

**日期**: 2026-03-16
**需求**: §54（`requirements/s50-s59.md`）
**Commit**: `0266e67`

### 背景

Session 父子关系的解析逻辑在 web-chat 前端 `resolveParent()` (~80 行 TS) 和 CronTool `_validate_target_session()` 各自独立实现，长期会分叉。将启发式逻辑统一到 nanobot 核心，成为 single source of truth。

### 任务清单

- [x] **T50.1** 新建 `nanobot/session/parents.py` — 4 个公共接口
- [x] **T50.2** 更新 `nanobot/session/__init__.py` — 导出新接口
- [x] **T50.3** `tests/test_session_parents.py` — 32 个测试全部通过
- [x] **T50.4** 全量回归 780 passed, 1 skipped ✅
- [x] **T50.5** Git commit `0266e67`

### 改动文件

| 文件 | 改动 |
|------|------|
| `nanobot/session/parents.py` | 新建：`load_manual_overrides`、`resolve_parent`、`build_parent_map`、`is_child_of` |
| `nanobot/session/__init__.py` | 导出 4 个新接口 |
| `tests/test_session_parents.py` | 32 个测试：7 个测试类覆盖 subagent/webchat/manual/root/is_child_of/build_parent_map |

### 决策记录

- **优先级排序确定性**：前端 Set 迭代顺序不确定，Python set 同理。当 priority a 有多个候选时，按长度排序取最短（最可能是 root session），确保结果确定性。
- **subagent parent 不验证存在性**：与前端行为一致，即使 parent session 文件不存在也返回解析结果。
- **不改 CronTool**：CronTool 消费改动属于独立任务（§54 消费方改动），本 Phase 只做核心模块。
