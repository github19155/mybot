# Agent Architecture Roadmap

This document is the architectural baseline for how nanobot should evolve. Keep it updated when the design changes so future development can rely on one shared reference.

## Core direction

- **Main Agent = conversation + orchestration**
  - Stay responsive to the user.
  - Understand intent, split work, choose the right child agent, and summarize results.
  - Avoid owning long-running operational work when a child can do it.

- **Subagents = background execution**
  - Run long or specialized work asynchronously by default.
  - Use role-specific capabilities instead of giving every child every tool.

## Planned specialist agents

- `coder` — code and workspace changes
- `debugger` — diagnosis and focused fixes
- `tester` — verification and test execution
- `browser-operator` — browser automation and human handoff
- `server-admin` — future privileged server-management work

## Browser design

Browser work should move to a dedicated `browser-operator` subagent.

That role should be allowed to use the browser tool family, including operations such as status, navigation, snapshot, click, type, wait, and human handoff.

Because nanobot uses one persistent Chromium/profile, browser access must be treated as a shared exclusive resource.

Planned ownership model:

```text
human
main
subagent:<task_id>
```

Only one owner may actively control the browser at a time. Human takeover keeps priority until control is explicitly returned.

## Main-Agent delegation policy

The long-term goal is to move from prompt-only delegation guidance toward runtime-enforced routing.

Typical routing:

```text
conversation / tiny action -> Main Agent
coding                  -> Coder Agent
debugging               -> Debugger Agent
build / broad tests      -> Tester or Coder Agent
browser work             -> Browser Operator
server maintenance       -> Server Admin Agent
```

The Main Agent should remain available for conversation while background work runs.

## Permissions

Capabilities should be isolated by role.

Do not make every agent simultaneously hold root shell, browser access, and future host-management access. Sensitive capabilities should belong to the narrowest specialist that needs them.

## Future platform work

After browser delegation is solid, continue with:

1. Runtime-enforced delegation for long work.
2. Unified task manager with running / queued / waiting-human / failed / completed states.
3. Dedicated `server-admin` role and controlled host-management design.
4. Cross-channel notifications, such as starting work in WebUI and receiving completion alerts in WeChat.

## Product goal

```text
Main Agent stays responsive
+
Specialist subagents do background work
+
Shared resources have explicit ownership
+
Sensitive capabilities are role-scoped
```
