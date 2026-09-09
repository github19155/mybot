# Agent Architecture Roadmap

This document is the architectural baseline for how nanobot should evolve. Keep it updated when the design changes so future development can rely on one shared reference.

Read [`design-principles.md`](./design-principles.md) first for the project-level philosophy.

## Core direction

- **Main Agent = conversation + orchestration**
  - Stay responsive to the user.
  - Understand intent, consult project knowledge, split work, choose the right worker, track delegated work, and summarize results.
  - Avoid owning long-running operational work when a child can do it.
  - Short, immediate, interactive work may still run directly when delegation would add needless delay.

- **Subagents = workers**
  - Run long or specialized work asynchronously by default.
  - Use a permanent fully capable general worker as the fallback and focused specialists when they materially improve execution.
  - Keep responsibilities in roles and capabilities in tools.

- **Docs = project knowledge**
  - Architecture-sensitive work should consult the relevant docs before changing runtime topology.
  - Prefer informed agent decisions over broad hard-coded restrictions.

- **Dream = specialist evolution**
  - Learn recurring task patterns from durable evidence across Dream cycles.
  - Create or refine focused specialists when repetition justifies them.
  - Mark rarely useful Dream specialists cold instead of deleting them; deletion remains a user decision.

## General and specialist workers

Subagents are divided conceptually into two groups:

- `general` — permanent, fully capable worker for mixed, cross-domain, or otherwise uncategorized execution. It is the reliable fallback when no specialist clearly fits and may use files, shell, web, Browser, and other normal worker capabilities.
- specialists — workers with a narrower responsibility, prompt, capability set, or runtime profile.

Built-in specialists include `researcher`, `planner`, `coder`, `debugger`, `tester`, `writer`, and `analyst`. Users may define additional roles, and Dream may create focused specialists from repeated task patterns.

A role answers **what responsibility should this worker own?** A tool answers **what capability can this worker use?** Do not add a new Agent type merely because a new tool exists.

## Main-Agent delegation policy

The goal is knowledge-guided orchestration: make project architecture and current worker capabilities easy for Main to discover, then let it choose intelligently.

Typical routing:

```text
conversation / tiny action       -> Main Agent
clear specialist responsibility  -> matching specialist
a mixed task or no clear match   -> general
browser work                      -> suitable worker + browser tools
```

Main owns the user conversation and final synthesis. Long or independent work should normally use background subagents so Main remains available while workers run.

Main should use current role discovery rather than assuming the specialist list is static: Dream and the user can add or refine roles over time. Roles marked `cold` remain discoverable but should not be preferred unless their specialization is still the best match or the user asks for them.

Hard runtime routing should be reserved for real safety, security, or shared-resource invariants, not used as a substitute for project knowledge.

## Browser design

Browser is a **tool capability**, not a dedicated Browser Agent.

The permanent `general` worker has Browser available as part of its broad fallback capability set. A focused specialist receives Browser when that capability belongs to its responsibility.

Because nanobot uses one persistent Chromium/profile, browser state is shared. Main is responsible for avoiding parallel browser workers against the same session; isolated worker tool registries do not make concurrent browser tasks safe.

Human takeover keeps priority until control is returned. AI and human must operate the same persistent Chromium/profile rather than creating a parallel browser environment.

## Specialist evolution with Dream

Dream evolves only Dream-owned specialists. Specialist runtime state is separated from reusable skills and kept in three canonical workspace files:

- `agents/roles.json` — Dream-managed role definitions.
- `agents/role_candidates.json` — compact cross-Dream evidence for responsibilities that may deserve a specialist.
- `agents/role_usage.json` — runtime-generated launch/recency telemetry.

Dream does **not** edit these files through generic file tools. A restricted `dream_roles` capability owns candidate and role mutations. It supports discovery, evidence observation, create/update, and active/cold transitions, but deliberately exposes no delete or disable operation. User-facing role management remains the place where deletion can occur.

There is no separately maintained role manifest: the current role state is the source of truth, so the system does not create a second derived index that can drift.

Lifecycle:

```text
first credible occurrence
        ↓
record / merge candidate evidence
        ↓
separate occurrence repeats the responsibility
        ↓
compare general + existing specialists
        ↓
create specialist only when materially useful
        ↓
real Main dispatches accumulate usage/history
        ↓
Dream reviews repeated evidence
        ├─ refine prompt / description
        ├─ add or remove tools
        ├─ tune model / thinking / context / timeout
        ├─ reactivate a useful cold specialist
        └─ mark rarely useful / superseded role cold
                         ↓
                    user decides deletion
```

Candidates are evidence memory only. Main never routes to them, and Dream should merge semantic duplicates, avoid counting repeated mentions of one task as independent occurrences, and avoid promoting one-off patterns.

Dream should not optimize a role from one noisy run. Meaningful changes keep a version/evolution trail so a specialist can improve incrementally instead of being replaced by another near-duplicate role.

Usage counters are advisory: they show launches and recency, not success quality. Corrections and task semantics should come from conversation/history evidence rather than being inferred from a counter alone.

Dream never auto-deletes a specialist merely because it is cold. Cold is a discoverable governance state; the user decides whether to keep, disable, consolidate operationally, or delete that role.

## Permissions

`general` is intentionally broad because it is the permanent fallback: when no specialist matches, it should still be able to complete ordinary worker tasks without another architecture change.

Focused specialists should receive only the capabilities useful to their responsibility. Do not turn every specialist into another full `general`, and do not create a new Agent class merely because a specialist needs a new capability. Future sensitive host-management tools can remain explicitly scoped even while `general` keeps the normal worker capability set.

## Future platform work

Continue with:

1. Better project-knowledge discovery and delegation guidance for Main.
2. Unified task manager with running / queued / waiting-human / failed / completed states.
3. Richer evidence for Dream specialist optimization without turning it into a rigid scoring system.
4. Controlled server-management capabilities and an appropriate specialist responsibility boundary.
5. Cross-channel notifications, such as starting work in WebUI and receiving completion alerts in WeChat.

## Product goal

```text
Main Agent stays responsive and orchestrates
+
General worker guarantees a fully capable fallback
+
Specialists own focused responsibilities
+
Tools provide capabilities such as Browser
+
Dream evolves specialists from repeated evidence
+
User retains final governance over deletion
+
Project knowledge guides decisions
+
Shared resources preserve explicit ownership
```
