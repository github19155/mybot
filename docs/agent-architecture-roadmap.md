# Agent Architecture Roadmap

This document is the architectural baseline for how nanobot should evolve. Keep it updated when the design changes so future development can rely on one shared reference.

Read [`design-principles.md`](./design-principles.md) first for the project-level philosophy.

## Core direction

- **Main Agent = conversation + orchestration**
  - Stay responsive to the user.
  - Understand intent, split work, judge capabilities, choose Workers, coordinate shared resources, clarify essential ambiguity, and summarize results.
  - Do not perform filesystem, shell, web, Browser, code, build, test, or image/media execution directly, regardless of task size.
  - Dispatch all operational work asynchronously and end the current Main turn after successful dispatch.
  - Process Worker completion in a new Main turn; do not wait or poll for completion.

- **Workers = execution**
  - Keep one shared Subagent runtime/lifecycle and one shared execution contract for every worker kind.
  - Use General as the permanent fallback, WorkAgent for one-off task-specific customization, and Specialists for stable recurring responsibilities.
  - Keep responsibilities in roles and capabilities in tools.
  - Discover and read relevant execution skills in the Worker rather than loading execution skill instructions into Main.

- **Capability visibility is not callability**
  - Tool Catalog describes capabilities that exist in the system.
  - Worker Capability describes the tools effectively available to a particular Worker after role/tool/permission resolution.
  - Main Callable Tools are the control-plane tools Main can actually invoke.
  - These sets must remain distinct. Discovery never expands authorization.

- **Docs = project knowledge**
  - Architecture-sensitive work should use the relevant docs before changing runtime topology.
  - Main routes document/repository investigation; a Worker performs file/search execution when reading is required.
  - Prefer informed agent decisions over broad hard-coded restrictions.

- **Dream = observation + analysis + proposals**
  - Learn recurring task patterns from durable evidence across Dream cycles.
  - Produce structured recommendations when repetition suggests a Specialist or other durable change may be useful.
  - Persist only Dream-owned run/cursor state and proposal records; do not directly modify formal memory, Specialist definitions, configuration, or external systems.
  - Proposal execution remains in the existing governed Main/Runtime path and subject to user authorization and runtime permissions.
  - Never manage WorkAgent because WorkAgent is non-persistent by definition.

## Worker model

Workers have three conceptual kinds:

- `general` — permanent, fully capable fallback for mixed, cross-domain, unknown, or uncategorized work. It cannot be disabled or removed.
- `work` — ephemeral WorkAgent snapshot for one task. Main may customize tools, prompt, model/preset, thinking, temperature, timeout, or context. It is never written to role state, never accumulates Specialist usage telemetry, and disappears after the task.
- specialists — persistent Workers with a stable recurring responsibility, prompt, capability set, or runtime profile.

Built-in specialists include `researcher`, `planner`, `coder`, `debugger`, `tester`, `writer`, and `analyst`. Users or the governed Main/Runtime execution path may define additional roles; Dream may only recommend such changes from repeated task patterns.

A role answers **what recurring responsibility should this Worker own?** A tool answers **what capability can this Worker use?** WorkAgent is the bridge for one-off customization that does not justify a persistent role.

All three Worker kinds reuse the same SubagentManager admission, task tracking, status, steer/stop, execution, result delivery, AgentRunner path, and common Worker execution prompt. WorkAgent must not grow a separate Agent framework or duplicate that lifecycle.

## Main-Agent delegation policy

The goal is knowledge-guided orchestration: make the system catalog and current Worker capabilities discoverable without exposing Worker execution tools as Main-callable tools.

Typical routing:

```text
conversation / clarification / result synthesis -> Main Agent
clear stable specialist responsibility           -> matching active Specialist
one-off task needs custom capability/runtime      -> WorkAgent
mixed / unknown / no clear match                  -> General
browser work                                      -> suitable Worker + Browser capability
any operational tiny action                       -> suitable Worker, not Main
```

Persistent role selection remains conceptually:

```text
explicit persistent role
  -> General or Specialist, with permitted per-run runtime overrides

no persistent role + no task-specific override
  -> General

no persistent role + any task-specific override
  -> WorkAgent
```

Task-specific overrides may include description, system prompt, tools, model/preset, thinking, temperature, timeout, or context. Runtime interfaces may change; Main Prompt must rely on the control-plane schema actually exposed at runtime and must not invent query or dispatch fields.

WorkAgent does not inherit General's persistent prompt/model/generation tuning. Unspecified WorkAgent runtime settings continue from the current Main runtime. Model-specific Prompt Prefix remains global and is prepended for Main, General, WorkAgent, or Specialist whenever that model is used.

Main owns the user conversation and final synthesis. Every operational dispatch is asynchronous. A successful dispatch ends the current Main turn with a concise acknowledgement. Main does not poll to wait for completion; status queries are for user-requested status, coordination, or recovery. Worker completion starts a new Main turn, which may synthesize the result or dispatch follow-up work.

Main should use current capability/role discovery exposed by the control plane rather than assuming the Specialist list is static. Users and governed proposal execution may add or refine roles over time. Roles marked `cold` remain discoverable but should not be preferred unless their specialization is still the best match or the user asks for them.

There is no architecture-level two-call budget for Main orchestration. Runtime limits should express actual safety/resource constraints, not an arbitrary per-turn call count.

Hard runtime routing should be reserved for real safety, security, permission, or shared-resource invariants, not used as a substitute for project knowledge.

## Skills

Skills are execution guidance, not an authority layer. Main may see skill metadata/catalog entries for routing, but should not use Worker-only file tools to open execution skills. The selected Worker receives the shared skills catalog and reads the relevant `SKILL.md` when its assigned task requires that workflow.

A skill cannot expand a Worker's effective tool set, workspace scope, or PermissionManager authority. Main should preserve explicit skill names and user constraints in delegated task text so the Worker can discover and apply the intended workflow.

## Browser design

Browser is a **tool capability**, not a dedicated Browser Agent.

General has Browser available as part of its broad fallback capability set. WorkAgent may receive Browser for one task. A focused Specialist receives Browser when that capability belongs to its recurring responsibility.

Because nanobot uses one persistent Chromium/profile, browser state is shared. Main is responsible for avoiding parallel browser work against the same session; isolated Worker tool registries do not make concurrent browser tasks safe.

Human takeover keeps priority until control is returned. AI and human must operate the same persistent Chromium/profile rather than creating a parallel browser environment.

## Specialist evolution with Dream

Dream observes evidence and proposes Specialist evolution; it does not own or directly mutate Specialist business state. Specialist runtime state is separated from reusable skills and kept in three canonical workspace files:

- `agents/roles.json` — persistent Specialist definitions.
- `agents/role_candidates.json` — compact cross-Dream evidence for responsibilities that may deserve a Specialist.
- `agents/role_usage.json` — runtime-generated launch/recency telemetry for persistent roles.

WorkAgent never appears in those files.

Dream does not edit these files as a way to execute proposals. It may persist Dream-owned control-plane state and structured proposal/evidence records required by its observation cycle. Saving that state is bookkeeping, not permission to apply the recommended business-state mutation. Creating, updating, cooling, reactivating, disabling, or deleting a Specialist remains an action for the governed Main/Runtime path under the applicable user authorization and permission boundary.

There is no separately maintained role manifest: current role state is the source of truth, so the system does not create a second derived index that can drift.

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
Main/Runtime evaluates and, when authorized, dispatches execution
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

General is intentionally broad because it is the permanent fallback: when no Specialist matches, it should still be able to complete ordinary Worker tasks without another architecture change.

WorkAgent capabilities are selected task-by-task but still intersect with the parent's allowed tools and still exclude recursive Worker creation.

Focused Specialists should receive only the capabilities useful to their responsibility. Do not turn every Specialist into another full General, and do not create a new Agent class merely because a Specialist needs a new capability. Future sensitive host-management tools can remain explicitly scoped even while General keeps the normal Worker capability set.

Dream's authority is narrower than ordinary Worker authority: analysis and Dream-owned state/proposal persistence do not grant permission to mutate formal memory, Specialist state, configuration, or external systems. Proposal execution must continue through existing Main, Runtime, and user authorization checks.

## Future platform work

Continue with:

1. Control-plane discovery that exposes the Tool Catalog, effective Worker capabilities/availability, and Main-callable tools without conflating them.
2. Unified task manager with running / queued / waiting-human / failed / completed states.
3. Richer evidence for Dream Specialist recommendations without turning them into a rigid scoring system.
4. Controlled server-management capabilities and an appropriate Specialist responsibility boundary.
5. Cross-channel notifications, such as starting work in WebUI and receiving completion alerts in WeChat.

## Product goal

```text
Main Agent stays responsive and only orchestrates
+
all operational execution happens in Workers
+
General guarantees a permanent fully capable fallback
+
WorkAgent handles one-off customized execution without persistence
+
Specialists own stable recurring responsibilities
+
one common Worker prompt governs execution
+
Tool Catalog / Worker Capability / Main Callable Tools remain separate
+
Worker completion enters a new Main turn
+
Dream observes, analyzes, and proposes from repeated evidence
+
Main/Runtime dispatch accepted execution within authorization boundaries
+
User retains final governance over consequential changes
+
Shared resources preserve explicit ownership
```
