---
name: cron
description: Schedule reminders and recurring tasks.
---

# Cron (Reminders & Tasks)

Two separate tools for scheduling:

- **`reminder`** — Send messages to existing sessions on a schedule
- **`cron_task`** — Create new agent sessions on a schedule

## Slash Commands

- `/tasks` — List all scheduled tasks
- `/reminders` — List all active reminders

---

## Tool: `reminder`

Schedule messages to be delivered to an existing session.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| action | string | ✅ | `add`, `list`, or `remove` |
| target_session | string | add | Session ID to deliver to (must be self or child, `_` separator) |
| message | string | add | The reminder message |
| name | string | — | Short display name. Auto-generated from message if omitted |
| every_seconds | int | — | Interval in seconds (recurring) |
| cron_expr | string | — | Cron expression like `0 9 * * *` |
| tz | string | — | IANA timezone, **only for cron_expr** |
| at | string | — | ISO datetime for one-time (e.g. `2026-02-12T10:30:00`) |
| job_id | string | remove | Job ID to remove |
| show_all | boolean | — | If true, list shows all reminders (default: only current session's) |

### Isolation

- **Created-by-session**: Only the session that created a reminder can remove it
- **Target validation**: Can only target self, child sessions, or `cron_*` sessions
- **CLI blocked**: Reminders require a persistent process (gateway/web)

### Examples

```python
# One-time reminder
reminder(action="add", message="Meeting in 5 minutes", at="2026-03-30T14:55:00",
         target_session="feishu_ST")

# Recurring reminder
reminder(action="add", name="午休提醒", message="该休息了！",
         cron_expr="0 12 * * 1-5", target_session="webchat_1773591411")

# List my reminders
reminder(action="list")

# List all reminders
reminder(action="list", show_all=true)

# Remove
reminder(action="remove", job_id="abc123")
```

---

## Tool: `cron_task`

Schedule tasks that create new agent sessions for execution.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| action | string | ✅ | `add`, `list`, or `remove` |
| owner | string | add/remove | Namespace owner (e.g. `cil`, `tushare`, `paper`) |
| message | string | add | Task description for the agent |
| name | string | — | Short display name. Auto-generated from message if omitted |
| every_seconds | int | — | Interval in seconds (recurring) |
| cron_expr | string | — | Cron expression like `0 9 * * *` |
| tz | string | — | IANA timezone, **only for cron_expr** |
| at | string | — | ISO datetime for one-time |
| job_id | string | remove | Job ID to remove |

### Isolation

- **Owner-based**: Tasks are grouped by `owner` namespace
- **Remove requires matching owner**: Must provide the correct owner to remove a task

### Examples

```python
# Recurring task
cron_task(action="add", owner="cil", name="CIL daily scan",
          message="Run daily CIL scan", cron_expr="0 9 * * *", tz="Asia/Shanghai")

# One-time task
cron_task(action="add", owner="tushare", message="Fetch latest data",
          at="2026-03-30T18:00:00")

# List all tasks (grouped by owner)
cron_task(action="list")

# Remove (must match owner)
cron_task(action="remove", job_id="abc123", owner="cil")
```

---

## Time Expressions

| User says | Parameters |
|-----------|------------|
| every 20 minutes | every_seconds: 1200 |
| every hour | every_seconds: 3600 |
| every day at 8am | cron_expr: "0 8 * * *" |
| weekdays at 5pm | cron_expr: "0 17 * * 1-5" |
| 9am Shanghai time | cron_expr: "0 9 * * *", tz: "Asia/Shanghai" |
| at a specific time | at: ISO datetime string |

## Timezone

Use `tz` with `cron_expr` for IANA timezone. Without `tz`, server's local timezone is used.
`tz` only works with `cron_expr`. The `at` parameter uses server's local time.

## target_session Format

**Format**: Must use **session_id format** with `_` separator (NOT session_key with `:`).

```
# ✅ Correct — session_id format
target_session="webchat_1773591411"

# ❌ Wrong — session_key format
target_session="webchat:1773591411"
```
