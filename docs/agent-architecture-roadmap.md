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
  - Keep one shared Subagent runtime/lifecycle for every worker kind.
  - Use General as the permanent fallback, WorkAgent for one-off task-specific customization, and Specialists for stable recurring responsibilities.
  - Keep responsibilities in roles and capabilities in tools.

- **Docs = project knowledge**
  - Architecture-sensitive work should consult the relevant docs before changing runtime topology.
  - Prefer informed agent decisions over broad hard-coded restrictions.

- **Dream = observation + analysis + proposals**
  - Learn recurring task patterns from durable evidence across Dream cycles.
  - Produce structured recommendations when repetition suggests a Specialist or other durable change may be useful.
  - Persist only Dream-owned run/cursor state and proposal records; do not directly modify formal memory, Specialist definitions, configuration, or external systems.
  - Proposal execution remains in the existing Main/Runtime path and subject to user authorization and governance.
  - Never manage WorkAgent because WorkAgent is non-persistent by definition.

## Worker model

Subagents have three conceptual worker kinds:

- `general` — permanent, fully capable fallback for mixed, cross-domain, unknown, or uncategorized work. It cannot be disabled or removed.
- `work` — ephemeral WorkAgent snapshot for one task. Main may customize tools, prompt, model/preset, thinking, temperature, timeout, or context. It is never written to role state, never accumulates specialist usage telemetry, and disappears after the task.
- specialists — persistent workers with a stable recurring responsibility, prompt, capability set, or runtime profile.

Built-in specialists include `researcher`, `planner`, `coder`, `debugger`, `tester`, `writer`, and `analyst`. Users or the governed Main/Runtime execution path may define additional roles; Dream may only recommend such changes from repeated task patterns.

A role answers **what recurring responsibility should this worker own?** A tool answers **what capability can this worker use?** WorkAgent is the bridge for one-off customization that does not justify a persistent role.

All three worker kinds reuse the same SubagentManager admission, task tracking, status, steer/stop, execution, result delivery, and AgentRunner path. WorkAgent must not grow a separate Agent framework or duplicate that lifecycle.

## Main-Agent delegation policy

The goal is knowledge-guided orchestration: make project architecture and current worker capabilities easy for Main to discover, then let it choose intelligently.

Typical routing:

```text
conversation / tiny action                -> Main Agent
clear stable specialist responsibility    -> matching active Specialist
one-off task needs custom capability/runtime -> WorkAgent
mixed / unknown / no clear match          -> General
browser work                               -> suitable worker + Browser tools
```

More precisely for `subagent run`:

```text
role supplied
  -> persistent General/Specialist role, with explicit per-run runtime overrides allowed

role omitted + no task override
  -> General

role omitted + any task override
  -> WorkAgent
```

Task overrides include `description`, `system_prompt`, `tools`, `model`, `model_preset`, `thinking`, `temperature`, `timeout_seconds`, and `context`.

WorkAgent does not inherit General's persistent prompt/model/generation tuning. Unspecified WorkAgent runtime settings continue from the current Main runtime. Model-specific Prompt Prefix remains global and is prepended for Main, General, WorkAgent, or Specialist whenever that model is used.

Main owns the user conversation and final synthesis. Long or independent work should normally use background subagents so Main remains available while workers run.

Main should use current role discovery rather than assuming the Specialist list is static: users and governed proposal execution may add or refine roles over time. Roles marked `cold` remain discoverable but should not be preferred unless their specialization is still the best match or the user asks for them.

Hard runtime routing should be reserved for real safety, security, or shared-resource invariants, not used as a substitute for project knowledge.

## Browser design

Browser is a **tool capability**, not a dedicated Browser Agent.

General has Browser available as part of its broad fallback capability set. WorkAgent may receive Browser for one task. A focused Specialist receives Browser when that capability belongs to its recurring responsibility.

Because nanobot uses one persistent Chromium/profile, browser state is shared. Main is responsible for avoiding parallel browser workers against the same session; isolated worker tool registries do not make concurrent browser tasks safe.

Human takeover keeps priority until control is returned. AI and human must operate the same persistent Chromium/profile rather than creating a parallel browser environment.

## Specialist evolution with Dream

Dream observes evidence and proposes Specialist evolution; it does not own or directly mutate Specialist business state. Specialist runtime state is separated from reusable skills and kept in three canonical workspace files:

- `agents/roles.json` — persistent Specialist definitions.
- `agents/role_candidates.json` — compact cross-Dream evidence for responsibilities that may deserve a Specialist.
- `agents/role_usage.json` — runtime-generated launch/recency telemetry for persistent roles.

WorkAgent never appears in those files.

Dream does not edit these files as a way to execute proposals. It may persist Dream-owned control-plane state and structured proposal/evidence records required by its observation cycle. Saving that state is bookkeeping, not permission to apply the recommended business-state mutation. Creating, updating, cooling, reactivating, disabling, or deleting a Specialist remains an action for the existing Main/Runtime execution path under the applicable user authorization boundary.

There is no separately maintained role manifest: the current role state is the source of truth, so the system does not create a second derived index that can drift.

Conceptual lifecycle:

```text
first credible occurrence
        ↓
Dream records / analyzes evidence
        ↓
independent occurrence repeats the responsibility
        ↓
Dream compares General + existing Specialists
        ↓
Dream emits a structured Specialist proposal when materially useful
        ↓
Main/Runtime evaluates and, when authorized, applies the proposal
        ↓
real Main dispatches accumulate usage/history
        ↓
Dream reviews repeated evidence
        ├─ propose prompt / description refinement
        ├─ propose tool-set changes
        ├─ propose model / thinking / context / timeout tuning
        ├─ propose reactivating a useful cold Specialist
        └─ propose marking a rarely useful / superseded role cold
                         ↓
             governed execution applies accepted changes
                         ↓
                    user decides deletion
```

Candidate evidence is evidence memory only. Main never routes to it. Candidate observations should carry a stable occurrence/source identity so repeated wording from one underlying task cannot satisfy the repeated-evidence threshold by itself.

Dream should not recommend optimizing a role from one noisy run. When an accepted proposal produces a meaningful change, the governed execution path should append it to a bounded version/evolution trail so a Specialist can improve incrementally instead of losing prior evolution history or being replaced by another near-duplicate role.

Usage counters are advisory: they show launches and recency, not success quality. Corrections and task semantics should come from conversation/history evidence rather than being inferred from a counter alone.

Dream never auto-deletes or directly marks a Specialist cold. Cold is a discoverable governance state; Dream may propose it, the governed execution path may apply it when authorized, and the user decides whether to keep, disable, consolidate operationally, or delete that role.

## Permissions

General is intentionally broad because it is the permanent fallback: when no Specialist matches, it should still be able to complete ordinary worker tasks without another architecture change.

WorkAgent capabilities are selected task-by-task but still intersect with the parent's allowed tools and still exclude `subagent`, so workers cannot recursively create workers.

Focused Specialists should receive only the capabilities useful to their responsibility. Do not turn every Specialist into another full General, and do not create a new Agent class merely because a Specialist needs a new capability. Future sensitive host-management tools can remain explicitly scoped even while General keeps the normal worker capability set.

Dream's authority is narrower than ordinary worker authority: analysis and Dream-owned state/proposal persistence do not grant permission to mutate formal memory, Specialist state, configuration, or external systems. Proposal execution must continue through existing Main, Runtime, and user authorization checks.

## Future platform work

Continue with:

1. Better project-knowledge discovery and delegation guidance for Main.
2. Unified task manager with running / queued / waiting-human / failed / completed states.
3. Richer evidence for Dream Specialist recommendations without turning them into a rigid scoring system.
4. Controlled server-management capabilities and an appropriate Specialist responsibility boundary.
5. Cross-channel notifications, such as starting work in WebUI and receiving completion alerts in WeChat.

## Product goal

```text
Main Agent stays responsive and orchestrates
+
General guarantees a permanent fully capable fallback
+
WorkAgent handles one-off customized execution without persistence
+
Specialists own stable recurring responsibilities
+
Tools provide capabilities such as Browser
+
Dream observes, analyzes, and proposes from repeated evidence
+
Main/Runtime execute accepted proposals within authorization boundaries
+
User retains final governance over consequential changes
+
Project knowledge guides decisions
+
Shared resources preserve explicit ownership
```