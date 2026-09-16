# My Tool

Let the agent sense and adjust its own runtime state — like asking a coworker "are you busy? can you switch to a bigger monitor?"

## Why You Need It

Normal tools let the agent operate on the outside world (read/write files, search code). But the agent knows nothing about itself — it doesn't know which model it's running on, which workspace it can access, or which runtime limits apply.

My tool fills this gap. With it, the agent can:

- **Know who it is**: What model am I using? Where is my workspace? What is my per-turn iteration limit?
- **Adapt on the fly**: Complex task? Expand the context window. Simple chat? Switch to a faster model.
- **Remember across turns**: Store notes in your scratchpad that persist into the next conversation turn.

## Configuration

Enabled by default (read-only mode). The agent can check its state but not set it.

```yaml
tools:
  my:
    enable: true       # default: true
    allow_set: false   # default: false (read-only)
```

To allow the agent to set its runtime state (for example, switch models or adjust parameters), set `tools.my.allow_set: true`.

`model_id` selection uses that catalog; this tool does not change model definitions.

Most modifications are held in memory only. In an active session, `model_id` is
saved with that session and applies to its next turn, so the selection remains
when the session is resumed.

---

## check — Check "my" current state

Without parameters, returns a key config overview:

```text
my(action="check")
# → max_iterations: 40
#   context_window_tokens: 200000
#   model_id: 'main'
#   workspace: PosixPath('/tmp/workspace')
#   provider_retry_mode: 'standard'
#   max_tool_result_chars: 16000
#   subagents: {...}
#   Note: model_id is the canonical ID selected for this session.
```

With a key parameter, drill into a specific config:

my(action="check", key="model_id")
# → The canonical model ID selected for this session

my(action="check", key="model")
# → The upstream model string behind the selected model_id

my(action="check", key="models")
# → The read-only catalog keyed by configured canonical model IDs

my(action="check", key="web_config.enable")
# → Whether web search is enabled

### What you can do with it

| Scenario | How |
|----------|-----|
| "What model ID are you using?" | `check("model_id")` |
| "Which configured model IDs are available?" | `check("models")` |
| "Which upstream model is selected?" | `check("model")` |
| "What is the per-turn iteration limit?" | `check("max_iterations")` |
| "How large is the selected context window?" | `check("context_window_tokens")` |
| "Where is your working directory?" | `check("workspace")` |
| "Show me your full config" | `check()` |
| "Are there any subagents running?" | `check("subagents")` — shows phase, iteration, elapsed time, tool events |

---

## set — Runtime tuning

Changes do not require a restart. In an active session, `model_id` is saved for
that session and applies to its next turn; it must match a key in read-only
`models`. The selected model's `ModelConfig` supplies its upstream model and
context-window values. Use a canonical ID from `models`, not a provider's
upstream model string.

Other writable runtime tuning takes effect immediately. Direct `model` writes
are rejected, and direct `context_window_tokens` writes are rejected during an
active session because they change the shared instance default; choose a
`model_id` instead.

```text
my(action="check", key="models")
# → Choose a configured canonical ID from this catalog, such as "main"

my(action="set", key="model_id", value="main")
# → Set model_id = 'main' for the next turn; model and context_window_tokens follow that ModelConfig
```

You can also store custom state in your scratchpad:

```text
my(action="set", key="current_project", value="nanobot")
my(action="set", key="user_style_preference", value="concise")
my(action="set", key="task_complexity", value="high")
# → These values persist into the next conversation turn
```

### Protected parameters

These parameters have type and range validation — invalid values are rejected:

| Parameter | Type | Range | Purpose |
|-----------|------|-------|---------|
| `max_iterations` | int | 1–100 | Max tool calls per conversation turn |
| `context_window_tokens` | int | 4,096–1,000,000 | Instance default; during an active session, choose the context window through `model_id` |
| `model` | str | — | Current upstream model string; read-only, select through `model_id` |
| `model_id` | str | configured key in `models` | Canonical model selection for this session's next turn; saved with the session |
| `models` | mapping | — | Read-only catalog of configured canonical model IDs |

Other parameters (e.g. `workspace`, `provider_retry_mode`, `max_tool_result_chars`) can be set freely, as long as the value is JSON-safe.

---

## Practical Scenarios

### "This task is complex, I need more room"
```text
Agent: This codebase is large; let me inspect the configured model IDs and choose one with the context window I need.
→ my(action="check", key="models")
→ my(action="set", key="model_id", value="coder_v2")  # after confirming "coder_v2" is present
# → Set model_id = 'coder_v2' for the next turn
```

### "Simple question, don't waste compute"

```text
Agent: This is a straightforward question, let me choose a configured lightweight model ID.
→ my(action="check", key="models")
→ my(action="set", key="model_id", value="main")  # after confirming "main" is present
# → Set model_id = 'main' for the next turn
```

### "Remember user preferences across turns"

```text
Turn 1: my(action="set", key="user_prefers_concise", value=True)
Turn 2: my(action="check", key="user_prefers_concise")
# → True (still remembers the user likes concise replies)
```

### "Self-diagnosis"

```text
User: "Why aren't you searching the web?"
Agent: Let me check my web config.
→ my(action="check", key="web_config.enable")
# → False
Agent: Web search is disabled — please set web.enable: true in your config.
```

### "Context budget management"

```text
Agent: Let me check the context limit for the selected model.
→ my(action="check", key="context_window_tokens")
# → 200000
Agent: I'll plan within that context-window limit.
```

### "Subagent monitoring"

```text
Agent: Let me check on the background tasks.
→ my(action="check", key="subagents")
# → 2 subagent(s):
#   [task-1] 'Code review'
#     phase: running, iteration: 5, elapsed: 12.3s
#     tools: read(✓), grep(✓)
#     usage: {'prompt_tokens': 8000, 'completion_tokens': 1200}
#   [task-2] 'Write tests'
#     phase: pending, iteration: 0, elapsed: 0.2s
#     tools: none
Agent: The code review is progressing well. The test task hasn't started yet.
```

---

## Safety Mechanisms

Core design principle: **The tool does not rewrite `config.json`.** Instance-wide
changes live in memory only. In an active session, `model_id` is persisted with
that session and applies to its next turn; it is a session selector, not a global
config rewrite. The `models` catalog remains read-only.

### Off-limits (BLOCKED)

Cannot be checked or modified — fully hidden:

| Category | Attributes | Reason |
|----------|-----------|--------|
| Core infrastructure | `bus`, `provider`, `_running` | Changes would crash the system |
| Tool registry | `tools` | Must not remove its own tools |
| Subsystems | `runner`, `sessions`, `consolidator`, etc. | Affects other users/sessions |
| Sensitive data | `_mcp_servers`, `_pending_queues`, etc. | Contains credentials and message routing |
| Security boundaries | `restrict_to_workspace`, `channels_config` | Bypassing would violate isolation |
| Python internals | `__class__`, `__dict__`, etc. | Prevents sandbox escape |

### Read-only (check only)

Can be checked but not set:

| Category | Attributes | Reason |
|----------|-----------|--------|
| Subagent manager | `subagents` | Observable, but replacing breaks the system |
| Execution config | `exec_config` | Can check sandbox/enable status, cannot change it |
| Web config | `web_config` | Can check enable status, cannot change it |

### Sensitive field protection

Sub-fields matching sensitive names (`api_key`, `password`, `secret`, `token`, etc.) are blocked from both check and set, regardless of parent path. This prevents credential leaks via dot-path traversal (e.g. `web_config.search.api_key`).
