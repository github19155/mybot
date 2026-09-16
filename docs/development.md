# Development

This page collects contributor-facing notes for extending nanobot. User-facing setup and runtime options live in [`configuration.md`](./configuration.md).

## Adding an LLM Provider

nanobot uses the provider registry in `nanobot/providers/registry.py` as the source of truth for provider credentials and adapter/transport metadata. Canonical model identity lives in `Config.models`: each entry has a stable `model_id`, a concrete `provider`, and the upstream `model` string. Most OpenAI-compatible provider integrations need two provider changes; each usable route also needs a model-catalog entry.

1. Add a `ProviderSpec` entry to `PROVIDERS`:

```python
ProviderSpec(
    name="myprovider",
    keywords=(),
    env_key="MYPROVIDER_API_KEY",
    display_name="My Provider",
    default_api_base="https://api.myprovider.com/v1",
)
```

2. Add a field to `ProvidersConfig` in `nanobot/config/schema.py`:

```python
class ProvidersConfig(BaseModel):
    ...
    myprovider: ProviderConfig = Field(default_factory=ProviderConfig)
```

Environment variables, provider status, and WebUI credential display derive from those two entries.

Add each usable provider/model route separately to the canonical `Config.models` catalog. Consumers store the catalog key as `model_id`; `ModelConfig.provider` must name the concrete provider, and `ModelConfig.model` is the upstream value sent to that provider. Runtime resolution does not infer a provider from a model name, credential, or endpoint.

Useful `ProviderSpec` options:
| Field | Description |
|---|---|
| `default_api_base` | Default OpenAI-compatible base URL. |
| `env_extras` | Additional environment variables derived from the provider config. |
| `model_overrides` | Per-model request parameter overrides. |
| `is_gateway` | Provider can route many model families, like OpenRouter; it does not select the canonical model ID. |
| `strip_model_prefix` | Request formatting applied before sending an upstream model value; it does not change model identity. |
| `supports_max_completion_tokens` | Use `max_completion_tokens` instead of `max_tokens`. |
| `is_transcription_only` | Provider has credentials but cannot serve chat completions. |
|
## Adding a Transcription Provider
Transcription is intentionally split into three layers:

- `Config.models` is the canonical model catalog. `transcription.model_id` references one entry, whose `ModelConfig` supplies the concrete provider and upstream model.
- `nanobot/audio/transcription_registry.py` is an explicit adapter registry that maps concrete provider IDs to transcription adapter implementations. It does not select model IDs or upstream models.
- `nanobot/providers/transcription.py` owns provider-specific HTTP behavior.

Credentials still live under `providers.<provider>` so chat channels and WebUI resolve API keys and API bases the same way.

1. Add provider credentials to `ProvidersConfig`.

```python
class ProvidersConfig(BaseModel):
    ...
    my_stt: ProviderConfig = Field(default_factory=ProviderConfig)
```

2. Add a canonical model entry to `Config.models` and reference its key from `transcription.model_id`.

```python
"my_stt": ModelConfig(
    display_name="My STT",
    provider="my_stt",
    model="my-stt-model",
    capabilities=ModelCapabilities(transcription=True),
)
```

The stable key (`my_stt` above) is the `model_id`. Set `provider` to the exact concrete provider ID and `model` to the upstream value; do not use a provider/model string as the selector.

3. Add a `ProviderSpec` in `nanobot/providers/registry.py`.

For transcription-only providers, set `is_transcription_only=True` so they show up in credential/settings surfaces but stay out of chat model selection.

```python
ProviderSpec(
    name="my_stt",
    keywords=(),
    env_key="MY_STT_API_KEY",
    display_name="My STT",
    default_api_base="https://api.example.com/v1",
    is_transcription_only=True,
)
```

4. Add an adapter class in `nanobot/providers/transcription.py`.

Adapters receive resolved credentials and the upstream model from the canonical `ModelConfig`. They return an empty string for provider errors so channel voice messages fail quietly instead of crashing the agent loop.

```python
class MySTTTranscriptionProvider:
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        api_base: str | None = None,
        language: str | None = None,
    ):
        self.api_key = api_key or os.environ.get("MY_STT_API_KEY")
        self.api_base = api_base or "https://api.example.com/v1"
        self.language = language or None
        self.model = model

    async def transcribe(self, file_path: str | Path) -> str:
        ...
```

5. Register the adapter in `nanobot/audio/transcription_registry.py`.

```python
TranscriptionProviderSpec(
    name="my_stt",
    adapter="nanobot.providers.transcription:MySTTTranscriptionProvider",
)
```

Set `name` to the exact concrete provider ID used by the model catalog entry.

6. Add tests.

At minimum, cover:

- config resolution in `tests/providers/test_transcription.py`
- adapter request/response behavior and retry/error handling
- WebUI settings payload/update behavior in `tests/webui/test_settings_api.py`
- provider brand mapping if the provider appears in Settings

7. Update user-facing docs.

Add the provider to [`configuration.md`](./configuration.md) where users choose `transcription.model_id`, but keep implementation details in this development guide.
