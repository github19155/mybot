# Design Principles

This page is the first stop for project-level architectural decisions. Read it before changing runtime, browser, subagent, deployment, or persistence behavior.

## 1. Main Agent orchestrates; subagents execute

The Main Agent is primarily the user-facing coordinator:

- stay responsive to the user;
- understand intent and split work;
- consult project documentation before changing architecture-sensitive behavior;
- discover and choose the right worker;
- track delegated work and summarize results.

Long or specialized work should normally run in subagents so the Main Agent remains available for conversation. Short, immediate actions may still run in the Main Agent when that is simpler.

Mental model:

```text
Main Agent = conversation + orchestration
General    = permanent capable fallback
WorkAgent  = one-task ephemeral customization
Specialist = stable recurring responsibility
Tools      = capabilities
Docs       = project knowledge
Dream      = long-term knowledge + specialist evolution
```

All worker kinds reuse the same SubagentManager/runtime lifecycle. WorkAgent must not become a second Agent framework.

## 2. Understand the existing architecture before repairing it

When a subsystem fails, first identify how it is intended to work. Prefer diagnosing the existing design over creating a parallel workaround.

Examples:

- browser failures -> read `docs/browser.md` and `docs/troubleshooting/browser-runtime.md`;
- container/root/persistence questions -> read `docs/container-root.md` and `docs/runtime-storage.md`;
- source ownership questions -> read `docs/architecture.md`;
- agent structure -> read `docs/agent-architecture-roadmap.md`.

Changing the design is allowed when justified, but the reason and new invariant should be explicit.

## 3. Responsibilities belong in persistent roles; capabilities belong in tools

Subagents have three conceptual worker kinds:

- `general` is permanent and fully capable, used for mixed/unknown work and final fallback;
- WorkAgent is temporary and task-scoped, used when one task needs custom tools/prompt/runtime settings but no persistent role is justified;
- Specialists own stable recurring responsibilities and may have narrower prompts, capabilities, or runtime profiles.

Prefer a matching active Specialist when it materially improves execution. Use WorkAgent for one-off customization. Use `general` when there is no clear Specialist and no task-specific override.

WorkAgent is not persisted, is not Dream-managed, and does not accumulate Specialist usage telemetry. Unspecified WorkAgent runtime settings inherit the current Main runtime, not General's persistent tuning.

Do not create a new Agent type merely because a capability exists. Browser is the first explicit example: General, WorkAgent, or a Specialist may receive Browser capability when appropriate. The browser itself is not a separate Browser Agent.

Keep Specialist capability sets narrow enough to match responsibility. A future server-management responsibility may justify a Specialist role with controlled tools, but not an independent Agent architecture solely because new tools were added.

## 4. Dream may evolve Specialists; the user governs deletion

Repeated task patterns may justify a new Specialist. Dream may create and later refine its own Specialist roles when repeated independent evidence shows that a narrower responsibility, prompt, tool set, or runtime profile is useful.

Dream Specialist state is changed through a restricted role-management capability rather than generic file writes. This is a governance boundary: Dream may create, refine, mark cold, or reactivate its own Specialists, but it cannot delete or disable them.

Candidate evidence should identify the underlying task/source occurrence. Rewording the same task is not independent evidence. Do not optimize from one noisy run and do not grow near-duplicate Specialists.

Meaningful Specialist evolution is versioned and keeps a bounded append-only evolution trail rather than replacing prior history. When a Dream-managed Specialist becomes rarely useful or superseded, mark it `cold` rather than deleting it automatically. Cold roles remain discoverable; the user decides whether they are kept, disabled, consolidated operationally, or deleted.

General is permanent and is never removed or narrowed by Dream. WorkAgent is outside Dream entirely because it has no persistent lifecycle.

## 5. Preserve shared-resource ownership

Stateful shared resources need explicit ownership. The persistent browser is the first important case: AI control and human takeover must refer to the same Chromium session/profile, with one active owner at a time.

Even when several workers have Browser capability, Main should not schedule parallel browser work against the same persistent session. Runtime serialization is a safety net, not a substitute for orchestration.

## 6. Persist intentional runtime state, not accidental container state

Container filesystems are replaceable. Data or dependencies that should survive recreation must live in an intentional persistent location or image layer. See `docs/runtime-storage.md`.

Persistent Specialist state belongs in intentional agent-owned files. WorkAgent state never belongs there because WorkAgent dies with its task.

## 7. Prefer informed flexibility over hard-coded prohibitions

Do not solve every failure mode by forbidding operations globally. Give the agent enough project knowledge to choose the correct path, then add hard runtime constraints only for true safety, security, or shared-resource invariants.
