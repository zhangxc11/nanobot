# nanobot 开发日志归档 — Phase 51~55

> 本文件归档 Phase 51~55 的完整开发记录。
> 返回主文件：[DEVLOG.md](../DEVLOG.md)

---

## Phase 51: CronTool 改用 session/parents.py 验证 target_session (§53 R2) ✅

**日期**: 2026-03-16
**需求**: §53 R2
**Commit**: `edcce71`

### 背景

Phase 50 新增了 `nanobot/session/parents.py` 核心模块。本 Phase 将 CronTool `_validate_target_session()` 从子串匹配 (`self._session_id in target_session`) 改为调用 `is_child_of()` 做正式的父子关系验证。

### 任务清单

- [x] **T51.1** `nanobot/agent/tools/cron.py` — import `is_child_of`；`set_context()` 新增 `sessions_dir` 参数；`_validate_target_session()` 改用 `is_child_of()`；`clone()` 复制 `_sessions_dir`
- [x] **T51.2** `nanobot/agent/loop.py` — `_set_tool_context()` 中传入 `sessions_dir=self.workspace / "sessions"`
- [x] **T51.3** `tests/test_cron_service.py` — 更新测试 helper `_make_tool` 支持 `sessions_dir`；subagent/foreign 测试创建 `.jsonl` 文件；新增 `test_validate_target_session_no_sessions_dir_rejects`
- [x] **T51.4** 全量回归 781 passed, 1 skipped ✅

### 改动文件

| 文件 | 改动 |
|------|------|
| `nanobot/agent/tools/cron.py` | `set_context()` 新增 `sessions_dir` 参数；`_validate_target_session()` 用 `is_child_of()` 替代子串匹配；`clone()` 复制 `_sessions_dir` |
| `nanobot/agent/loop.py` | `_set_tool_context()` 传入 `sessions_dir=self.workspace / "sessions"` |
| `tests/test_cron_service.py` | 更新 `_make_tool` helper；新增 `_make_sessions_dir` helper；subagent/foreign 测试改用 `tmp_path`；新增无 sessions_dir 拒绝测试 |

### 决策记录

- **sessions_dir 参数可选且 None 安全**：`set_context()` 中 `sessions_dir` 默认 None，仅当非 None 时更新。`_validate_target_session()` 在 `_sessions_dir` 为 None 时跳过 `is_child_of` 检查，直接拒绝非 self/非 cron 目标——这是安全的降级行为。
- **cron_ 前缀检查在 is_child_of 之前**：避免不必要的文件系统扫描。

---

## Phase 52: Subagent Provider 继承 (§56) ✅

**日期**: 2026-03-16
**需求**: §56（`requirements/s50-s59.md`）
**架构**: §二十九（`architecture/spawn.md`）

### 背景

Gateway 模式下，主 session 通过 per-session override 使用 `anthropic_proxy`，但 subagent 通过 `ProviderPool.chat()` 使用全局默认 `anthropic`。WebChat 下有并发竞态风险。

### 任务清单

- [x] **T52.1** `nanobot/agent/subagent.py` — 新增 `_resolve_provider()` 辅助方法
- [x] **T52.2** `nanobot/agent/subagent.py` — `QueuedSpawn` 新增 `provider`/`model` 字段
- [x] **T52.3** `nanobot/agent/subagent.py` — `spawn()` 调用 `_resolve_provider()`，传给 `_start_subagent_task()`
- [x] **T52.4** `nanobot/agent/subagent.py` — `_start_subagent_task()` 接收并传递 provider/model
- [x] **T52.5** `nanobot/agent/subagent.py` — `_run_subagent()` 新增 provider/model 参数，用于 LLM 调用和 usage recording
- [x] **T52.6** `nanobot/agent/subagent.py` — `_chat_with_retry()` 新增 provider/model 参数
- [x] **T52.7** `nanobot/agent/subagent.py` — `follow_up()` resume 时调用 `_resolve_provider()`
- [x] **T52.8** `nanobot/agent/subagent.py` — `_try_dequeue()` 传递 QueuedSpawn 中的 provider/model
- [x] **T52.9** `tests/test_subagent_provider_inherit.py` — 新增单元测试
- [x] **T52.10** 全量回归测试通过
- [x] **T52.11** Git commit

---

## Phase 53: 飞書语音消息 recognition 提取 (§57) ✅

**日期**: 2026-03-16
**需求**: §57（`requirements/s50-s59.md`）

### 背景

飞书语音消息的 `content` JSON 中包含 `recognition` 字段（语音转文字），但 nanobot 飞书通道代码在处理 `audio` 类型消息时完全没有提取该字段。

### 任务清单

- [x] **T53.1** `feishu.py` 直接 audio 消息处理：提取 `recognition` 字段
- [x] **T53.2** `feishu.py` merge_forward audio 子消息处理：提取 `recognition` 字段
- [x] **T53.3** 新增单元测试
- [x] **T53.4** 全量回归测试通过
- [x] **T53.5** Git commit

### 改动文件

| 文件 | 改动 |
|------|------|
| `nanobot/channels/feishu.py` | 两处 audio 处理后追加 recognition 提取（各 3 行） |
| `tests/test_feishu_audio_recognition.py` | 新增 6 个测试覆盖 recognition 提取 |
