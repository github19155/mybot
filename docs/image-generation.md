# Image Generation

nanobot can generate and edit images through the `generate_image` tool. Enable the tool in WebUI Settings, then ask for an image normally in chat; the agent decides when to call it and can keep iterating on generated images in the same conversation.

The feature is disabled by default. Configure an image-capable model in **Settings → Models**, then select its canonical model ID in **Settings → Image**. The running gateway applies the change immediately. If that screen is not available, use the manual config below.

## Quick Setup

**WebUI**

1. Add the provider credential under **Settings → Models** if it is not already configured.
2. Create or select a model with a concrete provider, upstream model, and `imageGeneration` capability.
3. Open **Settings → Image**, select that model's canonical model ID, and enable image generation.
4. Save and ask for a simple test image. If the gateway cannot apply the change live, WebUI will prompt you to restart it.

**Manual config**

The image tool selects a canonical model ID from `Config.models`. The selected `ModelConfig` supplies the concrete provider and upstream model, and must declare `capabilities.imageGeneration: true`.

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "${OPENROUTER_API_KEY}"
    }
  },
  "models": {
    "image-prod": {
      "displayName": "Image",
      "provider": "openrouter",
      "model": "openai/gpt-5.4-image-2",
      "capabilities": {
        "imageGeneration": true
      }
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

See [Provider Notes](#provider-notes) for provider-specific credentials, endpoints, and request behavior.

> [!TIP]
> Prefer environment variables for API keys. nanobot resolves `${VAR_NAME}` values from the environment at startup.

## WebUI Usage

1. Open Settings and enable **Image Generation** with a configured image model ID.
2. Describe the image or edit you want in chat.
3. Include an aspect ratio or size in the request when the configured defaults are not suitable.
4. Attach reference images when editing an existing image.

Generated images are rendered as assistant media in the chat. Follow-up prompts such as "make it warmer", "change the background", or "try a 16:9 version" can reuse the most recent generated artifact.

The WebUI hides provider storage details from the user. The selected model ID resolves its provider route internally, and the agent can pass the saved artifact path back to `generate_image` as `reference_images` for iterative edits.

## Configuration Reference

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `tools.imageGeneration.enabled` | boolean | `false` | Register the `generate_image` tool |
| `tools.imageGeneration.modelId` | string or null | `null` | Canonical key of a root `Config.models` entry whose `capabilities.imageGeneration` is `true` |
| `tools.imageGeneration.defaultAspectRatio` | string | `"1:1"` | Default ratio when the prompt/tool call does not specify one |
| `tools.imageGeneration.defaultImageSize` | string | `"1K"` | Default size hint, for example `1K`, `2K`, `4K`, or `1024x1024` |
| `tools.imageGeneration.maxImagesPerTurn` | number | `4` | Maximum `count` accepted by one tool call. Valid range: `1` to `8` |
| `tools.imageGeneration.saveDir` | string | `"generated"` | Relative directory under nanobot's media directory for generated artifacts |

Image selection follows one canonical chain:

```text
tools.imageGeneration.modelId
    ↓
Config.models[modelId]
    ↓
ModelConfig.provider + ModelConfig.model
```

When image generation is enabled, `modelId` must resolve to a `Config.models` entry with `displayName`, `provider`, and `model` fields and `capabilities.imageGeneration: true`. Provider and upstream model are selected from that `ModelConfig`; they are not fields on `tools.imageGeneration`.

Provider settings reuse normal provider config fields:

| Option | Description |
|--------|-------------|
| `providers.<name>.apiKey` | Provider API key. Prefer `${ENV_VAR}` |
| `providers.<name>.apiBase` | Optional custom base URL |
| `providers.<name>.extraHeaders` | Headers merged into provider requests |
| `providers.<name>.extraBody` | Extra JSON fields merged into provider request bodies |
| `providers.<name>.proxy` | Explicit trusted HTTP proxy for provider requests and returned image URL downloads |

For providers that return image URLs, direct downloads use DNS pinning. When an explicit provider `proxy` is configured, nanobot rejects malformed URLs and locally identifiable private/internal targets on the initial URL and every redirect. Hostnames unavailable to local DNS are delegated to that trusted proxy, which owns final DNS resolution and network egress. Process-wide proxy environment variables are not used for these downloads.

## Provider Notes

Each example below puts the provider and upstream model in `Config.models` and selects that entry with `tools.imageGeneration.modelId`.

### OpenRouter

OpenRouter uses a chat-completions style image response:

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "${OPENROUTER_API_KEY}"
    }
  },
  "models": {
    "openrouter-image": {
      "displayName": "OpenRouter Image",
      "provider": "openrouter",
      "model": "openai/gpt-5.4-image-2",
      "capabilities": {
        "imageGeneration": true
      }
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "modelId": "openrouter-image"
    }
  }
}
```

Choose an upstream model that supports image generation. For reference-image edits, choose one that also supports image editing.

### Custom (OpenAI-compatible)

The `custom` image provider fits services that implement the synchronous OpenAI Images API:

```text
POST /v1/images/generations
```

The response must include generated images in `data[].b64_json` or `data[].url`. Native prediction APIs, such as Replicate's `/v1/models/{owner}/{model}/predictions`, are not directly compatible unless an OpenAI-compatible gateway fronts them.

```json
{
  "providers": {
    "custom": {
      "apiKey": "${CUSTOM_IMAGE_API_KEY}",
      "apiBase": "https://api.example.com/v1"
    }
  },
  "models": {
    "custom-image": {
      "displayName": "Custom Image",
      "provider": "custom",
      "model": "your-model-name",
      "capabilities": {
        "imageGeneration": true
      }
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "modelId": "custom-image"
    }
  }
}
```

The `apiBase` is required. The provider sends requests to `{apiBase}/images/generations` using the OpenAI Images API format with `response_format: "b64_json"`. The `apiKey` is optional for an endpoint that does not require authentication. Reference-image edits are not supported by the generic `custom` provider.

`extraBody` can adapt provider-specific request fields because it is merged last into the request body. For example, use `providers.custom.extraBody.response_format` for an endpoint that returns URLs, or set `providers.custom.extraBody.size` for a model that requires an explicit size. `defaultImageSize: "1K"` is mapped to `1024x1024`; other explicit size hints are passed through unchanged.

### AIHubMix

AIHubMix `gpt-image-2-free` uses the unified predictions API:

```text
/v1/models/openai/gpt-image-2-free/predictions
```

```json
{
  "providers": {
    "aihubmix": {
      "apiKey": "${AIHUBMIX_API_KEY}",
      "extraBody": {
        "quality": "low"
      }
    }
  },
  "models": {
    "aihubmix-image": {
      "displayName": "AIHubMix Image",
      "provider": "aihubmix",
      "model": "gpt-image-2-free",
      "capabilities": {
        "imageGeneration": true
      }
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "modelId": "aihubmix-image"
    }
  }
}
```

`quality: low` is optional. It can make image requests faster and less likely to time out.

### MiniMax

MiniMax `image-01` supports text-to-image and reference-image (subject reference) edits. Supported aspect ratios are `1:1`, `16:9`, `4:3`, `3:2`, `2:3`, `3:4`, `9:16`, and `21:9`.

```json
{
  "providers": {
    "minimax": {
      "apiKey": "${MINIMAX_API_KEY}"
    }
  },
  "models": {
    "minimax-image": {
      "displayName": "MiniMax Image",
      "provider": "minimax",
      "model": "image-01",
      "capabilities": {
        "imageGeneration": true
      }
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "modelId": "minimax-image",
      "defaultAspectRatio": "1:1"
    }
  }
}
```

### Gemini

nanobot supports two Gemini image model families through Google's Generative Language API:

| Upstream model | Endpoint | Reference images |
|----------------|----------|-----------------|
| `imagen-4.0-generate-001` | `:predict` | Not supported by this integration |
| `gemini-2.5-flash-image` | `:generateContent` | Supported |

For reference-image edits, select a model entry whose upstream `model` is a Gemini Flash image model:

```json
{
  "providers": {
    "gemini": {
      "apiKey": "${GEMINI_API_KEY}"
    }
  },
  "models": {
    "gemini-image": {
      "displayName": "Gemini Image",
      "provider": "gemini",
      "model": "gemini-2.5-flash-image",
      "capabilities": {
        "imageGeneration": true
      }
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "modelId": "gemini-image"
    }
  }
}
```

Imagen 4 supports the aspect ratios `1:1`, `9:16`, `16:9`, `3:4`, and `4:3`. Unsupported ratios are ignored and the model uses its default. `defaultImageSize` has no effect on Gemini models; sizing is controlled by `defaultAspectRatio`. Reference images passed with an Imagen model are ignored with a warning.

### Ollama
Ollama's native image generation API accepts configured Ollama endpoints. Configure the endpoint in the provider block and keep the image model route in `Config.models`:

```json
{
  "providers": {
    "ollama": {
      "apiBase": "http://localhost:11434/api"
    }
  },
  "models": {
    "ollama-image": {
      "displayName": "Ollama Image",
      "provider": "ollama",
      "model": "x/z-image-turbo",
      "capabilities": {
        "imageGeneration": true
      }
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "modelId": "ollama-image",
      "defaultAspectRatio": "16:9",
      "defaultImageSize": "2K"
    }
  }
}
```

Ollama maps `defaultAspectRatio` and `defaultImageSize` to native `width` and `height` values. Reference images are not supported by this integration.

### StepFun

StepFun `step-image-edit-2` supports text-to-image generation. The `step-1x-medium` variant additionally supports style-reference image edits.

Supported aspect ratios are `1:1`, `16:9`, `9:16`, `3:4`, and `4:3`. Sizes use `WIDTHxHEIGHT`, such as `1024x1024`, `1280x800`, or `800x1280`.

```json
{
  "providers": {
    "stepfun": {
      "apiKey": "${STEPFUN_API_KEY}"
    }
  },
  "models": {
    "stepfun-image": {
      "displayName": "StepFun Image",
      "provider": "stepfun",
      "model": "step-image-edit-2",
      "capabilities": {
        "imageGeneration": true
      }
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "modelId": "stepfun-image"
    }
  }
}
```

The StepFun provider reuses `providers.stepfun` for its LLM and image API credentials. With `step-image-edit-2`, `reference_images` are ignored; select an entry whose upstream model is `step-1x-medium` for style-reference generation.

#### StepPlan (Subscription)

StepPlan uses a different API base URL. Keep the same model selection shape and override `apiBase`:

```json
{
  "providers": {
    "stepfun": {
      "apiKey": "${STEPFUN_API_KEY}",
      "apiBase": "https://api.stepfun.ai/step_plan/v1"
    }
  },
  "models": {
    "stepplan-image": {
      "displayName": "StepPlan Image",
      "provider": "stepfun",
      "model": "step-image-edit-2",
      "capabilities": {
        "imageGeneration": true
      }
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "modelId": "stepplan-image"
    }
  }
}
```

The API key is shared with the standard StepFun provider.

### Zhipu

Zhipu `glm-image` supports text-to-image generation. The API returns temporary image URLs; nanobot downloads and re-encodes them as base64 data URLs.

Supported aspect ratios are `1:1`, `16:9`, `9:16`, `3:4`, and `4:3`. Sizes use `WIDTHxHEIGHT`, such as `1280x1280` or `1728x960`, or an aspect-ratio preset.

```json
{
  "providers": {
    "zhipu": {
      "apiKey": "${ZAI_API_KEY}"
    }
  },
  "models": {
    "zhipu-image": {
      "displayName": "Zhipu Image",
      "provider": "zhipu",
      "model": "glm-image",
      "capabilities": {
        "imageGeneration": true
      }
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "modelId": "zhipu-image"
    }
  }
}
```

Other supported upstream models include `cogview-4`, `cogview-4-250304`, and `cogview-3-flash`. Reference images are not supported by this integration.

### ModelScope

ModelScope API-Inference supports text-to-image generation and image editing through an asynchronous task pattern.

Supported aspect ratios are `1:1`, `16:9`, `9:16`, `3:4`, and `4:3`. Sizes use `WIDTHxHEIGHT`, such as `1024x1024` or `1664x928`, or an aspect-ratio preset.

```json
{
  "providers": {
    "modelscope": {
      "apiKey": "${MODELSCOPE_API_KEY}"
    }
  },
  "models": {
    "modelscope-image": {
      "displayName": "ModelScope Image",
      "provider": "modelscope",
      "model": "Qwen/Qwen-Image-2512",
      "capabilities": {
        "imageGeneration": true
      }
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "modelId": "modelscope-image"
    }
  }
}
```

## Artifacts

Generated images are stored under the active nanobot instance's media directory:

```text
~/.nanobot/media/generated/YYYY-MM-DD/img_<id>.<ext>
~/.nanobot/media/generated/YYYY-MM-DD/img_<id>.json
```

For non-default config locations, the media directory is relative to the active config file's directory.

The JSON sidecar stores:

| Field | Meaning |
|-------|---------|
| `id` | Short generated image ID, such as `img_ab12cd34ef56` |
| `path` | Local image path used internally for follow-up edits |
| `mime` | Detected image MIME type |
| `prompt` | Prompt used for the generation |
| `model` | Upstream model sent to the provider |
| `provider` | Provider selected by the model configuration |
| `source_images` | Reference image paths used for edits |
| `created_at` | Creation timestamp |

Do not paste base64 image payloads into chat. The agent should keep local artifact paths internal unless the user explicitly asks for debugging details.

## Prompting

Good image prompts include:

- Subject and scene.
- Composition, camera, or layout.
- Style, mood, lighting, and color palette.
- Exact text that must appear in the image, quoted.
- Constraints such as "keep the same character" or "preserve the logo".

Example:

```text
A minimal app icon for nanobot: friendly robot head, rounded square, soft blue and white palette, clean vector style, no text
```

For edits, describe what should change and what must stay fixed:

```text
Use the reference image. Keep the same robot and composition, change the palette to warm orange, and add a subtle sunrise background.
```

## Troubleshooting

| Symptom | Check |
|---------|-------|
| `generate_image` is not available | Enable `tools.imageGeneration.enabled`, save an image-capable model in `Config.models`, and set `tools.imageGeneration.modelId` to that model ID |
| No image model is configured | Set `tools.imageGeneration.modelId` to a configured `Config.models` key |
| Selected model lacks image generation | Set `capabilities.imageGeneration` to `true` on the selected `ModelConfig` |
| Missing API key error | Configure the selected model's `ModelConfig.provider` credentials under `providers.<name>.apiKey`; if using `${VAR_NAME}`, confirm the environment variable is visible to the gateway process |
| Provider adapter error | Check that the selected model's concrete `provider` has an image-generation adapter and that its `model` is accepted by that provider |
| Generation times out | Try a smaller output size, set AIHubMix `extraBody.quality` to `"low"`, or retry later |
| Reference image rejected | Reference image paths must be inside the workspace or nanobot media directory and must be valid image files |
