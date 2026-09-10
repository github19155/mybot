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
| 3 | P1 | done | Strict post-turn context compaction | Main-triggered context compaction is scheduled in the background and then competes for the session lock after the current turn. A following turn can theoretically acquire the lock first. | Move pending compaction to a deterministic post-turn stage before releasing the session lock, so `applies_to: next_turn` is guaranteed. |
| 4 | P1 | in-progress | Model Fleet + provider/model admission | Main can select child models, but lacks a runtime fleet view of concrete provider/model offerings; provider calls also lack shared concurrency/429 pressure control. Same nominal model can perform very differently across suppliers. | Treat provider+model as an independent Offering. Persist passive real-work telemetry and objective quality evidence, expose status/recommendation/profile management to Main, auto-select when the user did not explicitly choose a model, and gate each physical request attempt with provider/offering concurrency, shared 429 cooldown, adaptive recovery and bounded Main priority. No periodic token-burning benchmark. |
| 5 | P1 | deferred | Browser takeover capture efficiency | Human takeover polls a 1920x1080 screenshot frequently; the sidecar captures PNG by starting ImageMagick for each request. This adds CPU, process, memory-copy, and network overhead. | Deferred while the project runs on the current 2C2G server; revisit persistent capture/push streaming and adaptive frame rate after moving to the E5 server. |
| 6 | P1 | in-progress | Durable subagent interruption state | Background Subagents are process-local tasks/statuses. Gateway restart can make a running task disappear with no durable record that it was interrupted. | Add a minimal durable job ledger so previously-running tasks surface as `interrupted` after restart, without automatic replay or a second scheduler. |
| 7 | P2 | deferred | Subagent initialization caching | Each Subagent rebuilds tool registries, skill summaries, and static prompt sections, which becomes wasteful for many short WorkAgents. | Deferred because correct invalidation for prompts, skills, tools and permissions adds more complexity/risk than the current startup savings justify. |
| 8 | P2 | pending | Dream/Specialist privilege governance | Dream candidate evidence is deduped by occurrence, but source provenance/trust and privilege escalation are not first-class. Long-lived Specialist creation should not automatically imply high-risk capabilities. | Track evidence provenance/trust and introduce a privilege ceiling/default low-privilege policy. Require stronger governance/user approval for high-risk tools such as exec/write/browser where appropriate. |
| 9 | P2 | pending | Reproducible dependency locking | Docker/runtime installs resolve dependencies from version ranges, so rebuilds at different times can produce different dependency graphs. | Add and maintain `uv.lock` (or the repository's chosen lock mechanism) and use frozen/locked installs in CI and Docker builds where compatible with existing extras. |
| 10 | P2 | pending | Browser/CDP network isolation | Chromium CDP is reachable inside the compose network without a dedicated authenticated control boundary. A compromised peer container could potentially control the persistent logged-in browser. | Put browser/CDP on a tighter internal network and/or expose CDP through a narrow authenticated proxy/allowlisted path while keeping the current persistent Chromium architecture. |

## Review notes

### Items confirmed directly from current implementation

- Item 1 is resolved: WorkAgent now uses native ephemeral `SubagentManager` launch paths rather than temporary resolver replacement and a launch lock.
- Item 2 is resolved: shared stateful browser workflows use a process-wide lease keyed by CDP endpoint, with inline-child delegation to avoid parent/child deadlock.
- Item 3 is resolved: Main-requested context compaction is drained at awaited post-turn runtime-event boundaries rather than by a competing background session-lock task.
- Item 4 extends scheduling at the physical LLM request boundary: configured provider+model routes are independent offerings, while the existing Subagent admission layer remains the Agent-level scheduler.
- Item 5 is deferred until the E5 migration because higher-rate takeover capture would be a poor trade on the current 2C2G host.
- Item 6 is being implemented as durability for the existing Subagent status system, not as a new scheduler or automatic resume queue.
- Item 7 is deferred because stale prompt/skill/tool/permission caches are a larger correctness risk than the expected startup savings.
- Browser takeover state/screenshot polling is frequent, and the sidecar screenshot endpoint shells out to ImageMagick for each frame.
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
