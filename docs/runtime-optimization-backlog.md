# Runtime Optimization Backlog

> Temporary tracking document for the current nanobot runtime/architecture review.
>
> Governance rule: every item below must be discussed with the user individually and must not be implemented until the user explicitly approves that item. Each implementation must use its own feature/fix branch and PR; nothing should be merged without explicit user confirmation.
>
> Lifecycle rule: keep this document until every item is completed and merged. The final implementation PR may delete this document once all remaining items are resolved.

## Status legend

- `pending` — recorded, not yet approved for implementation
- `approved` — user approved implementation, work may start
- `in-progress` — implementation is underway on its own branch/PR
- `done` — implementation completed and merged
- `deferred` — user explicitly chose not to implement for now

## Backlog

| # | Priority | Status | Item | Problem / Goal | Proposed Direction |
|---|---|---|---|---|---|
| 1 | P0 | done | Native WorkAgent execution | WorkAgent currently relies on temporary `SubagentManager` method replacement plus a launch lock. Inline WorkAgent runs can therefore be unnecessarily serialized and the shared-manager monkey-patch is fragile under concurrency. | Make ephemeral workers a first-class `SubagentManager` path, e.g. a task-scoped worker/role spec passed directly into spawn/inline execution. Remove the monkey-patch and `_WORK_LAUNCH_LOCKS`. |
| 2 | P0 | done | Global browser resource lease | `browser` tools are marked exclusive only for one agent's tool-call batching. Multiple Main/Subagent executions can still operate the same persistent Chromium session concurrently. | Add a shared resource-lease mechanism keyed by browser/CDP endpoint so only one agent owns the persistent browser at a time, while preserving human takeover ownership. |
| 3 | P1 | pending | Strict post-turn context compaction | Main-triggered context compaction is scheduled in the background and then competes for the session lock after the current turn. A following turn can theoretically acquire the lock first. | Move pending compaction to a deterministic post-turn stage before releasing the session lock, so `applies_to: next_turn` is guaranteed. |
| 4 | P1 | pending | Provider/model-aware admission control | Subagent admission limits task counts, but does not yet coordinate provider/model concurrency, rate-limit pressure, or token-heavy parallel work. | Extend the existing admission layer with provider/model concurrency limits, adaptive 429/backoff signals, and optional token-aware budgeting. Do not introduce a second scheduler. |
| 5 | P1 | pending | Browser takeover capture efficiency | Human takeover polls a 1920x1080 screenshot frequently; the sidecar captures PNG by starting ImageMagick for each request. This adds CPU, process, memory-copy, and network overhead. | Add adaptive polling based on recent interaction/page activity, reduce/background polling when idle/hidden, and consider a cheaper preview encoding/capture path while keeping high-quality PNG for AI screenshots where needed. |
| 6 | P1 | pending | Durable subagent interruption state | Background Subagents are process-local tasks/statuses. Gateway restart can make a running task disappear with no durable record that it was interrupted. | Add a minimal durable job ledger or equivalent restart record so previously-running tasks can be surfaced as `interrupted` and optionally recovered/restarted later. Avoid heavyweight external queue infrastructure unless justified. |
| 7 | P2 | pending | Subagent initialization caching | Each Subagent rebuilds tool registries, skill summaries, and static prompt sections, which becomes wasteful for many short WorkAgents. | Cache immutable/static generations such as tool schema sets, skills summaries, and role prompt prefixes; keep task/session/runtime-specific data dynamic. |
| 8 | P2 | pending | Dream/Specialist privilege governance | Dream candidate evidence is deduped by occurrence, but source provenance/trust and privilege escalation are not first-class. Long-lived Specialist creation should not automatically imply high-risk capabilities. | Track evidence provenance/trust and introduce a privilege ceiling/default low-privilege policy. Require stronger governance/user approval for high-risk tools such as exec/write/browser where appropriate. |
| 9 | P2 | pending | Reproducible dependency locking | Docker/runtime installs resolve dependencies from version ranges, so rebuilds at different times can produce different dependency graphs. | Add and maintain `uv.lock` (or the repository's chosen lock mechanism) and use frozen/locked installs in CI and Docker builds where compatible with existing extras. |
| 10 | P2 | pending | Browser/CDP network isolation | Chromium CDP is reachable inside the compose network without a dedicated authenticated control boundary. A compromised peer container could potentially control the persistent logged-in browser. | Put browser/CDP on a tighter internal network and/or expose CDP through a narrow authenticated proxy/allowlisted path while keeping the current persistent Chromium architecture. |

## Review notes

### Items confirmed directly from current implementation

- Item 1 is resolved: WorkAgent now uses native ephemeral `SubagentManager` launch paths rather than temporary resolver replacement and a launch lock.
- Item 2 is resolved by the PR carrying this status update: shared stateful browser workflows use a process-wide lease keyed by CDP endpoint, with inline-child delegation to avoid parent/child deadlock.
- Subagent admission already has global and per-session concurrency controls; future scheduling improvements should extend this layer rather than create a parallel scheduler.
- Browser takeover state/screenshot polling is frequent, and the sidecar screenshot endpoint shells out to ImageMagick for each frame.
- Main-triggered context compaction is scheduled for after the current turn by acquiring the session lock in a background task.
- Background Subagent runtime state is primarily process-local.
- Dream Specialist creation already requires at least two distinct persisted observations and Dream cannot delete/replace protected roles.

## Per-item workflow

For each backlog item:

1. Explain the exact current behavior and defect/inefficiency.
2. Propose the smallest architecture-compatible design.
3. Identify API, compatibility, migration, concurrency, security, and test impact.
4. Wait for explicit user approval before writing code.
5. Create a fresh branch from the then-current `main`.
6. Implement in stages and add focused tests.
7. Update the completed item status in the same implementation PR.
8. Open the PR and run full relevant CI.
9. Report results; do not merge until the user explicitly says to merge.

When all items are `done` (or explicitly resolved by the user as no longer required), delete this document in the final implementation PR.
