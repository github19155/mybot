# Design Principles

This page is the first stop for project-level architectural decisions. Read it before changing runtime, browser, subagent, deployment, or persistence behavior.

## 1. Main Agent orchestrates; subagents execute

The Main Agent is the user-facing control plane:

- stay responsive to the user;
- understand intent and split work;
- consult project documentation before changing architecture-sensitive behavior;
- discover available worker roles and capabilities;
- choose, launch, steer, stop, and coordinate the right worker;
- track delegated work and summarize results.

Execution-heavy work belongs to workers. Filesystem inspection or mutation, shell/process execution, web research/fetching, browser control, code changes, builds, and tests should not become direct Main-model execution merely because an individual action is small. Main may directly perform short conversation or orchestration-control actions when no worker execution is involved.

Main needs **discovery visibility without execution authority**. It should be able to understand which worker tools and capabilities exist so it can route work intelligently, but worker-only capabilities should be represented as non-callable catalog/role metadata rather than ordinary callable function schemas. A tool shown to the model as callable invites direct calls and retry loops even if runtime permission later rejects them.

Long or independent work should normally run asynchronously so Main remains available for conversation. Responsiveness is a runtime property, not only a prompt preference: Main should not stay inside one user turn merely to wait for a worker that can report completion later.

Mental model:

```text
Main Agent        = conversation + orchestration control plane
General           = permanent capable fallback worker
WorkAgent         = one-task ephemeral customization
Specialist        = stable recurring responsibility
Task Manager      = durable task lifecycle / dependency state
PermissionManager = capability authority
Tools             = execution/control capabilities
Docs              = project knowledge
Dream             = observation + long-term analysis + proposals
```

All worker kinds reuse the same SubagentManager/runtime lifecycle. WorkAgent must not become a second Agent framework.

## 2. Understand the existing architecture before repairing it

When a subsystem fails, first identify how it is intended to work. Prefer diagnosing the existing design over creating a parallel workaround.

Examples:

- browser failures -> read `docs/browser.md` and `docs/troubleshooting/browser-runtime.md`;
- container/root/persistence questions -> read `docs/container-root.md` and `docs/runtime-storage.md`;
- source ownership questions -> read `docs/architecture.md`;
- agent structure -> read `docs/agent-architecture-roadmap.md`;
- long-running task lifecycle -> read `docs/task-manager.md`.

Changing the design is allowed when justified, but the reason and new invariant should be explicit.

## 3. Responsibilities belong in persistent roles; capabilities belong in tools

Subagents have three conceptual worker kinds:

- `general` is permanent and fully capable, used for mixed/unknown work and final fallback;
- WorkAgent is temporary and task-scoped, used when one task needs custom tools/prompt/runtime settings but no persistent role is justified;
- Specialists own stable recurring responsibilities and should expose only the normal tool set useful to that responsibility.

Prefer a matching active Specialist when it materially improves execution. Use WorkAgent for one-off customization. Use `general` when there is no clear Specialist and no task-specific override.

WorkAgent is not persisted, is not Dream-managed, and does not accumulate Specialist usage telemetry. Unspecified WorkAgent runtime settings inherit the current Main runtime, not General's persistent tuning.

Do not create a new Agent type merely because a capability exists. Browser is the first explicit example: General, WorkAgent, or a Specialist may receive Browser capability when appropriate. The browser itself is not a separate Browser Agent.

Role tool declarations and permission policy have different jobs:

- a role's tool set describes what that worker normally needs for its responsibility;
- `PermissionManager` defines what that subject is actually authorized to do and remains the hard runtime authority.

Do not use a broad role tool declaration as a substitute for a clear responsibility boundary, and do not infer authorization from a role name, prompt, or requested tool.

## 4. Background work must not capture the conversation

A background worker owns execution, not the user conversation. Dispatching a worker should not make the originating Main turn wait for the worker merely because the eventual answer depends on its result.

A long autonomous request may span multiple runtime turns:

```text
user request
    -> Main decomposes / dispatches
    -> Main acknowledges and returns
    -> workers continue independently
    -> task state records progress/results
    -> completion or waiting-human event wakes Main
    -> Main coordinates the next step or sends final synthesis
```

This preserves autonomous execution without keeping one model request or one user turn open for minutes. Synchronous child execution may remain an internal runtime primitive for genuinely near-instant checks or non-interactive system paths, but it should not be the normal Main orchestration mechanism.

## 5. Task lifecycle belongs outside model context

Long-running task state must not live only in an LLM transcript. A Task Manager should own compact structured records for task identity, parent/child relationships, state, dependencies, progress, result references, errors, and timestamps.

The model should receive only the task information relevant to the current decision. Do not inject the full task ledger or repeated worker output into every prompt. Prefer event-driven wakeups, compact snapshots, and on-demand detail reads. This keeps token cost proportional to actual coordination work rather than total task history.

See `docs/task-manager.md` for the target design.

## 6. Dream proposes Specialist evolution; execution remains governed

Repeated task patterns may justify a new Specialist. Dream may observe durable evidence and propose creating or refining a Specialist when a narrower responsibility, prompt, tool set, or runtime profile appears useful. It does not directly create, modify, cool, reactivate, delete, or disable Specialist definitions.

Dream may save its own cursor/run state and structured proposal records. Those writes are part of Dream's control-plane bookkeeping, not execution of the proposal. Any accepted proposal that changes formal memory, Specialist state, configuration, or an external system must be executed through the existing Main/Runtime path and remain subject to the user's authorization and governance boundaries.

Candidate evidence should identify the underlying task/source occurrence. Rewording the same task is not independent evidence. Do not optimize from one noisy run and do not grow near-duplicate Specialists.

When Specialist evolution is applied by the governed execution path, meaningful changes should remain versioned with a bounded append-only evolution trail rather than silently replacing prior history. A rarely useful or superseded Specialist may be proposed for `cold` state; Dream does not apply that mutation itself. Cold roles remain discoverable, and the user retains final governance over deletion or other consequential changes.

General is permanent and is never removed or narrowed by Dream. WorkAgent is outside Dream entirely because it has no persistent lifecycle.

## 7. Preserve shared-resource ownership

Stateful shared resources need explicit ownership. The persistent browser is the first important case: AI control and human takeover must refer to the same Chromium session/profile, with one active owner at a time.

Even when several workers have Browser capability, Main should not schedule parallel browser work against the same persistent session. Runtime serialization is a safety net, not a substitute for orchestration.

## 8. Persist intentional runtime state, not accidental container state

Container filesystems are replaceable. Data or dependencies that should survive recreation must live in an intentional persistent location or image layer. See `docs/runtime-storage.md`.

Persistent Specialist definitions are config-owned. Worker/task telemetry and other durable runtime state belong in intentional agent-owned stores. WorkAgent role definitions never belong in persistent role state because WorkAgent dies with its task.

## 9. Prefer informed flexibility over hard-coded prohibitions

Do not solve every failure mode by forbidding operations globally. Give Main enough non-callable project, role, and capability metadata to choose the correct path, then add hard runtime constraints only for true safety, security, authority, responsiveness, or shared-resource invariants.