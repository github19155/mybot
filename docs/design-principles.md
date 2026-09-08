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
Subagents  = workers
Tools      = capabilities
Docs       = project knowledge
Dream      = specialist evolution
```

## 2. Understand the existing architecture before repairing it

When a subsystem fails, first identify how it is intended to work. Prefer diagnosing the existing design over creating a parallel workaround.

Examples:

- browser failures -> read `docs/browser.md` and `docs/troubleshooting/browser-runtime.md`;
- container/root/persistence questions -> read `docs/container-root.md` and `docs/runtime-storage.md`;
- source ownership questions -> read `docs/architecture.md`;
- agent structure -> read `docs/agent-architecture-roadmap.md`.

Changing the design is allowed when justified, but the reason and new invariant should be explicit.

## 3. Responsibilities belong in roles; capabilities belong in tools

Subagents have a permanent `general` fallback plus specialists with focused responsibilities. Prefer a matching specialist when it materially improves execution; use `general` for mixed work or when no specialist clearly fits.

Do not create a new Agent type merely because a capability exists. Browser is the first explicit example: an appropriate general or specialist worker may receive browser tools. The browser itself is not a separate Browser Agent.

Keep capability sets narrow enough to match responsibility. A future server-management responsibility may justify a specialist role with controlled tools, but not an independent Agent architecture solely because new tools were added.

## 4. Dream may evolve specialists; the user governs deletion

Repeated task patterns may justify a new specialist. Dream may create and later refine its own specialist roles when repeated evidence shows that a narrower responsibility, prompt, tool set, or runtime profile is useful.

Do not optimize from one noisy run and do not grow near-duplicate specialists. When a Dream-managed specialist becomes rarely useful or superseded, mark it `cold` rather than deleting it automatically. Cold roles remain discoverable; the user decides whether they are kept, disabled, consolidated operationally, or deleted.

The `general` worker is permanent and is never removed by Dream.

## 5. Preserve shared-resource ownership

Stateful shared resources need explicit ownership. The persistent browser is the first important case: AI control and human takeover must refer to the same Chromium session/profile, with one active owner at a time.

Even when several subagent roles have browser capability, Main should not schedule parallel browser work against the same persistent session. Runtime serialization is a safety net, not a substitute for orchestration.

## 6. Persist intentional runtime state, not accidental container state

Container filesystems are replaceable. Data or dependencies that should survive recreation must live in an intentional persistent location or image layer. See `docs/runtime-storage.md`.

## 7. Prefer informed flexibility over hard-coded prohibitions

Do not solve every failure mode by forbidding operations globally. Give the agent enough project knowledge to choose the correct path, then add hard runtime constraints only for true safety, security, or shared-resource invariants.
