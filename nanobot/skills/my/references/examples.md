# My Tool — Practical Examples

Concrete scenarios showing when and how to use the my tool effectively.

## Diagnosis

### "Why can't you search the web?"
```
→ my(action="check", key="web_config.enable")
  → False
→ "Web search is disabled. Add web.enable: true to your config to enable it."
```

### "Why did you stop?"
```
→ my(action="check", key="max_iterations")
  → 40
→ "I hit the iteration limit (40). The task was complex. I can ask the user if they want to increase it."
```

### "What model are you running?"
```
→ my(action="check", key="model_id")
  → 'main'
→ my(action="check", key="model")
  → 'claude-opus-4-5'
→ "I'm running model_id 'main' (upstream model 'claude-opus-4-5'). List the configured catalog with my(action=\"check\", key=\"models\")."
```

## Adaptive Behavior

### Large codebase analysis
```
→ my(action="check")
  → context_window_tokens: 200000
→ my(action="set", key="model_id", value="deep")
  → "Set model_id = 'deep' for the next turn; model will be '<resolved model>'; context_window_tokens will be 262144"
→ "I've switched to the configured 'deep' model for this session's next turn."
```

### Switching to a configured model
```
→ my(action="set", key="model_id", value="fast")
  → "Set model_id = 'fast' for the next turn; model will be '<resolved model>'; context_window_tokens will be <n>"
→ "Selected the configured 'fast' model for this session's next turn."
```

Model selection is by `model_id` — the stable slug of an entry in your configured `models` catalog (e.g. 'main', 'deep', 'fast'). The set response echoes the resolved upstream model and new context window; exact values depend on that config. Unknown IDs are rejected, and raw upstream provider model strings are never valid `model_id` values.

## Cross-Turn Memory

### Remembering user preferences
```
# Turn 1: user says "keep it brief"
→ my(action="set", key="user_style", value="concise")
  → "Set scratchpad.user_style = 'concise'"

# Turn 3: new topic
→ my(action="check", key="user_style")
  → 'concise'
  (adjusts response style accordingly)
```

### Tracking project context
```
→ my(action="set", key="active_branch", value="feat/auth")
→ my(action="set", key="test_framework", value="pytest")
→ my(action="set", key="has_docker", value=true)
```
