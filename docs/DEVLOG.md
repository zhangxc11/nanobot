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
| 50 | Session 父子关系解析统一到核心 (§54) | ✅ | *主文件* |
| 51 | CronTool 改用 session/parents.py 验证 target_session (§53 R2) | ✅ | *主文件* |
| 52 | Subagent Provider 继承 (§56) | ✅ | *主文件* |
| 53 | 飞书语音消息 recognition 提取 (§57) | ✅ | *主文件* |

---

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

- [ ] **T52.1** `nanobot/agent/subagent.py` — 新增 `_resolve_provider()` 辅助方法
- [ ] **T52.2** `nanobot/agent/subagent.py` — `QueuedSpawn` 新增 `provider`/`model` 字段
- [ ] **T52.3** `nanobot/agent/subagent.py` — `spawn()` 调用 `_resolve_provider()`，传给 `_start_subagent_task()`
- [ ] **T52.4** `nanobot/agent/subagent.py` — `_start_subagent_task()` 接收并传递 provider/model
- [ ] **T52.5** `nanobot/agent/subagent.py` — `_run_subagent()` 新增 provider/model 参数，用于 LLM 调用和 usage recording
- [ ] **T52.6** `nanobot/agent/subagent.py` — `_chat_with_retry()` 新增 provider/model 参数
- [ ] **T52.7** `nanobot/agent/subagent.py` — `follow_up()` resume 时调用 `_resolve_provider()`
- [ ] **T52.8** `nanobot/agent/subagent.py` — `_try_dequeue()` 传递 QueuedSpawn 中的 provider/model
- [ ] **T52.9** `tests/test_subagent_provider_inherit.py` — 新增单元测试
- [ ] **T52.10** 全量回归测试通过
- [ ] **T52.11** Git commit

---

## Phase 53: 飞书语音消息 recognition 提取 (§57) ✅

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
