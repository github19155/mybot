# Model Domain

Nanobot's model configuration is converging on one canonical model registry.
This document defines the boundary shared by the staged model-domain refactor.

## Three concepts

A Provider answers **how Nanobot connects** to an API or adapter.

A Model answers **what configured model route exists and what facts/defaults
belong to that route**.

A consumer answers **which model ID it uses**. Main/default, Dream, Subagent,
image tools, transcription, and other consumers own their own assignment and
policy; they do not redefine model facts.

```text
providers
    ↓
models: dict[model_id, ModelConfig]
    ↓
consumer model_id references
```

There is no special "default model" object. Default is an assignment from the
Main/default consumer to a canonical model ID.

## Canonical model shape

`nanobot.model_domain.ModelConfig` is the model-domain type.

```text
ModelConfig
├── model
├── provider
├── capabilities
│   ├── text
│   ├── vision
│   ├── tools
│   ├── reasoning
│   ├── image_generation
│   ├── audio
│   └── video
├── context_window_tokens
├── pricing
│   ├── input
│   ├── output
│   ├── cache_read
│   └── cache_write
├── generation_defaults
│   ├── temperature
│   ├── max_tokens
│   └── reasoning_effort
├── offering_id
├── pools
└── max_concurrent_requests
```

`generation_defaults` means only "default inference parameters when calling
this model". It is not a capability and it is not Dream/Fleet/Worker policy.

`pools` is retained as a model-offering fact. Fleet already routes and scores
`ModelOffering` values by pool, so this refactor does not introduce an
`allowed_models` center or replace Fleet's pool-based selection model.

## Capabilities

Model capabilities describe facts about the concrete model. Provider registry
metadata remains responsible for protocol/adapter/transport behavior.

Effective runtime capability may be reduced by a real hard limit in an adapter,
but Nanobot must not create a second complete Provider capability registry that
duplicates model capability facts.

## Staged migration

The model-domain refactor is intentionally split so unrelated runtime paths are
not changed in one large PR:

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

A defines the canonical model type and the migration contract. B-E move their
own consumers to canonical model IDs. The final integration switches the root
configuration to `models` plus consumer `model_id` references and removes the
old `ModelPresetConfig`, `model_presets`, synthetic `default` preset, direct
AgentDefaults model facts, and staged compatibility aliases.

The temporary flat-input/proxy surface in `ModelConfig` is only an integration
bridge for the staged branches. It normalizes into the nested canonical fields;
it is not a second persisted source of truth and must be removed in the final
legacy-deletion step.

## Invariants

1. A concrete model has one canonical configuration record.
2. Consumers reference canonical model IDs and never duplicate model facts.
3. Default is an assignment, not a special model lifecycle.
4. Generation defaults, model capabilities, and consumer policy are distinct.
5. Fleet keeps pool-based offering selection; no new `allowed_models` registry
   is introduced.
6. Provider capability logic is added only for actual adapter hard limits, not
   as a duplicate capability truth source.
7. Persistent stable identity must not depend on runtime guessing after the
   final migration; provider auto-detection is an input convenience, not the
   long-term target representation.
