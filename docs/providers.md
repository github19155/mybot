# Providers and Models

Use this page when the first reply fails because of provider/model mismatch, or when you want to adapt the concrete setup example to a different provider. If you already know which provider you want and only need a pasteable setup, use [`provider-cookbook.md`](./provider-cookbook.md).

For normal local setup, open **Settings → Models** in the WebUI to add provider credentials, create entries in the canonical `models` registry, and select the active model. Use the JSON below for manual deployments, local endpoints, provider-specific fields, or diagnosis.

For every setup, answer three questions:

1. Which provider owns the credential or endpoint?
2. What model name does that provider expect?
3. Does the provider need `apiKey`, `apiBase`, OAuth login, cloud credentials, or only a local server URL?

Prefer a named `models` entry for the model/provider pair, then select it with `agents.defaults.modelId`; this selects a top-level `models.<model_id>` entry whose explicit `provider` and upstream `model` values define the runtime route.

## Choose a Provider Explicitly

The docs show concrete provider names so the JSON is copyable, not because nanobot ranks providers. Start from the service or endpoint you actually control:

| If you have... | Configure... |
|---|---|
| An API key from a hosted provider or gateway | That provider's `providers.<name>.apiKey`, then a `models.<model_id>` entry with that concrete provider and a model ID from the service. |
| An OpenCode Zen or Go key | `providers.opencodeZen.apiKey` or `providers.opencodeGo.apiKey`, then a model entry with `provider: "opencode_zen"` or `provider: "opencode_go"`. |
| A company proxy or regional endpoint | The matching provider block plus `apiBase` if the proxy gives you a URL. |
| A local OpenAI-compatible server | A local provider block such as `ollama`, `vllm`, `lmStudio`, or `custom`, usually with `apiBase`, plus an explicit model entry. |
| An OAuth-based account | Run the matching `nanobot provider login ...` command, then reference the resulting concrete provider from a model entry. |
| No provider yet | Pick one outside nanobot based on account access, pricing, regional availability, privacy requirements, and the model IDs you need. Then come back with its key and model ID. |

## Minimal Shape

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  },
  "agents": {
    "defaults": {
      "modelId": "primary"
    }
  },
  "models": {
    "primary": {
      "displayName": "Primary",
      "provider": "openrouter",
      "model": "anthropic/claude-opus-4.5",
      "capabilities": {
        "text": true
      },
      "contextWindowTokens": 65536,
      "generationDefaults": {
        "maxTokens": 8192,
        "temperature": 0.1
      }
    }
  }
}
```

The provider config gives nanobot credentials and endpoint details. The model registry entry combines one concrete provider with its upstream model route. Agent defaults select a canonical model ID. Replace the example provider and model facts together; mixing a credential from one provider with a model entry for another is the most common first-run failure.

## Provider, Model, API Key, and Base URL

These fields answer different questions:

| Field | Where it lives | Meaning |
|---|---|---|
| `provider` | `models.<model_id>.provider` | Concrete nanobot provider adapter for this model entry. |
| `model` | `models.<model_id>.model` | Upstream model ID expected by that provider or gateway; it is not a consumer selector. |
| `apiKey` | `providers.<provider>.apiKey` | Credential for that provider. Use `${ENV_VAR}` for secrets. |
| `apiBase` | `providers.<provider>.apiBase` | HTTP base URL of the provider endpoint. |
| `proxy` | `providers.<provider>.proxy` | Optional HTTP proxy for this provider only. Supported for OpenAI-compatible providers, OpenAI Codex, and xAI OAuth. |

You usually omit `apiBase` for hosted built-in providers such as OpenRouter, Anthropic direct, OpenAI direct, Groq, or Bedrock because nanobot knows their default endpoints. Set `apiBase` for `custom`, local OpenAI-compatible servers, provider proxies, regional endpoints, or subscription endpoints. Include the API version path when the endpoint requires it, for example `https://api.example.com/v1` or `http://localhost:11434/v1`.

Use `proxy` when one provider must send HTTP traffic through a proxy without changing process-wide `HTTP_PROXY` / `HTTPS_PROXY`. This is supported for providers that use nanobot's OpenAI-compatible client, including `openai`, `custom`, named custom providers, OpenRouter-style gateways, local OpenAI-compatible servers, and similar registry entries. It is also supported for `openai_codex` and `xai_grok`, including OAuth token exchange/refresh and model requests. Native provider backends such as `anthropic`, `bedrock`, `azure_openai`, and `github_copilot` reject `proxy`; use their endpoint-specific configuration instead.

## Common Provider Patterns

### OpenRouter Gateway

Gateway-style setup for model IDs served through OpenRouter.

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "${OPENROUTER_API_KEY}"
    }
  },
  "agents": {
    "defaults": {
      "modelId": "primary"
    }
  },
  "models": {
    "primary": {
      "displayName": "Primary",
      "provider": "openrouter",
      "model": "anthropic/claude-opus-4.5",
      "capabilities": {
        "text": true
      },
      "contextWindowTokens": 65536,
      "generationDefaults": {
        "maxTokens": 8192
      }
    }
  }
}
```

Use the model ID exactly as OpenRouter lists it.

To opt into OpenRouter server-managed search and fetch, add:

```json
{
  "providers": {
    "openrouter": {
      "extraBody": {
        "tools": [
          { "type": "openrouter:web_search" },
          { "type": "openrouter:web_fetch" }
        ]
      }
    }
  }
}
```

Chat Completions-compatible OpenRouter
[server tools](https://openrouter.ai/docs/guides/features/server-tools), such as those above, are
appended to nanobot's generated functions. This keeps unrelated local tools such as `write_file`
available in the same request. Responses-only server tools require an API surface that the
OpenRouter provider does not currently enable.

### OrcaRouter Gateway

[OrcaRouter](https://www.orcarouter.ai) is an OpenAI-compatible model routing gateway. Configure
the built-in `orcarouter` provider and use a model ID from OrcaRouter's catalog:

```json
{
  "providers": {
    "orcarouter": {
      "apiKey": "${ORCAROUTER_API_KEY}"
    }
  },
  "agents": {
    "defaults": {
      "modelId": "primary"
    }
  },
  "models": {
    "primary": {
      "displayName": "Primary",
      "provider": "orcarouter",
      "model": "orcarouter/auto",
      "capabilities": {
        "text": true
      },
      "contextWindowTokens": 65536,
      "generationDefaults": {
        "maxTokens": 8192
      }
    }
  }
}
```

Use an explicit upstream model ID exactly as OrcaRouter lists it. Gateway routing choices belong to the upstream service; nanobot does not select a provider from a model-name prefix. OrcaRouter API keys start with `sk-orca-`. The WebUI can load the account's model catalog after the API key is saved under **Settings → Models**.

### Eden AI Gateway

Eden AI exposes an OpenAI-compatible chat-completions endpoint at
`https://api.edenai.run/v3`. Configure the built-in `edenai` provider and use
the full `provider/model` identifier listed by Eden AI:

```json
{
  "providers": {
    "edenai": {
      "apiKey": "${EDENAI_API_KEY}"
    }
  },
  "agents": {
    "defaults": {
      "modelId": "primary"
    }
  },
  "models": {
    "primary": {
      "displayName": "Primary",
      "provider": "edenai",
      "model": "anthropic/claude-sonnet-4-5",
      "capabilities": {
        "text": true
      },
      "generationDefaults": {
        "maxTokens": 8192
      }
    }
  }
}
```

Nanobot sends the upstream model ID unchanged. Use
Eden AI's [model listing](https://www.edenai.co/docs/v3/llms/listing-models)
to choose a currently available model. The WebUI can also load that catalog
after the Eden AI API key is saved under **Settings → Models**.

### OpenCode Zen and Go

OpenCode Zen and OpenCode Go are OpenCode-managed gateways for coding-agent models.
They share `OPENCODE_API_KEY`, but use separate provider config keys and default base
URLs in nanobot.

```json
{
  "providers": {
    "opencodeZen": {
      "apiKey": "${OPENCODE_API_KEY}"
    }
  },
  "agents": {
    "defaults": {
      "modelId": "primary"
    }
  },
  "models": {
    "primary": {
      "displayName": "Primary",
      "provider": "opencode_zen",
      "model": "opencode/deepseek-v4-pro",
      "capabilities": {
        "text": true
      },
      "contextWindowTokens": 65536,
      "generationDefaults": {
        "maxTokens": 8192
      }
    }
  }
}
```

For OpenCode Go, switch the provider block and model entry:

```json
{
  "providers": {
    "opencodeGo": {
      "apiKey": "${OPENCODE_API_KEY}"
    }
  },
  "models": {
    "primary": {
      "displayName": "Primary",
      "provider": "opencode_go",
      "model": "opencode-go/deepseek-v4-flash",
      "capabilities": {
        "text": true
      },
      "contextWindowTokens": 65536,
      "generationDefaults": {
        "maxTokens": 8192
      }
    }
  }
}
```

OpenCode documents the upstream model IDs for each endpoint. Store the exact upstream string in the model entry; the canonical registry key remains the only Nanobot selector. Use model IDs that OpenCode lists under
the `chat/completions` endpoint; models listed only under `responses`,
`messages`, or provider-specific endpoints are not handled by this
OpenAI-compatible provider path.

### Anthropic Direct

```json
{
  "providers": {
    "anthropic": {
      "apiKey": "${ANTHROPIC_API_KEY}"
    }
  },
  "agents": {
    "defaults": {
      "modelId": "primary"
    }
  },
  "models": {
    "primary": {
      "displayName": "Primary",
      "provider": "anthropic",
      "model": "claude-opus-4-5",
      "capabilities": {
        "text": true
      },
      "contextWindowTokens": 200000,
      "generationDefaults": {
        "maxTokens": 8192
      }
    }
  }
}
```

Anthropic direct uses the native Anthropic provider. Do not use an OpenRouter model ID unless the provider is OpenRouter.

If you use an Anthropic-compatible proxy, keep the provider as `anthropic` and override `apiBase`:

```json
{
  "providers": {
    "anthropic": {
      "apiKey": "${ANTHROPIC_API_KEY}",
      "apiBase": "https://anthropic-proxy.example.com"
    }
  },
  "models": {
    "primary": {
      "displayName": "Primary",
      "provider": "anthropic",
      "model": "claude-sonnet-4-5",
      "capabilities": {
        "text": true
      }
    }
  }
}
```

Arbitrary custom provider names are OpenAI-compatible only; they do not use the Anthropic Messages API request format.

### OpenAI Direct

```json
{
  "providers": {
    "openai": {
      "apiKey": "${OPENAI_API_KEY}"
    }
  },
  "agents": {
    "defaults": {
      "modelId": "primary"
    }
  },
  "models": {
    "primary": {
      "displayName": "Primary",
      "provider": "openai",
      "model": "gpt-5",
      "capabilities": {
        "text": true
      },
      "contextWindowTokens": 128000,
      "generationDefaults": {
        "maxTokens": 8192
      }
    }
  }
}
```

`providers.openai.apiType` may be set when you need to force a specific OpenAI API surface. Other providers reject `apiType`; leave it unset outside `providers.openai`. Replace the model with a model ID available to your OpenAI account. Direct OpenAI Responses, OpenAI Codex, Azure OpenAI Responses, and eligible GitHub Copilot models share [opaque Responses state retention](./configuration.md#responses-state-and-compaction); native compaction is enabled only where the backend supports it. The WebUI exposes provider-native switches for OpenAI web search, Codex Fast mode, DeepSeek web search, and Grok X Search. These switches write the corresponding raw provider request fields under `extraBody`.

DeepSeek is the model-level exception in the OpenAI-compatible provider: `deepseek-v4-flash` and `deepseek-v4-pro` automatically use DeepSeek's native Responses API. Its native `web_search` tool is enabled by default and shows its lifecycle in WebUI chat activity; set `providers.deepseek.extraBody.tools` to `[]` to disable it.

### Custom OpenAI-Compatible Endpoint

The `custom` provider fits one OpenAI-compatible endpoint that is not represented by a named provider.

```json
{
  "providers": {
    "custom": {
      "apiKey": "${CUSTOM_API_KEY}",
      "apiBase": "https://example.com/v1"
    }
  },
  "agents": {
    "defaults": {
      "modelId": "primary"
    }
  },
  "models": {
    "primary": {
      "displayName": "Primary",
      "provider": "custom",
      "model": "provider-model-name",
      "capabilities": {
        "text": true
      },
      "contextWindowTokens": 65536,
      "generationDefaults": {
        "maxTokens": 8192
      }
    }
  }
}
```

`custom` does not infer a default base URL. Set `apiBase`.

If you have more than one custom OpenAI-compatible endpoint, give each endpoint its own provider key under `providers` and use that same key in its explicit `models.<model_id>.provider` field. The key can be a name that makes sense in your environment, such as `companyProxy`, `tenant-a`, or `dev-local`.

```json
{
  "providers": {
    "companyProxy": {
      "apiKey": "${COMPANY_PROXY_API_KEY}",
      "apiBase": "https://llm-proxy.example.com/v1"
    },
    "tenant-a": {
      "apiBase": "https://tenant-a.example.com/v1"
    }
  },
  "agents": {
    "defaults": {
      "modelId": "company"
    }
  },
  "models": {
    "company": {
      "displayName": "Company",
      "provider": "companyProxy",
      "model": "gpt-4o-mini",
      "capabilities": {
        "text": true
      },
      "contextWindowTokens": 65536,
      "generationDefaults": {
        "maxTokens": 8192
      }
    },
    "tenanta": {
      "displayName": "Tenanta",
      "provider": "tenant-a",
      "model": "served-model-name",
      "capabilities": {
        "text": true
      },
      "contextWindowTokens": 65536,
      "generationDefaults": {
        "maxTokens": 8192
      }
    }
  }
}
```

Custom provider keys are treated as direct OpenAI-compatible providers. `apiBase` is required because nanobot cannot know the endpoint URL. `apiKey` is optional for local servers or private proxies that do not require one. Choose a name that does not conflict with a built-in provider name or alias, such as `openai`, `openai-codex`, `github-copilot`, or `lm-studio`. Do not set `apiType` on custom provider keys; `apiType` is only for `providers.openai`.

If your custom endpoint documents a nonstandard thinking toggle, set `providers.<name>.thinkingStyle` to `thinking_type`, `enable_thinking`, or `reasoning_split`; nanobot then maps `reasoningEffort` onto that provider-specific request body. Leave it unset for ordinary OpenAI-compatible endpoints.

This named custom provider path is not for Anthropic-compatible endpoints. For Anthropic-compatible proxies, use `providers.anthropic.apiBase` and set the model entry provider to `anthropic`.

### ModelScope

ModelScope (魔搭社区) exposes an OpenAI-compatible LLM endpoint plus a separate async image generation API. Both are covered by the built-in `modelscope` provider.

Create a ModelScope [access token](https://modelscope.cn/my/myaccesstoken), then choose a model whose page exposes API-Inference. The example below uses [`Qwen/Qwen3-32B`](https://modelscope.cn/models/Qwen/Qwen3-32B); hosted availability and quotas are controlled by ModelScope. See the official [API-Inference guide](https://modelscope.cn/docs/model-service/API-Inference/intro) for current service details.

```json
{
  "providers": {
    "modelscope": {
      "apiKey": "${MODELSCOPE_API_KEY}"
    }
  },
  "agents": {
    "defaults": {
      "modelId": "primary"
    }
  },
  "models": {
    "primary": {
      "displayName": "Primary",
      "provider": "modelscope",
      "model": "Qwen/Qwen3-32B",
      "capabilities": {
        "text": true
      },
      "contextWindowTokens": 65536,
      "generationDefaults": {
        "maxTokens": 8192
      }
    }
  }
}
```

Use an inference-enabled model ID exactly as ModelScope publishes it (usually `Namespace/model-name`). The default base URL is `https://api-inference.modelscope.cn/v1`; override `providers.modelscope.apiBase` only if your account routes through a different host. Chat model IDs may optionally be prefixed with `modelscope/`; nanobot strips that routing prefix before sending the request.

ModelScope image generation uses a canonical image model entry and `tools.imageGeneration.modelId`:

```json
{
  "models": {
    "image-prod": {
      "displayName": "Image production",
      "provider": "modelscope",
      "model": "Qwen/Qwen-Image-2512",
      "capabilities": { "imageGeneration": true }
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "modelId": "image-prod"
    }
  }
}
```

Use the image model's exact upstream ModelScope ID in its `models.<model_id>.model` field. The image client sends this value unchanged and handles ModelScope's async submit/poll flow. See [Image Generation](./image-generation.md#modelscope) for supported sizes, aspect ratios, and complete provider configuration.

### Ollama

Start Ollama separately, then point nanobot at the OpenAI-compatible endpoint.

```json
{
  "providers": {
    "ollama": {
      "apiBase": "http://localhost:11434/v1"
    }
  },
  "agents": {
    "defaults": {
      "modelId": "primary"
    }
  },
  "models": {
    "primary": {
      "displayName": "Primary",
      "provider": "ollama",
      "model": "llama3.2",
      "capabilities": {
        "text": true
      },
      "contextWindowTokens": 32768,
      "generationDefaults": {
        "maxTokens": 4096
      }
    }
  }
}
```

Most Ollama setups do not require an API key.

Ollama renders the OpenAI-compatible messages and tools through each model's chat
template. If ordinary model responses are fast but tool-using turns show low prompt
cache reuse, diagnose the rendered template before changing nanobot's context or
memory settings. The
[Ollama prompt-cache guide](./guides/configure-ollama-prompt-cache.md) explains the
log pattern and a tested `llama3.1:8b` workaround.

### vLLM or Other Local OpenAI-Compatible Server

```json
{
  "providers": {
    "vllm": {
      "apiBase": "http://127.0.0.1:8000/v1",
      "apiKey": "EMPTY"
    }
  },
  "agents": {
    "defaults": {
      "modelId": "primary"
    }
  },
  "models": {
    "primary": {
      "displayName": "Primary",
      "provider": "vllm",
      "model": "served-model-name",
      "capabilities": {
        "text": true
      },
      "contextWindowTokens": 65536,
      "generationDefaults": {
        "maxTokens": 8192
      }
    }
  }
}
```

Some OpenAI-compatible local servers require any non-empty API key even when they do not validate it.

### LM Studio

```json
{
  "providers": {
    "lmStudio": {
      "apiBase": "http://localhost:1234/v1"
    }
  },
  "agents": {
    "defaults": {
      "modelId": "primary"
    }
  },
  "models": {
    "primary": {
      "displayName": "Primary",
      "provider": "lm_studio",
      "model": "local-model",
      "capabilities": {
        "text": true
      },
      "contextWindowTokens": 32768,
      "generationDefaults": {
        "maxTokens": 4096
      }
    }
  }
}
```

Config keys may be camelCase or snake_case. Provider names belong in the concrete `models.<model_id>.provider` field and should use the registry name, such as `lm_studio`.

### AWS Bedrock

Bedrock can use the AWS credential chain, profile, region, or Bedrock bearer token depending on your AWS setup.

```json
{
  "providers": {
    "bedrock": {
      "region": "us-east-1",
      "profile": "default"
    }
  },
  "agents": {
    "defaults": {
      "modelId": "primary"
    }
  },
  "models": {
    "primary": {
      "displayName": "Primary",
      "provider": "bedrock",
      "model": "bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0",
      "capabilities": {
        "text": true
      },
      "contextWindowTokens": 200000,
      "generationDefaults": {
        "maxTokens": 8192
      }
    }
  }
}
```

See [`configuration.md#providers`](./configuration.md#providers) for Bedrock-specific notes.

### OAuth Providers

Some providers do not use API keys in `config.json`.

For OpenAI Codex:

```bash
nanobot provider login openai-codex --set-main
```

The WebUI reads the account's Codex model catalog online, including current
context-window and reasoning-effort metadata. A small compatible catalog remains
available when the service cannot be reached.

For an eligible X Premium / Grok subscription:

```bash
nanobot provider login xai-grok --set-main
```

This selects `xai-grok/grok-4.6`. The WebUI model selector reads xAI's online
model catalog, so newly available subscription models appear without a nanobot
release. Online metadata is cached and enriched with nanobot's curated labels;
if xAI is temporarily unavailable, nanobot uses the last successful catalog or
a small built-in fallback instead of emptying the selector. The same catalog
controls whether the provider exposes the hosted `x_search` tool; models that do
not advertise support continue without hosted X Search.
When enabled, Grok can search current X posts and return inline source links
without invoking a local nanobot tool. Credentials are stored under the
active instance's `auth/xai.json` (normally `~/.nanobot/auth/xai.json`), not in
`config.json` and not in Grok Build's credential file.
Hosted X Search remains enabled by default and can be disabled with the WebUI
switch or `providers.xaiGrok.extraBody.tools: []`.

The login is xAI subscription OAuth, not X Developer OAuth. It follows the
public client contract documented and implemented by
[Grok Build](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/02-authentication.md);
xAI may change that upstream contract independently of nanobot.

For GitHub Copilot:

```bash
nanobot provider login github-copilot --set-main
```

The WebUI reads the models enabled for the signed-in Copilot account. nanobot
lists entries that support its current Copilot chat-completions or Responses
transport and hides models that it cannot route safely.

Each command authenticates the selected provider and makes its current default model active. OpenAI Codex and eligible GitHub Copilot models participate in [Responses state retention](./configuration.md#responses-state-and-compaction), while native compaction remains provider-capability-specific. See [`troubleshooting.md`](./troubleshooting.md#provider-and-model-problems) for proxy, headless-login, model-name, and config-key errors.

## Model Selection

Nanobot resolves model selection only through the canonical top-level `models` registry. `agents.defaults.modelId`, session `/model` choices, and other consumers reference a configured registry key. Each `models.<model_id>` entry explicitly contains a concrete `provider`, upstream `model`, capabilities, context facts, and generation defaults.

Provider names, display names, upstream model IDs, and slash prefixes are never selector fallbacks or provider-inference hints. If the selected model ID is missing or its provider route fails, nanobot reports the error; it does not guess another provider or model.

## Models

Use `models.<model_id>` entries for explicit model choices. The model registry key is the selector; provider and upstream model values are facts on that entry.

```json
{
  "models": {
    "fast": {
      "displayName": "Fast",
      "provider": "openrouter",
      "model": "anthropic/claude-sonnet-4.5",
      "capabilities": { "text": true },
      "generationDefaults": { "maxTokens": 4096 }
    },
    "local": {
      "displayName": "Local",
      "provider": "ollama",
      "model": "llama3.2",
      "capabilities": { "text": true }
    }
  },
  "agents": { "defaults": { "modelId": "fast" } }
}
```

Use `/model <model_id>` to select a configured entry for the current session. A request stays on the selected route; provider retries do not switch it to another configured entry.

## Failure behavior

A request stays on the model/provider route selected before admission. Provider implementations may retry that same route according to their retry policy. If retries are exhausted, the request fails explicitly instead of switching to another configured model or provider. Use `/model <model_id>`, session model selection, Subagent role bindings, Dream policy, or Model Fleet when you want an explicit different model choice before a request starts.

## Quick Checks

Run these before debugging a chat app:

```bash
nanobot status
nanobot agent -m "Hello!"
```

If `nanobot agent -m "Hello!"` fails:

| Symptom | Likely cause |
|---|---|
| 401, unauthorized, invalid API key | Key is missing, expired, copied with whitespace, or stored under the wrong provider |
| model not found | Model ID does not exist for the selected provider or gateway |
| connection refused | Local provider server is not running or `apiBase` points to the wrong port |
| provider not found | The active model entry uses a misspelled provider; use registry names such as `openrouter`, `anthropic`, `ollama`, `vllm`, `lm_studio` |
| works in CLI but not chat app | Provider is fine; debug gateway/channel setup in [`chat-apps.md`](./chat-apps.md) or [`troubleshooting.md`](./troubleshooting.md) |

For the complete provider table and advanced provider-specific notes, see [`configuration.md#providers`](./configuration.md#providers).
