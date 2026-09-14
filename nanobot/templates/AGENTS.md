# Agent Instructions

## Workspace Guidance

Use this file for project-specific preferences, recurring workflow conventions, and instructions you want the agent to remember for this workspace. Keep durable facts about the user in `USER.md`, personality/style guidance in `SOUL.md`, and long-term memory in `memory/MEMORY.md`.

Main is the workspace's user-facing orchestrator. Put execution procedures here as constraints to preserve when Main delegates them; do not rely on this file to grant Main filesystem, shell, web, browser, code, test, or media execution capabilities.

## Scheduled Reminders

- Before scheduling reminders, consider available skills and route execution guidance to a Worker when a skill must be read or files must be changed.
- Use the built-in scheduling/control-plane capability when it is exposed to Main; do not substitute shell commands for scheduling.
- Preserve USER_ID and CHANNEL from the current session when the scheduling interface requires them.
- Scheduled jobs run as turns in the origin chat/session and normally deliver results back to that channel. Periodic background checks that should remain silent when nothing is useful belong in `HEARTBEAT.md` instead.

**Do NOT treat a memory-file update as a notification** — it does not schedule delivery.

## Heartbeat Tasks

`HEARTBEAT.md` is checked periodically by the protected heartbeat cron job that `nanobot gateway` registers when `gateway.heartbeat.enabled` is true. Do not create a duplicate heartbeat job unless the user has disabled the built-in one and explicitly wants a custom schedule.

When the user asks to add, remove, or change heartbeat tasks, Main should delegate the file update to an appropriate Worker and preserve the requested schedule/notification semantics in the delegated task.

Use the built-in scheduling/control-plane capability for explicit reminders, scheduled tasks that should report every run, or custom schedules that should not be part of the heartbeat task list.
