# nanobot 核心 — 需求文档

<!-- 📖 文档组织说明
本需求文档采用"主文件 + 归档子文件"结构：
- **本文件（主文件）**：项目概述 + 全量需求索引表 + Backlog
- **requirements/ 子目录**：按编号分组的完整需求正文归档

🔍 如何查找需求：
1. 在下方"全量需求索引"表中按编号/标题找到对应的归档文件链接
2. 点击链接跳转到归档文件，每个归档文件头部也有本文件索引

📝 如何添加新需求：
1. 先在 Backlog 区域添加条目（`### Backlog #N: 标题`）
2. 决定开发时，分配 §编号，在**最新的归档文件末尾**追加完整需求正文
3. 在本文件的索引表中添加对应行
4. 从 Backlog 中删除该条目

⚠️ 维护规则：
- 索引表必须与归档文件内容保持同步
- 归档文件中的内容一旦写入不再删减，只追加
- Backlog 区域必须始终位于本文件最末尾
-->

> 完整需求详情见 `requirements/` 目录下的归档文件。
> 本文件保留项目概述、全量需求索引、Backlog。

---

## 一、项目概述

nanobot 是一个超轻量级个人 AI 助手框架。本文档记录 `local` 分支上针对个人使用场景的需求改进，这些需求不一定会合入上游。

### 分支策略

```
main     ← 跟上游 HKUDS/nanobot 同步
local    ← 本地自定义改动（基于 main）
```

---

## 全量需求索引

| 编号 | 标题 | 状态 | 归档文件 |
|------|------|------|---------|
| §二 | 已完成需求（2.1~2.6 概要） | ✅ | [requirements/s01-s10.md](requirements/s01-s10.md) |
| §三 | SDK 化改造 | ✅ | [requirements/s01-s10.md](requirements/s01-s10.md) |
| §四 | 实时 Session 持久化 | ✅ | [requirements/s01-s10.md](requirements/s01-s10.md) |
| §五 | 统一 Token 用量记录 | ✅ | [requirements/s01-s10.md](requirements/s01-s10.md) |
| §六 | 三个需求的关联关系 | ✅ | [requirements/s01-s10.md](requirements/s01-s10.md) |
| §七A | 图片存储架构改进 | ✅ | [requirements/s01-s10.md](requirements/s01-s10.md) |
| §七 | 实时 Token 用量记录 | ✅ | [requirements/s01-s10.md](requirements/s01-s10.md) |
| §八 | LLM 调用详情日志 | ✅ | [requirements/s01-s10.md](requirements/s01-s10.md) |
| §九 | 文件访问审计日志 | ✅ | [requirements/s01-s10.md](requirements/s01-s10.md) |
| §十 | 多飞书租户支持 | ✅ | [requirements/s01-s10.md](requirements/s01-s10.md) |
| §十一 | media 参数支持 | ✅ | [requirements/s11-s20.md](requirements/s11-s20.md) |
| §十二 | LLM API 速率限制重试机制 | ✅ | [requirements/s11-s20.md](requirements/s11-s20.md) |
| §十三 | /new 命令重构 | ✅ | [requirements/s11-s20.md](requirements/s11-s20.md) |
| §十四 | 大图片自动压缩 | ✅ | [requirements/s11-s20.md](requirements/s11-s20.md) |
| §十五 | ProviderPool | ✅ | [requirements/s11-s20.md](requirements/s11-s20.md) |
| §十六 | 飞书合并转发消息解析 | ✅ | [requirements/s11-s20.md](requirements/s11-s20.md) |
| §十七 | 飞书通道文件附件发送修复 | ✅ | [requirements/s11-s20.md](requirements/s11-s20.md) |
| §十八 | Gateway 并发执行 | ✅ | [requirements/s11-s20.md](requirements/s11-s20.md) |
| §十九 | /session 状态查询命令 | ✅ | [requirements/s11-s20.md](requirements/s11-s20.md) |
| §二十 | /new 归档方向反转 | ✅ | [requirements/s11-s20.md](requirements/s11-s20.md) |
| §二十一 | LLM 错误响应持久化与前端展示 | ✅ | [requirements/s21-s30.md](requirements/s21-s30.md) |
| §二十二 | AgentLoop 迭代预算软限制提醒 | ✅ | [requirements/s21-s30.md](requirements/s21-s30.md) |
| §二十三 | exec 工具动态超时参数 | ✅ | [requirements/s21-s30.md](requirements/s21-s30.md) |
| §二十四 | spawn subagent 能力增强 | ✅ | [requirements/s21-s30.md](requirements/s21-s30.md) |
| §二十五 | ProviderPool 接口同步防护 | ✅ | [requirements/s21-s30.md](requirements/s21-s30.md) |
| §二十六 | LiteLLMProvider 错误吞没 | ✅ | [requirements/s21-s30.md](requirements/s21-s30.md) |
| §二十七 | 弱网环境 LLM API 稳定性增强 | ✅ | [requirements/s21-s30.md](requirements/s21-s30.md) |
| §二十八 | SpawnTool session_key 传递 | ✅ | [requirements/s21-s30.md](requirements/s21-s30.md) |
| §二十九 | Session 间消息传递机制 | ✅ | [requirements/s21-s30.md](requirements/s21-s30.md) |
| §三十 | Subagent 回报消息 role 修正 | ✅ | [requirements/s21-s30.md](requirements/s21-s30.md) |
| §31 | Bug Fix: unhashable type 'slice' | ✅ | [requirements/s31-s39.md](requirements/s31-s39.md) |
| §32 | Cache Control 策略优化 | ✅ | [requirements/s31-s39.md](requirements/s31-s39.md) |
| §33 | ServiceUnavailableError 误判 | ✅ | [requirements/s31-s39.md](requirements/s31-s39.md) |
| §34 | read_file 大文件保护 | ✅ | [requirements/s31-s39.md](requirements/s31-s39.md) |
| §35 | Subagent 回报消息 role 回归 user | ✅ | [requirements/s31-s39.md](requirements/s31-s39.md) |
| §36 | Spawn follow_up | ✅ | [requirements/s31-s39.md](requirements/s31-s39.md) |
| §37 | Spawn stop | ✅ | [requirements/s31-s39.md](requirements/s31-s39.md) |
| §38 | Spawn status | ✅ | [requirements/s31-s39.md](requirements/s31-s39.md) |
| §39 | SpawnTool 未知参数检查 | ✅ | [requirements/s31-s39.md](requirements/s31-s39.md) |

---

<!-- ═══════════════════════════════════════════════════════════════════════
  ⚠️ BACKLOG 区域 — 必须始终位于本文件最末尾！

  ── 格式规范 ──
  - Backlog 条目使用 **三级标题**：`### Backlog #N: 标题`
  - 使用 `Backlog #N` 编号（非 §编号），N 从 1 递增
  - 条目内容只写：来源、问题描述、初步方案思路、优先级判断
  - **不写**完整的设计方案、影响范围表格、子需求拆解（这些属于正式需求）

  ── 生命周期 ──
  1. 新发现的待办 → 在此追加 `### Backlog #N: 标题`
  2. 决定开发 → 分配 §编号，写成 `## §xx 标题` 正式需求章节，
     插入到最新的归档文件末尾，并更新主文件索引表，
     然后从 BACKLOG 中删除该条目
  3. 开发完成 → 正式需求章节已在归档文件中，无需再动

  ── 禁止事项 ──
  - ❌ 不要在 BACKLOG 条目中使用 §编号（避免与正式需求混淆）
  - ❌ 不要在 BACKLOG 之后追加任何正式需求章节
  - ❌ 不要在 BACKLOG 中原地修改条目为正式需求（应挪出去）
  - ❌ 已完成的条目不要留在 BACKLOG 中（应已挪出或删除）
  ═══════════════════════════════════════════════════════════════════════ -->

## 📋 Backlog（手动维护）

> **⚠️ 本区域必须始终位于文件最末尾。**
>
> Backlog 条目格式：`### Backlog #N: 标题`（三级标题 + 序号，不使用 §编号）。
> 只记录问题描述和初步思路，不写完整设计方案。
> 决定开发时分配 §编号，转为正式需求追加到最新归档文件末尾，并更新索引表。

（当前无 Backlog 条目）

<!-- ⚠️ BACKLOG 结束 — 此行之后不得追加任何内容 -->
