# Design Constraints

These rules govern architectural decisions. When adding a feature or fixing a bug, prefer paths that respect these boundaries.

## Main Agent orchestrates; Workers execute

The Main Agent is the user-facing control plane. It stays responsive, understands intent, decomposes work, judges required capabilities, chooses Workers, coordinates shared resources, tracks delegated work when needed, and synthesizes results.

Main does not perform operational execution itself, including filesystem reads/searches/edits, shell/process execution, web or Browser work, code changes, builds, tests, or image/media processing. Operational work is delegated even when it is tiny or near-instant.

Keep three layers separate: the system Tool Catalog, each Worker's effective capabilities, and the control-plane tools callable by Main. Discovery does not grant callability or authority. PermissionManager remains the runtime authority.

Workers have a permanent, fully capable `general` fallback, focused persistent Specialists, and task-scoped WorkAgents. Main should prefer a clearly matching active Specialist, use WorkAgent for one-off custom capability/runtime needs, and use `general` otherwise. All three use the same SubagentManager/AgentRunner lifecycle and the same common Worker execution prompt.

Every Worker dispatch is asynchronous. Main ends the dispatching turn after successful launch, does not poll for completion, and receives completion through a new Main turn. There is no special two-call orchestration budget in the architecture.

Treat this as the project-level mental model:

```text
Main Agent = conversation + orchestration
General    = permanent fallback Worker
WorkAgent  = ephemeral customized Worker
Specialist = persistent focused Worker
Tools      = capabilities
Docs       = project knowledge
Dream      = observation + analysis + proposals
User       = highest governance authority
```

Responsibilities belong in roles; capabilities belong in tools. Browser is a capability that eligible Workers may use, not a reason to create a separate Browser Agent type. Shared browser state still requires orchestration so multiple Workers do not operate the same persistent Chromium session concurrently. Concurrent file mutations likewise require Main coordination.

Dream is advisory, not an execution authority. It may inspect durable evidence, save its own run/cursor state, and persist structured proposals, but it does not directly modify formal memory, Specialist definitions, configuration, or external systems. A proposal may suggest creating, refining, cooling, or reactivating a Specialist, but any such business-state mutation must go through the existing governed Main/Runtime path and user authorization boundary. Saving Dream's own state or proposal records is not the same as executing a proposal.

## Read the intended design before repairing it

For architecture-sensitive failures, first locate the relevant project knowledge and diagnose the intended topology before inventing a parallel workaround. Main routes the investigation; Workers perform repository/document inspection that requires file or search tools.

Start with `docs/design-principles.md` and `docs/README.md`, then follow the subsystem guide. In particular:

- browser runtime and handoff: `docs/browser.md`;
- browser failures: `docs/troubleshooting/browser-runtime.md`;
- runtime persistence: `docs/runtime-storage.md`;
- agent structure and evolution: `docs/agent-architecture-roadmap.md`.

Changing the architecture is allowed when justified. Make the new invariant explicit and update the documentation with the change.

## Core stays small; extend at the edges

New capabilities should be added via `channels/`, `tools/`, skills, or MCP servers. The files `agent/loop.py` and `agent/runner.py` form the critical core path; changes there should be minimal and justified. If a feature can live in a channel adapter, a tool, or an external MCP server, it should not be inlined into the agent loop.

Runtime state fan-out follows the same boundary. `AgentLoop` may publish generic runtime events from `nanobot.bus.runtime_events` for turn/run/model/goal state changes, but WebUI/WebSocket wire details such as `_turn_end`, `_goal_status`, title refreshes, and goal-state sync belong in `nanobot.session.webui_turns.WebuiTurnCoordinator` or the relevant channel adapter.

## Less structure, more intelligence

Prefer simple, readable code over new framework layers and indirection. Add structure only when it removes real complexity, protects an important boundary, or matches an established local pattern. The best fix is often a smaller prompt, a tighter tool contract, a channel-local change, or one focused regression test.

Prefer informed flexibility over broad prohibitions. Give Main enough capability knowledge to route correctly and Workers enough task knowledge to execute; add hard constraints only for real safety, security, permission, or shared-resource invariants.

## Prefer duplication over premature abstraction

Channels and providers are allowed to repeat similar logic (send retries, media handling, message splitting). Do not introduce complex base classes or shared helpers just to eliminate duplication across channel files. Each channel file should remain self-contained and readable on its own. The same applies to provider implementations.

Worker execution policy is the exception only because General, Specialist, and WorkAgent intentionally share one execution lifecycle and one public execution contract; do not copy that contract into three role prompts.

## Minimal change that solves the real problem

Fix bugs by changing only what is necessary. Do not bundle unrelated refactors or clean-ups into a feature or bugfix PR. If a refactor is genuinely required, it should be a separate, clearly scoped PR.

## Keep PRs reviewable

A bugfix should make the protected invariant clear, change the smallest surface that enforces it, and add only the closest regression test. If a diff starts changing ownership boundaries or mixing behavior changes with clean-up, split it before it becomes hard to review.

## Type dynamic boundaries at the edge

Wire payloads, persisted records, and third-party SDK objects are untrusted dynamic boundaries. Prefer a parser or small normalizer at the owning edge, and use `TypedDict` for stable dictionary shapes, so validation happens once and internal code receives a concrete type. Do not spread raw dynamic dictionaries or SDK objects through the core.

Stable first-party dependencies must be typed where they are stored or passed. Do not declare an internal service, context field, or callback result as `Any` and then recover its real type with consumer-side casts. Use the concrete type or a narrow `Protocol`; reserve `Any` for genuinely dynamic boundaries.

`typing.cast` performs no runtime validation. Every new cast must be supported by a runtime check on the same path or by an explicit invariant that is clear from construction and control flow (and documented locally when it is not obvious). If input can violate the claimed type, handle that invalid case before casting; never use `cast` only to silence BasedPyright.

## Explicit over magical

Configuration must be declared explicitly in `config/schema.py` Pydantic models. Error handling should raise clear exceptions rather than silently correcting bad input. Provider auto-detection exists, but every resolution path must be traceable from the factory to the concrete provider class.
