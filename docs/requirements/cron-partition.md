# §72 Cron 分区重设计

**状态**: 🔜 开发中
**分支**: `feat/cron-partition`
**设计文档**: `~/.nanobot/workspace/data/analysis/cron-partition-redesign.md`

## 背景

§71 实现了基于 target_session 前缀的分区路由，但设计存在缺陷：
1. 分区维度不足 — 基于 target_session 前缀推断 channel 类型不可靠
2. CronPayload 暴露路由细节 — `deliver`/`channel`/`to` 是执行层细节
3. Bug4 未修复 — timer + poll 可能同时触发 `_on_timer()` 导致双重执行
4. 执行器能力不匹配 — Gateway 无法操作 web session，反之亦然

## 需求

### R1: CronPayload 简化
- 砍掉 `kind`/`deliver`/`channel`/`to`（保留读取兼容）
- 新增 `source_channel: str | None`（"gateway"/"web"/None）
- 隐式判定：有 target_session → reminder；无 → task

### R2: 分区调度
- `source_channel="gateway"` → GATEWAY 分区
- 其他（"web"/"cli"/None）→ WEB 分区
- 每个进程只执行自己分区的 job
- 保留 scheduler.lock 用于 PUBLIC/fallback

### R3: Bug4 防重入守卫
- `_on_timer()` 过滤已执行的 job（last_run_at_ms >= next_run_at_ms）

### R4: CronTool 改造
- 自动填 source_channel（从 channel 推断）
- CLI 创建 reminder 直接拒绝

### R5: GatewayCronExecutor 改造
- reminder 前台 session：现有逻辑
- reminder 后台 session + 前台空闲：切换前台再执行
- reminder 后台 session + 前台忙：后台静默执行 + 通知用户

### R6: 向后兼容
- 旧 jobs.json 无 source_channel 的 job 归 web 分区
- deprecated 字段保留读取
