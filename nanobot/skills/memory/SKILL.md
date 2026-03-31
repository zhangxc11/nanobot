---
name: memory
description: Two-layer memory system with grep-based recall.
always: true
---

# Memory

## Structure

- `memory/MEMORY.md` — Long-term facts (preferences, project context, relationships). Always loaded into your context.
- `memory/HISTORY.md` — Append-only event log. NOT loaded into context. Search it with grep. Each entry starts with [YYYY-MM-DD HH:MM].

## Search Past Events

```bash
grep -i "keyword" memory/HISTORY.md
```

Use the `exec` tool to run grep. Combine patterns: `grep -iE "meeting|deadline" memory/HISTORY.md`

## When to Update MEMORY.md

Write important facts immediately using `edit_file` or `write_file`:
- User preferences ("I prefer dark mode")
- Project context ("The API uses OAuth2")
- Relationships ("Alice is the project lead")

## Auto-consolidation

Old conversations are automatically summarized and appended to HISTORY.md when the session grows large. Long-term facts are extracted to MEMORY.md. You don't need to manage this.

## MEMORY 写入准则

写入 MEMORY.md 前，逐条检查：

1. **六月法则** — 这条信息 6 个月后还有用吗？否则不写
2. **可查即不写** — cron list / git log / todo list 能查到的不写
3. **归属判定** — 只在某个 skill 场景下需要？→ 放那个 skill，不放 MEMORY
   - 不读那个 skill 就不会触发需求 → 放 skill
   - 读 skill 之前就需要知道 → 放 MEMORY
4. **项目 ≤3 行** — 位置 + 仓库 + 详细文档链接
5. **Active Work 只放 topic** — 不放细节/ID/进度
6. **troubleshooting → memory/TROUBLESHOOTING.md** — 错误处理/已知问题不放 MEMORY
7. **事件 → HISTORY.md** — 一次性决策/事件记录不放 MEMORY
8. **不写清单** — Cron ID / Commit Hash / Subagent ID / Watchdog 状态 / Bug 修复记录 / Review 评分
