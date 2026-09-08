# Design Principles

This page is the first stop for project-level architectural decisions. Read it before changing runtime, browser, subagent, deployment, or persistence behavior.

## 1. Main Agent orchestrates; subagents execute

The Main Agent is primarily the user-facing coordinator:

- stay responsive to the user;
- understand intent and split work;
- consult project documentation before changing architecture-sensitive behavior;
- choose and launch the right specialist subagent;
- track status and summarize results.

Long or specialized work should normally run in subagents so the Main Agent remains available for conversation. Short, immediate actions may still run in the Main Agent when that is simpler.

Mental model:

```text
Main Agent = conversation + orchestration
Subagents  = background execution
Tools      = capabilities
Docs       = project knowledge
```

## 2. Understand the existing architecture before repairing it

When a subsystem fails, first identify how it is intended to work. Prefer diagnosing the existing design over creating a parallel workaround.

Examples:

- browser failures -> read `docs/browser.md` and `docs/troubleshooting/browser-runtime.md`;
- container/root/persistence questions -> read `docs/container-root.md` and `docs/runtime-storage.md`;
- source ownership questions -> read `docs/architecture.md`;
- future agent structure -> read `docs/agent-architecture-roadmap.md`.

Changing the design is allowed when justified, but the reason and new invariant should be explicit.

## 3. Specialist capabilities should stay specialist

Prefer role-specific agents and tools over giving every agent every capability.

Planned examples include `coder`, `debugger`, `tester`, `browser-operator`, and future `server-admin` roles.

## 4. Preserve shared-resource ownership

Stateful shared resources need explicit ownership. The persistent browser is the first important case: AI control and human takeover must refer to the same Chromium session/profile, with one active owner at a time.

## 5. Persist intentional runtime state, not accidental container state

Container filesystems are replaceable. Data or dependencies that should survive recreation must live in an intentional persistent location or image layer. See `docs/runtime-storage.md`.

## 6. Prefer informed flexibility over hard-coded prohibitions

Do not solve every failure mode by forbidding operations globally. Give the agent enough project knowledge to choose the correct path, then add hard runtime constraints only for true safety, security, or shared-resource invariants.
