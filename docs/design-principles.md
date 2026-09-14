# Design Principles

This page is the first stop for project-level architectural decisions. Read it before changing runtime, browser, subagent, deployment, or persistence behavior.

## 1. Main Agent orchestrates; Workers execute

The Main Agent is the user-facing control plane:

- stay responsive to the user;
- understand intent and split work;
- judge required capabilities and choose the right Worker;
- coordinate shared resources and delegated work;
- clarify essential ambiguity;
- synthesize Worker results and reply to the user.

Operational execution always belongs to a Worker, regardless of task size. Main does not directly read/search/edit files, run shell commands or code, browse/fetch the web, operate Browser, run builds/tests, or process images/media. A small task is still Worker work; size does not change the ownership boundary.

Main's knowledge and authority are intentionally separate:

```text
Tool Catalog      = capabilities that exist in the system
Worker Capability = tools/capabilities effectively available to a selected Worker
Main Callable     = control-plane tools Main can actually call
```

Knowing that a tool exists or that a Worker can use it does not make that tool callable by Main and does not expand permission.

Mental model:

```text
Main Agent = conversation + orchestration
General    = permanent capable fallback
WorkAgent  = one-task ephemeral customization
Specialist = stable recurring responsibility
Tools      = capabilities
Docs       = project knowledge
Dream      = observation + long-term analysis + proposals
```

All worker kinds reuse the same SubagentManager/runtime lifecycle and one shared Worker execution prompt. WorkAgent must not become a second Agent framework.

All Worker dispatches are asynchronous. After successful dispatch Main ends its current turn promptly; it does not wait or poll for completion. A Worker completion returns through a new Main turn, where Main can synthesize the result or dispatch follow-up work.

## 2. Understand the existing architecture before repairing it

When a subsystem fails, first identify how it is intended to work. Main uses available project knowledge and control-plane discovery to route architecture-sensitive investigation; operational document reading and repository inspection belong to Workers. Prefer diagnosing the existing design over creating a parallel workaround.

Examples:

- browser failures -> `docs/browser.md` and `docs/troubleshooting/browser-runtime.md`;
- container/root/persistence questions -> `docs/container-root.md` and `docs/runtime-storage.md`;
- source ownership questions -> `docs/architecture.md`;
- agent structure -> `docs/agent-architecture-roadmap.md`.

Changing the design is allowed when justified, but the reason and new invariant should be explicit.

## 3. Responsibilities belong in persistent roles; capabilities belong in tools

Workers have three conceptual kinds:

- `general` is permanent and fully capable, used for mixed/unknown work and final fallback;
- WorkAgent is temporary and task-scoped, used when one task needs custom tools/prompt/runtime settings but no persistent role is justified;
- Specialists own stable recurring responsibilities and may have narrower prompts, capabilities, or runtime profiles.

Prefer a matching active Specialist when it materially fits the work. Use WorkAgent for one-off customization. Use `general` when there is no clear Specialist and no task-specific override.

WorkAgent is not persisted, is not Dream-managed, and does not accumulate Specialist usage telemetry. Unspecified WorkAgent runtime settings inherit the current Main runtime, not General's persistent tuning.

Do not create a new Agent type merely because a capability exists. Browser is the first explicit example: General, WorkAgent, or a Specialist may receive Browser capability when appropriate. The browser itself is not a separate Browser Agent.

Keep Specialist capability sets narrow enough to match responsibility. A future server-management responsibility may justify a Specialist role with controlled tools, but not an independent Agent architecture solely because new tools were added.

## 4. Dream proposes Specialist evolution; execution remains governed

Repeated task patterns may justify a new Specialist. Dream may observe durable evidence and propose creating or refining a Specialist when a narrower responsibility, prompt, tool set, or runtime profile appears useful. It does not directly create, modify, cool, reactivate, delete, or disable Specialist definitions.

Dream may save its own cursor/run state and structured proposal records. Those writes are part of Dream's control-plane bookkeeping, not execution of the proposal. Any accepted proposal that changes formal memory, Specialist state, configuration, or an external system must be executed through the governed Main/Runtime path and remain subject to the user's authorization and runtime permission boundaries.

Candidate evidence should identify the underlying task/source occurrence. Rewording the same task is not independent evidence. Do not optimize from one noisy run and do not grow near-duplicate Specialists.

When Specialist evolution is applied by the governed execution path, meaningful changes should remain versioned with a bounded append-only evolution trail rather than silently replacing prior history. A rarely useful or superseded Specialist may be proposed for `cold` state; Dream does not apply that mutation itself. Cold roles remain discoverable, and the user retains final governance over deletion or other consequential changes.

General is permanent and is never removed or narrowed by Dream. WorkAgent is outside Dream entirely because it has no persistent lifecycle.

## 5. Preserve shared-resource ownership

Stateful shared resources need explicit ownership. The persistent browser is the first important case: AI control and human takeover must refer to the same Chromium session/profile, with one active owner at a time.

Even when several Workers have Browser capability, Main should not schedule parallel browser work against the same persistent session. Runtime serialization is a safety net, not a substitute for orchestration.

Concurrent Workers may also share files. Main should partition overlapping mutations or sequence them instead of assuming filesystem isolation.

## 6. Persist intentional runtime state, not accidental container state

Container filesystems are replaceable. Data or dependencies that should survive recreation must live in an intentional persistent location or image layer. See `docs/runtime-storage.md`.

Persistent Specialist state belongs in intentional agent-owned files. WorkAgent state never belongs there because WorkAgent dies with its task.

## 7. Prefer informed flexibility over hard-coded prohibitions

Do not solve every failure mode by forbidding operations globally. Give Main enough capability metadata to choose the correct Worker, and give Workers enough execution guidance to complete assigned work. Add hard runtime constraints only for true safety, security, permission, or shared-resource invariants.
