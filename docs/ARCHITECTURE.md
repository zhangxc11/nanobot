# nanobot 核心 — 架构设计文档

<!-- 📖 文档组织说明
本架构文档采用"主文件 + 模块子文件"结构：
- **本文件（主文件）**：架构总览 + 模块索引 + 章节完整索引
- **architecture/ 子目录**：按功能模块分组的完整架构设计

🔍 如何查找设计：
1. 在"模块索引"表中按功能领域找到对应模块文件
2. 或在"章节完整索引"表中按 §编号精确定位

📝 如何添加新设计：
1. 确定新设计属于哪个模块文件（或需要新建模块文件）
2. 在对应模块文件末尾追加新章节
3. 更新模块文件头部的索引表
4. 更新本文件的"章节完整索引"表

⚠️ 维护规则：
- 主文件不包含设计正文，只做导航
- 模块文件按功能内聚分组，不按时间顺序
- 新增模块文件需同步更新"模块索引"表
-->

> 版本：V2.0 | 最后更新：2026-02-26
> 架构按功能模块拆分，详见 `architecture/` 目录。
> 本文件提供架构全景和模块导航。

---

## 系统架构总览

nanobot 核心是一个可嵌入的 AI Agent 框架，支持多 channel（CLI/Web/飞书/Telegram）接入。

### 核心模块
- **AgentLoop**: 消息处理核心循环，驱动 LLM 调用和工具执行
- **EventCallback**: 实时事件通知机制（持久化/Token/日志）
- **ProviderPool**: 多 LLM Provider 管理，运行时动态切换
- **Gateway/Dispatcher**: 多 channel 并发接入，per-session provider 隔离
- **SpawnTool + SessionMessenger**: 子 agent 生命周期管理 + 跨 session 通信
- **Tools**: exec/read_file/write_file/web_search 等内置工具

### 关键设计原则
- EventCallback 解耦：核心循环不直接依赖持久化/日志实现
- Provider 隔离：每个 session 可独立选择 LLM provider
- 实时持久化：每条消息即时写入，crash-safe
- Cache 优化：Anthropic prompt caching 三断点策略

---

## 模块索引

| 模块 | 文件 | 包含章节 | 行数 | 概要 |
|------|------|---------|------|------|
| 核心循环 | [architecture/core-loop.md](architecture/core-loop.md) | §一~§五 | ~685 | AgentLoop/EventCallback/Session持久化/Token/SDK |
| Provider | [architecture/provider.md](architecture/provider.md) | §七+§十三 | ~300 | ProviderPool 动态切换 + Cache Control 策略 |
| Gateway | [architecture/gateway.md](architecture/gateway.md) | §八 | ~205 | Gateway 并发执行/Dispatcher/Tool Context 隔离 |
| Spawn | [architecture/spawn.md](architecture/spawn.md) | §十一+§十二+§十五~§十八 | ~850 | Spawn/follow_up/stop/status/单例化+跨进程恢复 + SessionMessenger |
| 工具 | [architecture/tools.md](architecture/tools.md) | §六+§九+§十+§十四 | ~435 | 审计日志/session命令/exec超时/read_file保护 |

---

## 章节完整索引

| 原始编号 | 标题 | 所在模块文件 |
|---------|------|-------------|
| §一 | 现有架构概览 | core-loop.md |
| §二 | 架构改造设计（Backlog #6+#7+#8） | core-loop.md |
| §三 | 实施计划 | core-loop.md |
| §三-B | 实时 Token 用量记录 | core-loop.md |
| §四 | 与 Web Chat 的交互 | core-loop.md |
| §五 | 文件变更清单 | core-loop.md |
| §六 | 文件访问审计日志架构 | tools.md |
| §七 | ProviderPool 动态切换 | provider.md |
| §八 | Gateway 并发执行架构 | gateway.md |
| §九 | /session 状态查询命令 | tools.md |
| §十 | 迭代预算软限制 + exec 动态超时 | tools.md |
| §十一 | spawn subagent 能力增强 | spawn.md |
| §十二 | SessionMessenger 跨 Session 消息投递 | spawn.md |
| §十三 | Cache Control 策略 + Usage Cache 字段 | provider.md |
| §十四 | read_file 大文件保护 | tools.md |
| §十五 | Spawn follow_up | spawn.md |
| §十六 | Spawn stop | spawn.md |
| §十七 | Spawn status | spawn.md |
| §十八 | SubagentManager 单例化 + 跨进程恢复 | spawn.md |

---

*本文档将随开发进展持续更新。*
