# Nonblocking Subagent Control and Dynamic Roles

Date: 2026-09-08
Status: approved design

## Goal

Make nanobot's main chat session remain responsive while it delegates work to in-process
subagents. The main agent must be able to launch, inspect, steer, and stop its own children,
choose each child's model and thinking effort, and create or modify reusable roles at runtime.

The deployment target is a single Linux server. Subagents do not need to survive a nanobot
restart.

## Non-goals

- Detached worker processes or a distributed task queue.
- Restoring subagents after process restart.
- Persisted child transcripts or `resume`.
- Nested subagents, workflow scripts, missions, schedules, or worktree orchestration.
- A WebUI role editor or Fleet control buttons.
- Compatibility with the old `spawn` tool name.

## Architecture

Replace the `spawn` tool with one LLM-facing `subagent` tool. It exposes these actions:

- `run`
- `status`
- `steer`
- `stop`
- `role.list`
- `role.get`
- `role.create`
- `role.update`
- `role.delete`
- `role.reset`

The implementation has three focused responsibilities:

1. `SubagentManager` owns queued and running tasks, concurrency admission, lifecycle state,
   steering, cancellation, bounded history, and completion delivery.
2. `SubagentRoleStore` resolves builtin templates with configuration overrides and uses the
   existing locked, atomic config update path for role mutations.
3. The existing model runtime resolver builds an immutable runtime snapshot for each launch.
   The public `thinking` field maps to the existing internal `reasoning_effort` setting.

Background runs remain `asyncio.Task` instances in the gateway process. Shutdown cancels all
queued and running children. No database, broker, detached runner, or new dependency is added.

## Role model

Keep the existing root-level `subagentRoles` configuration key and expand each role value.
Configuration JSON uses the project's existing camelCase aliases:

```json
{
  "subagentRoles": {
    "backend-coder": {
      "description": "负责 Python 后端实现",
      "systemPrompt": "专注实现后端功能，修改后运行相关测试。",
      "tools": ["read_file", "write_file", "edit_file", "exec"],
      "modelPreset": "coding",
      "thinking": "high",
      "temperature": 0.1,
      "timeoutSeconds": 1800,
      "context": "fork"
    }
  }
}
```

A role may define:

- `description`
- `systemPrompt`
- `tools`
- `model` or `modelPreset`, but not both
- `thinking`
- `temperature`
- `timeoutSeconds`
- `context`: `fresh` or `fork`
- `disabled`

Role names are normalized to lowercase and must match `[a-z][a-z0-9_-]{0,63}`.

The existing seven roles remain builtin templates: `researcher`, `planner`, `coder`,
`debugger`, `tester`, `writer`, and `analyst`. A config entry with a builtin name is a partial
override merged onto that template. A config entry with any other valid name is a custom role.
Creating a custom role requires a non-empty description and system prompt.

`role.delete` sets `disabled: true` for a builtin role so it can be restored. It removes a
custom role from config. `role.reset` is valid for builtin roles and removes their persisted
override, restoring the code-defined template. Running tasks keep the immutable role snapshot
captured when they started; role mutations affect only future launches.

`role.update` cannot rename a role. Renaming is an explicit create-then-delete operation so task
ownership, status history, and builtin reset behavior remain unambiguous.

The effective child tool set is the intersection of the role's requested tools and the current
parent session's allowed tools. The `subagent` tool itself is always removed because nested
delegation is out of scope. A role can reduce authority but cannot increase it.

## Model and generation resolution

The `run` action accepts optional `model`, `model_preset`, `thinking`, `temperature`, and
`timeout_seconds` fields. `model` and `model_preset` are mutually exclusive.

Resolution precedence, strongest first, is:

1. Per-run fields.
2. Resolved role fields.
3. The selected model preset.
4. The parent session runtime.

`thinking` accepts `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, or `adaptive`
and is passed internally as `reasoning_effort`. Provider-specific validation remains in the
provider layer. An unknown role, model preset, or thinking value fails before task admission.

The first version does not add per-role fallback model lists. Model selection remains authoritative through named presets and `ModelRuntimeResolver`; a request does not transparently switch models after admission.

## Tool contract

### Run

Example:

```json
{
  "action": "run",
  "task": "检查登录流程",
  "role": "backend-coder",
  "wait": false,
  "model": "provider/model",
  "thinking": "high",
  "temperature": 0.1,
  "timeout_seconds": 1800,
  "context": "fork"
}
```

`wait` defaults to `false`. A background launch returns immediately with its task ID, state,
role, model, and thinking effort. `wait=true` awaits the same managed task and returns its final
result. A synchronous run can still be cancelled by existing session/runtime cancellation, but
the blocked parent turn cannot issue another tool call until the run returns.

### Status

Without an ID, `status` lists tasks owned by the current parent session. With an exact ID, it
returns task details, including lifecycle state, phase, role, model, thinking, elapsed time,
latest tool, token usage, stop reason, and bounded final output when available.

The LLM-facing tool cannot enumerate or control another parent session's children. The WebUI's
existing read-only API may continue to aggregate instance-wide status for display.

### Steer

`steer` requires an owned task ID and a non-empty message. For a queued task, the message is
appended to its launch brief. For a running task, it is inserted at the next safe runner
iteration boundary using the existing bounded injection queue. Acceptance means the message was
queued for delivery, not that the model followed it.

### Stop

For a queued task, `stop` removes it from admission and records `stopped`. For a running task,
it cancels the managed `asyncio.Task`. Cancellation propagates into active tools. On Linux, an
active shell command must terminate its process group and be awaited so no orphan process is
left behind.

### Role actions

`role.list` returns compact names and descriptions. `role.get` returns one effective role and
identifies builtin defaults versus persisted overrides. Create and update operations validate
the complete resulting role before one atomic config write. Failed validation does not partially
modify in-memory or on-disk configuration.

## Scheduling and lifecycle

Task states are:

```text
queued -> running -> completed
                  -> failed
                  -> stopped
```

Detailed execution phases remain observational fields and are not separate control states.

Limits are configurable and default to:

- 8 running subagents per parent session.
- 16 running subagents across the nanobot instance.

Tasks above either limit remain queued instead of failing. Admission selects the oldest queued
task that satisfies both limits, skipping temporarily ineligible tasks to avoid one saturated
session blocking other sessions. Releasing a slot schedules another admission pass.

The manager retains the most recent 50 terminal task records in memory. Queued and running
records are not evicted. Restart clears all records.

## Context behavior

`context` accepts `fresh` or `fork` and defaults to `fresh`, unless the selected role provides a
different default.

- `fresh` sends the role prompt, project context already allowed by nanobot, and the explicit
  task brief. It does not copy parent conversation history.
- `fork` captures a read-only snapshot of the parent conversation at launch. Later parent
  messages do not appear in the child. Provider-private reasoning blocks are removed through the
  existing portable-message sanitization path before the snapshot is sent to a different child
  runtime.

## Completion delivery and main-session responsiveness

Background completion publishes exactly one result event to the originating session. The event
contains the task identity, terminal state, role, model, thinking effort, and bounded final result
or error. `completed`, `failed`, and `stopped` all produce a terminal notification; a stopped
task carries no successful result.

Session serialization remains authoritative: a completion event never interrupts a main-agent
turn or writes history concurrently. If the session is busy, the result waits in its event queue
and is processed after the current turn. Each event is idempotent by task ID so delivery retries
cannot create duplicate completion turns. A notification-delivery failure is logged and exposed
in task status; it never reruns the child.

## Prompt and WebUI changes

Remove registration, documentation, examples, and system-prompt guidance for `spawn`. The main
agent prompt teaches the unified actions and these policies:

- Prefer `run` with `wait=false`.
- Use `wait=true` only when the current answer requires the child result.
- Use `role.list` for role discovery and `role.get` for full details.
- Use `status`, `steer`, and `stop` instead of blocking for progress.
- Avoid launching duplicate work.

Only role names and one-line descriptions are advertised in the main prompt. Full role prompts,
tools, and model settings are loaded on demand so custom roles do not permanently inflate the
main context.

FleetView remains read-only. It is updated to display the simplified lifecycle states plus role,
model, thinking, current tool, elapsed time, and token usage. The existing `/api/subagents`
endpoint remains read-only and instance-wide.

## Error handling

- Validate role existence, disabled state, model/preset exclusivity, thinking, temperature,
  timeout, context, and requested tools before admission.
- A child exception or timeout marks only that task as `failed` and releases its slot.
- Cancellation marks the task `stopped`, releases its slot, and does not emit a successful result.
- Role mutation during execution cannot alter an existing task snapshot.
- Completion delivery failure is recorded separately from child execution state.
- Shutdown cancels and awaits all queued/running work within the existing shutdown sequence.

## Migration

Existing `subagentRoles` entries that only contain `modelPreset` remain valid as builtin role
overrides. Existing role names and model presets require no manual config migration.

The old `spawn` tool is intentionally removed without an alias. All prompts, tool registry
expectations, tests, and user-facing examples switch to `subagent` in the same change.

## Tests and acceptance

Backend tests cover:

- Background `run` returns before a deliberately slow child completes.
- `wait=true` returns the child result.
- Per-session 8 and global 16 limits queue excess work and admit the oldest eligible task.
- Completion cannot preempt or concurrently mutate an active parent turn.
- Per-run model, preset, thinking, temperature, timeout, and context precedence.
- `fresh` isolation and `fork` snapshot behavior.
- Builtin overrides, disable/delete/reset, custom create/update/delete, reload, and atomic failure.
- Child tool authority is never greater than parent authority.
- Steering queued and running tasks.
- Stopping queued and running tasks and Linux shell process-group cleanup.
- Child failure, timeout, cancellation, and notification failure isolation.
- The old `spawn` tool is absent from registration and prompts.

WebUI tests cover display of `queued`, `running`, `completed`, `failed`, and `stopped`, together
with role, model, and thinking effort.

Acceptance requires that a main session can continue receiving and answering user messages while
background children launch, queue, run, complete, receive steering, or stop. Only an explicit
`wait=true` call may hold the current parent turn open.
