---
name: cron
description: Schedule reminders and recurring tasks.
---

# Cron

Use the `cron` tool to schedule reminders or recurring tasks.

## Three Modes

1. **Reminder** - message is sent directly to user
2. **Task** - message is a task description, agent executes and sends result
3. **One-time** - runs once at a specific time, then auto-deletes

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| action | string | ✅ | `add`, `list`, or `remove` |
| message | string | add | The reminder/task message |
| name | string | — | Short display name (e.g. '午休提醒'). Auto-generated from message if omitted |
| every_seconds | int | — | Interval in seconds (recurring) |
| cron_expr | string | — | Cron expression like `0 9 * * *` |
| tz | string | — | IANA timezone, **only for cron_expr** (e.g. `Asia/Shanghai`) |
| at | string | — | ISO datetime for one-time execution (e.g. `2026-02-12T10:30:00`) |
| job_id | string | remove | Job ID to remove |
| target_session | string | — | Target session to send message to (see below) |

### target_session

Send the cron message to a different session instead of the current one.

**Format**: Must use **session_id format** with `_` separator (NOT session_key with `:`).

**Security**: Only self or child sessions are allowed:
- Self: exact match with current session_id
- Child: subagent sessions spawned by the current session
- Cron sessions: IDs starting with `cron_`

**Examples**:
```
# ✅ Correct — session_id format (underscore separator)
target_session="webchat_1773591411"
target_session="subagent_webchat_1773647406_a94e3367"

# ❌ Wrong — session_key format (colon separator)
target_session="webchat:1773591411"
```

> 💡 For subagent sessions, the full session_id includes the `subagent_` prefix + parent session_id + `_` + 8-char hex suffix.

## Examples

Fixed reminder:
```
cron(action="add", message="Time to take a break!", every_seconds=1200)
```

Named reminder:
```
cron(action="add", name="午休提醒", message="该休息了！站起来活动一下", cron_expr="0 12 * * 1-5")
```

Dynamic task (agent executes each time):
```
cron(action="add", message="Check HKUDS/nanobot GitHub stars and report", every_seconds=600)
```

One-time scheduled task (compute ISO datetime from current time):
```
cron(action="add", message="Remind me about the meeting", at="<ISO datetime>")
```

Timezone-aware cron:
```
cron(action="add", message="Morning standup", cron_expr="0 9 * * 1-5", tz="America/Vancouver")
```

Send to a specific session:
```
cron(action="add", message="Daily status check", cron_expr="0 9 * * *", target_session="webchat_1773591411")
```

List/remove:
```
cron(action="list")
cron(action="remove", job_id="abc123")
```

## Time Expressions

| User says | Parameters |
|-----------|------------|
| every 20 minutes | every_seconds: 1200 |
| every hour | every_seconds: 3600 |
| every day at 8am | cron_expr: "0 8 * * *" |
| weekdays at 5pm | cron_expr: "0 17 * * 1-5" |
| 9am Vancouver time daily | cron_expr: "0 9 * * *", tz: "America/Vancouver" |
| at a specific time | at: ISO datetime string (compute from current time) |

## Timezone

Use `tz` with `cron_expr` to schedule in a specific IANA timezone. Without `tz`, the server's local timezone is used.

**Note**: `tz` only works with `cron_expr`. The `at` parameter always uses the server's local time (parse user's intended time into local ISO datetime).
