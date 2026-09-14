# Model Domain

Nanobot's model configuration is converging on one canonical model registry.
This document defines the frozen contract that the staged consumer migrations
will target.

## Identity

Three identifiers have different jobs and must never be conflated:

```text
model_id      stable Nanobot machine identity (the registry dict key)
display_name  mutable human-facing name
model         upstream model string sent to the Provider API
```

Example:

```text
models["main"] = ModelConfig(
    display_name="Primary GPT",
    provider="openai",
    model="gpt-5.6",
)
```

Changing `display_name` does not change references. Changing `model` changes the
upstream route while retaining the Nanobot identity. Renaming `model_id` is an
identity change and consumers must be explicitly rebound.

A Provider answers **how Nanobot connects**. A Model answers **which concrete
provider/model route exists and what facts/defaults belong to it**. A consumer
answers **which model ID it uses**.

```text
providers
    ↓
models: Mapping[model_id, ModelConfig]
    ↓
consumer model_id references
```

There is no special default-model object. Default is a consumer assignment to a
canonical model ID.

## Model ID contract

All model references use the same small validation contract:

```text
[a-z][a-z0-9_-]{0,63}
```

IDs are lowercase stable slugs such as `main`, `coder_v2`, or `image-prod`.
Whitespace, uppercase letters, dots, slashes, and provider/upstream model names
are not canonical IDs.

`validate_model_id()` is the shared validator. Consumers must not invent their
own normalization rules.

## Canonical ModelConfig

`nanobot.model_domain.ModelConfig` is the canonical model-domain type:

```text
ModelConfig
├── display_name
├── provider
├── model
├── capabilities
│   ├── text
│   ├── vision
│   ├── image_generation
│   └── transcription
├── context_window_tokens
├── pricing
│   ├── input
│   ├── output
│   └── cache_read
├── generation_defaults
│   ├── temperature
│   ├── max_tokens
│   └── reasoning_effort
├── offering_id
├── pools
└── max_concurrent_requests
```

`provider` is mandatory canonical state and must name a concrete Provider ID.
`provider="auto"` is invalid. Auto-detection may exist later as a model-creation
input convenience, but successful creation must resolve it before producing a
`ModelConfig`.

`generation_defaults` means only default inference parameters when calling the
model. It is not capability and it is not Dream/Fleet/Worker policy.

`pools` remains a model-offering fact because current Fleet routes and scores
`ModelOffering` values by pool. This design does not introduce a central
`allowed_models` list.

## Capabilities

The canonical capability set contains only capabilities with current Nanobot
consumers:

```text
text
vision
image_generation
transcription
```

All capability flags default to false and are declared explicitly per model.
Do not add generic `audio`, `video`, `tools`, or `reasoning` flags merely for
possible future use. `transcription` is intentionally distinct from generic
audio processing.

Provider registry metadata continues to describe adapter/protocol/transport
behavior. Runtime code may apply a real adapter hard limit, but Nanobot must not
create another complete Provider capability registry that duplicates the model
truth source.

## Shared lookup API

Consumers use the small shared API in `nanobot.model_domain`:

```python
model = get_model(models, model_id)
vision_model = require_model_capability(models, model_id, "vision")
```

Both functions work on `Mapping[str, ModelConfig]`. They do not depend on root
`Config`, so Runtime/Fleet, Image, Transcription, and Subagent/Dream can migrate
independently while sharing identical model-ID and capability semantics.

`get_model()` validates the canonical ID and resolves `model_id -> ModelConfig`.
`require_model_capability()` performs the same lookup and rejects a model that
does not declare the requested canonical capability.

## Staged migration

The remaining migration remains split by actual consumer boundaries:

```text
A  Core Model Domain
       ↓
B  Runtime + Fleet
C  Image
D  Transcription
E  Subagent + Dream
       ↓
Final legacy deletion
```

A defines only the canonical model contract and lookup API. It does not migrate
Runtime, Image, Transcription, Subagent, Dream, WebUI, or the root `Config`.

B-E must actively migrate their consumers to this API. `ModelConfig` does not
carry flat legacy fields, `supports_*` aliases, preset aliases, or other staged
compatibility surfaces.

After all consumers have moved, final integration switches the root schema to
`models` plus consumer `model_id` references and deletes `ModelPresetConfig`,
`model_presets`, synthetic `default`, direct AgentDefaults model facts, and the
remaining old migration paths.

## Invariants

1. A concrete model has exactly one canonical `ModelConfig` record.
2. `model_id`, `display_name`, and upstream `model` are separate concepts.
3. Every canonical model has one concrete Provider ID; `auto` is never stored.
4. Consumers reference canonical model IDs and never duplicate model facts.
5. Default is an assignment, not a special model lifecycle.
6. Generation defaults, capabilities, and consumer policy are separate.
7. Capability names are added only when a real consumer requires them.
8. Fleet retains pool-based offering selection; no `allowed_models` center is
   introduced.
9. Provider capability logic exists only for actual adapter hard limits, not as
   a duplicate model-capability truth source.
10. All consumers share `validate_model_id()`, `get_model()`, and
    `require_model_capability()` instead of implementing local model lookup.
