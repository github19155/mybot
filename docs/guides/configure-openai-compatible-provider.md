# How to Configure an OpenAI-Compatible Provider in nanobot

nanobot can call OpenAI-compatible model providers by configuring an `apiBase`,
optional `apiKey`, and a root `models` entry with an explicit provider and
upstream model. Select that entry with `agents.defaults.model_id`.

## What you will build

- a root `models` entry with an explicit provider and upstream model
- a canonical `agents.defaults.model_id` selection
- one successful `nanobot agent` run

## When to use this

Use this for local or hosted services that expose OpenAI-compatible endpoints,
including internal gateways, local model servers, and provider proxies that are
not already named in nanobot.

## Install

```bash
python -m pip install nanobot-ai
nanobot onboard --wizard
```

Verify the endpoint responds before debugging nanobot:

```bash
curl -sS https://api.example.com/v1/models
```

## Minimal working example

Merge this into `~/.nanobot/config.json`:

```json
{
  "providers": {
    "custom": {
      "apiKey": "${CUSTOM_API_KEY}",
      "apiBase": "https://api.example.com/v1"
    }
  },
  "models": {
    "custom_provider_model": {
      "displayName": "Custom provider model",
      "provider": "custom",
      "model": "provider-model-name",
      "capabilities": {
        "text": true
      },
      "contextWindowTokens": 65536,
      "generationDefaults": {
        "maxTokens": 4096,
        "temperature": 0.1
      }
    }
  },
  "agents": {
    "defaults": {
      "model_id": "custom_provider_model"
    }
  }
}
```

`custom_provider_model` is nanobot's canonical selector. The provider's upstream
model string belongs in `models.custom_provider_model.model`; do not use that
upstream string as `agents.defaults.model_id`.

Then run:

```bash
nanobot agent -m "Hello!"
```

## Production notes

- Include the version path in `apiBase` when the service expects `/v1`.
- Use separate provider names for separate endpoints.
- Use a placeholder key such as `EMPTY` only when the endpoint requires a
  non-empty key but does not validate it.
- Leave `apiType` unset for OpenAI-compatible custom endpoints.

## Security notes

- Keep provider keys in environment variables.
- Treat internal model gateways as sensitive network services.
- Do not point nanobot at untrusted proxy endpoints for private workspaces.

## Troubleshooting

- If `curl /models` fails, fix the provider endpoint before changing nanobot.
- If nanobot says the model is unknown, check that
  `models.custom_provider_model.model` matches the upstream model name expected by
  the provider and that `agents.defaults.model_id` is
  `custom_provider_model`.
- If auth fails, confirm whether the provider wants Bearer auth and whether the
  key is present in the environment that starts nanobot.

## Related nanobot docs

- [Provider Cookbook: Custom OpenAI-Compatible Provider](../provider-cookbook.md#recipe-custom-openai-compatible-provider)
- [Providers: Custom OpenAI-Compatible Endpoint](../providers.md#custom-openai-compatible-endpoint)
- [OpenAI-Compatible Agent API](./openai-compatible-agent-api.md)
