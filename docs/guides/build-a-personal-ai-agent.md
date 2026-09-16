# How to Build a Personal AI Agent with nanobot

This guide builds a personal AI agent you can run locally, talk to from the
terminal or browser, and later connect to chat apps, memory, tools, and
automations.

## What you will build

- a configured nanobot install
- one working model provider
- one local agent reply
- a browser WebUI session for ongoing work

## When to use this

Use this when you want a personal AI agent that you control rather than a hosted
chat-only interface. nanobot is useful when the agent needs local workspace
access, tool calls, session history, memory, scheduled work, or chat app
delivery.

## Install

```bash
python -m pip install nanobot-ai
nanobot onboard --wizard
```

The wizard creates `~/.nanobot/config.json` and helps you configure a provider
and model. After onboarding, verify that the config has a root `models` entry
with a concrete `provider` and upstream `model`, and that
`agents.defaults.model_id` selects that entry. For example, a local Ollama
setup can use:

```json
{
  "providers": {
    "ollama": {
      "api_base": "http://localhost:11434/v1"
    }
  },
  "models": {
    "main": {
      "display_name": "Local Llama",
      "provider": "ollama",
      "model": "llama3.1:8b",
      "capabilities": {
        "text": true
      },
      "context_window_tokens": 16384,
      "generation_defaults": {
        "max_tokens": 2048,
        "temperature": 0.1
      }
    }
  },
  "agents": {
    "defaults": {
      "model_id": "main"
    }
  }
}
```

This example assumes Ollama is running with `llama3.1:8b` available. If you use
another provider, keep the same structure, set its provider configuration and
upstream model in the `models` entry, and select the entry by its canonical key.
Do not use the upstream model string as `agents.defaults.model_id`.

If terminals and config files are new to you, use
[Start Without Technical Background](../start-without-technical-background.md)
instead.

## Minimal working example

First prove the runtime can answer:

```bash
nanobot agent -m "Hello!"
```

Then open the browser workbench:

```bash
nanobot webui
```

The WebUI starts the local gateway, opens a browser, and keeps persistent chat
sessions for longer work.

## Production notes

- Keep one workspace per project or personal context.
- Give each configured model a stable canonical key under root `models`, with
  its concrete provider and upstream model recorded in that entry.
- Set `agents.defaults.model_id` to the key of the model you want as the default.
- Keep `nanobot gateway` running for WebUI, chat apps, automations, and the
  WebSocket channel.
- Use the Python SDK or OpenAI-compatible API when another program should call
  the agent.

## Security notes

- Do not store API keys directly in shared files; use environment variables.
- Prefer chat app pairing for first setup. Use `allowFrom` only for static
  allowlists, and keep those lists narrow.
- Enable workspace restriction before exposing file or shell tools to other
  users.
- Use a separate workspace for experiments that can modify files.

## Troubleshooting

- `nanobot status` shows the config path, workspace path, and active model.
- If the selected model is unknown, make sure `agents.defaults.model_id` exactly
  matches a key under root `models`.
- If `nanobot agent -m "Hello!"` fails, fix provider setup before opening the
  WebUI or chat apps.
- If the WebUI opens but does not answer, check gateway logs and provider
  credentials.

## Related nanobot docs

- [Quick Start](../quick-start.md)
- [Concepts](../concepts.md)
- [WebUI](../webui.md)
- [Configuration](../configuration.md)
- [Troubleshooting](../troubleshooting.md)
