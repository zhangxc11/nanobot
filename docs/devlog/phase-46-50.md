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
