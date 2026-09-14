# Task Manager

The Task Manager is the durable coordination layer for long-running and multi-worker work. It is not another Agent and it does not execute models or tools itself.

## Goals

- keep Main responsive while workers run;
- allow autonomous work to continue across multiple Main turns;
- represent parent/child tasks, dependencies, waiting-human states, retries, and terminal outcomes outside the LLM transcript;
- wake Main only when a coordination decision or user-visible update is needed;
- keep token use low by exposing compact state rather than replaying the full task history.

## Non-goals

- replacing `SubagentManager` execution;
- creating a second worker lifecycle;
- storing full model transcripts or every tool event in prompts;
- making Main poll worker status repeatedly;
- granting permissions. `PermissionManager` remains the runtime authority.

## Core record

A minimal task record should contain structured fields such as:

```text
id
parent_id
origin_session_key
origin_message_id
kind
worker_role
state
created_at
started_at
updated_at
ended_at
depends_on[]
progress_summary
result_ref
last_error
waiting_reason
```

Recommended states:

```text
queued
running
waiting-human
blocked
completed
failed
cancelled
```

The record should stay intentionally small. Large worker outputs belong in existing session/artifact/result storage and are referenced by `result_ref` rather than copied into the ledger.

## Execution model

`SubagentManager` remains responsible for worker admission, queueing, execution, steering, stopping, and terminal result delivery. The Task Manager observes and indexes that lifecycle.

```text
Main
  -> create/update task intent
  -> dispatch worker through SubagentManager
  -> return user-visible response

SubagentManager
  -> queued/running/progress/completed/failed
  -> Task Manager updates compact state

Task Manager
  -> emits meaningful coordination events
  -> Main receives a new turn only when needed
```

Main must not keep one interactive turn open simply to wait for a task state transition.

## Event-driven wakeups

The default path should be event-driven instead of polling. Useful wake events include:

- a leaf worker completes or fails and its parent has no remaining runnable dependencies;
- all children required for a synthesis barrier are terminal;
- a task enters `waiting-human`;
- a previously blocked task becomes runnable;
- a user explicitly asks for task status or changes task direction.

Routine progress updates should update state without necessarily waking Main. A channel may render lightweight progress directly when appropriate.

## Long autonomous tasks

`wait=false` does not mean Main gives up control of a long task. It means control is represented durably instead of by blocking one model call.

Example:

```text
User: refactor this project, run tests, fix failures, and report when complete

Turn 1
Main:
  - creates root task
  - dispatches coder worker
  - tells the user work has started

Worker completion event
  - coder result is recorded
  - dependency rule dispatches tester or wakes Main for the next decision

Later turn
Main:
  - evaluates test result
  - dispatches debugger if needed
  - returns again without blocking

Final barrier reached
  - Task Manager wakes Main
  - Main reads compact task/result summaries
  - Main sends final synthesis
```

This can continue for many stages without an interactive turn remaining open for the full wall-clock duration.

## Token budget design

The Task Manager should reduce token use relative to synchronous orchestration, not increase it.

Principles:

1. **State is structured, not conversational.** Do not append every state transition to the normal model transcript.
2. **Inject only deltas.** A wake event should carry the task ID, changed state, one compact progress/result summary, and relevant dependency state.
3. **Read details on demand.** Main can request a task snapshot or a specific `result_ref` when a decision requires it.
4. **Do not replay completed children by default.** Final synthesis receives compact child summaries plus explicit result references.
5. **Bound summaries.** `progress_summary`, `last_error`, and terminal summaries should have strict size limits.
6. **No status polling loop.** Background state changes should wake Main only on meaningful transitions.

A normal event payload can be very small:

```json
{
  "task_id": "t_123",
  "state": "completed",
  "summary": "Targeted tests passed; 3 files changed.",
  "result_ref": "subagent:ab12cd34",
  "parent_state": "running",
  "ready_children": []
}
```

The large implementation output stays outside the prompt until Main actually needs it.

## Parent/child aggregation

A parent task should define when child results require Main attention. Common policies:

- `any-terminal`: wake on every child terminal event;
- `all-terminal`: wake only after all selected children finish;
- `failure-fast`: wake immediately on failure, otherwise wait for all;
- `dependency-ready`: wake when a dependent next step becomes runnable;
- `waiting-human`: always wake when user input is required.

The first implementation does not need a generic workflow engine. A small dependency graph plus a few aggregation policies is enough.

## User interaction while tasks run

A running background task must not lock its origin conversation. The user may:

- ask unrelated questions;
- request current task status;
- steer a running worker;
- cancel one task or the entire root task;
- add requirements that become a new task event or child task.

Task state should identify its origin conversation, but conversation availability and task execution are independent lifecycles.

## Main visibility and authority

Main should know what capabilities exist across General, WorkAgent, and Specialists so it can route work. That knowledge should come from role/capability catalog metadata and task snapshots, not from exposing worker-only tools as callable Main function definitions.

The separation is:

```text
capability discovery -> visible to Main
callable Main controls -> visible and executable by Main
worker execution tools -> visible as metadata, executable only by workers
PermissionManager -> final runtime authority
```

## Implementation sequence

A small first version can reuse current SubagentManager status instead of replacing it:

1. define a durable/structured `TaskRecord` and task-state store;
2. map subagent launch/status/completion into task records;
3. add parent/dependency fields and an `all-terminal` aggregation barrier;
4. emit compact task events back into the existing session/message bus;
5. add Main-facing `task status/list/cancel/steer` controls;
6. later extend to waiting-human, cross-channel notification, richer dependency scheduling, and durable restart recovery.

Avoid building a general DAG/workflow platform until real use cases require it.