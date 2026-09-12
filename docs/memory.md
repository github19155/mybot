# AI Agent Memory in nanobot

This page explains how nanobot implements long-term AI agent memory: session
history, compressed archives, durable knowledge files, and Dream analysis.

nanobot's memory is built on a simple belief: memory should feel alive, but it should not feel chaotic.

Good memory is not a pile of notes. It is a quiet system of attention. It notices what is worth keeping, lets go of what no longer needs the spotlight, and turns lived experience into something calm, durable, and useful.

That is the shape of memory in nanobot.

## The Design

nanobot does not treat memory as one giant file.

It separates memory into layers, because different kinds of remembering deserve different tools:

- `session.messages` holds the living short-term conversation.
- `memory/history.jsonl` is the running archive of compressed past turns.
- `SOUL.md`, `USER.md`, and `memory/MEMORY.md` are the durable knowledge files.
- Dream is a read-only cognition layer that can analyze this state and propose changes without applying them.

This keeps the system light in the moment, but reflective over time.

## The Flow

Memory moves through nanobot in two stages.

### Stage 1: Consolidator

When a conversation grows large, the `Consolidator` summarizes older turns and appends the result to `memory/history.jsonl`, while keeping recent conversation available. Each summary preserves useful long-term facts and a short handoff for active work.

This file is:

- append-only
- cursor-based
- optimized for machine consumption first, human inspection second

Each line is a JSON object:

```json
{"cursor": 42, "timestamp": "2026-04-03 00:02", "content": "- User prefers dark mode\n- Decided to use PostgreSQL"}
```

It is not the final memory. It is durable history that Dream and normal runtime code can inspect.

### Stage 2: Dream

Dream is the slower background cognition layer. It consumes new history plus permitted read-only views of the current `SOUL.md`, `USER.md`, and `memory/MEMORY.md`, then emits validated findings and proposals.

Dream does **not** edit or restore those canonical files. It has no mutation authority over memory, profile, Specialist definitions, configuration, tools, or external systems. Its durable output is a runtime-owned audit record of the validated Dream result; any later canonical change must be handled outside Dream under the normal runtime and permission boundaries.

This keeps reflection separate from authority: Dream may identify what could be worth changing, but it does not make that proposal true by itself.

## The Files

In this page, `workspace` means the configured **agent workspace** (the default
is `~/.nanobot/workspace/`, or the path passed with `--workspace`). Selecting a
different project in the WebUI changes that chat's project context and tool
working directory; it does not relocate the files below.

```text
workspace/
├── SOUL.md              # The bot's long-term voice and communication style
├── USER.md              # Stable knowledge about the user
└── memory/
    ├── MEMORY.md        # Project facts, decisions, and durable context
    ├── history.jsonl    # Append-only history summaries
    ├── dream_results.jsonl # Validated Dream findings/proposals audit records
    ├── .cursor          # Consolidator write cursor
    └── .dream_cursor    # Dream consumption cursor
```

A selected project may provide its own `AGENTS.md`, but project-local `SOUL.md`,
`USER.md`, and `memory/` do not replace the agent-owned files above. This keeps
one agent's profile and memory continuous while it works across projects. Use a
separate configured agent workspace when identity or memory must be isolated.

These files play different roles:

- `SOUL.md` stores durable agent behavior/profile state.
- `USER.md` stores stable knowledge about the user.
- `MEMORY.md` stores durable project facts, decisions, and context.
- `history.jsonl` stores what happened on the way there.
- `dream_results.jsonl` stores Dream's validated findings and proposals, not applied mutations.

## Why `history.jsonl`

The old `HISTORY.md` format was pleasant for casual reading, but it was too fragile as an operational substrate.

`history.jsonl` gives nanobot:

- stable incremental cursors
- safer machine parsing
- easier batching and compaction
- a clear boundary between durable history and canonical profile/memory state

You can still search it with familiar tools:

```bash
# grep
grep -i "keyword" memory/history.jsonl

# jq
cat memory/history.jsonl | jq -r 'select(.content | test("keyword"; "i")) | .content' | tail -20

# Python
python -c "import json; [print(json.loads(l).get('content','')) for l in open('memory/history.jsonl','r',encoding='utf-8') if l.strip() and 'keyword' in l.lower()][-20:]"
```

The difference is philosophical as much as technical:

- `history.jsonl` is for structured history
- `SOUL.md`, `USER.md`, and `MEMORY.md` are canonical durable state
- Dream results are analysis/proposals, not automatic writes to that state

## Dream Authority

Dream execution and persistence are runtime-owned. The Dream worker may read the state exposed to it and create findings or proposals under canonical capability policy, but it cannot use a proposal as authority to mutate or restore `SOUL.md`, `USER.md`, or `memory/MEMORY.md`.

`PermissionManager` remains the runtime authority for capabilities. Dream's read-only prompt contract is an additional invariant, not a replacement for capability enforcement.

## Dream Model Selection

Dream model policy lives under `agents.defaults.dream`. It may select an explicit Dream model preset, choose a preset recommended from a configured Dream pool, or use the configured Dream fallback preset. `ModelManagement` owns that selection policy and delegates the selected preset to `ModelRuntimeResolver`, which is the only component that turns the selection into `LLMRuntime`.

Dream does not inherit an ad hoc runtime construction path, and `ModelFleet` recommendations do not construct the runtime.

## In Practice

What this means in daily use is simple:

- conversations can stay fast without carrying infinite context
- durable history can be analyzed separately from canonical memory mutation
- Dream can surface useful proposals without silently rewriting identity or memory

Memory should not feel like a dump. It should feel like continuity.

That is what this design is trying to protect.
