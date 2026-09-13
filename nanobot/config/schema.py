"""Configuration schema using Pydantic."""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast

from pydantic import AliasChoices, ConfigDict, Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from nanobot.config.timezone import detect_system_timezone
from nanobot.config_base import Base
from nanobot.permission_config import PermissionConfig

if TYPE_CHECKING:
    from nanobot.agent.tools.cli_apps import CliAppsToolConfig
    from nanobot.agent.tools.filesystem import FileToolsConfig
    from nanobot.agent.tools.image_analysis import ImageAnalysisToolConfig
    from nanobot.agent.tools.image_generation import ImageGenerationToolConfig
    from nanobot.agent.tools.self import MyToolConfig
    from nanobot.agent.tools.shell import ExecToolConfig
    from nanobot.agent.tools.web import WebToolsConfig


class ChannelsConfig(Base):
    """Configuration for chat channels."""

    model_config = ConfigDict(extra="allow")
    send_progress: bool = True
    send_tool_hints: bool = True
    show_reasoning: bool = True
    extract_document_text: bool = True
    send_max_retries: int = Field(default=3, ge=0, le=10)
    transcription_provider: str = "groq"
    transcription_language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")


class TranscriptionConfig(Base):
    """Cross-channel audio transcription configuration."""

    enabled: bool = True
    provider: str | None = None
    model: str | None = None
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")
    max_duration_sec: int = Field(default=120, ge=1, le=600)
    max_upload_mb: int = Field(default=25, ge=1, le=100)


class DreamConfig(Base):
    """Main-adjustable policy for read-only background Dream cognition.

    These fields are the only Dream policy source. Runtime state lives under the
    workspace, but state files never override policy. Bounds are deliberately
    enforced here so Main can adapt ordinary policy without expanding its own
    safety/resource envelope.
    """

    enabled: bool = True
    cooldown_minutes: int = Field(default=30, ge=5, le=24 * 60)
    idle_minutes: int = Field(default=10, ge=0, le=24 * 60)
    pressure_entries: int = Field(default=20, ge=1, le=500)
    max_defer_minutes: int = Field(default=360, ge=1, le=7 * 24 * 60)
    max_entries_per_run: int = Field(default=40, ge=1, le=100)
    max_runs_per_day: int = Field(default=8, ge=1, le=24)
    retention_days: int = Field(default=30, ge=1, le=90)
    poll_interval_seconds: int = Field(default=30, ge=5, le=300)
    model_override: str | None = None
    fallback_preset: str | None = None
    pools: dict[str, str] = Field(default_factory=lambda: {
        "dream.consolidation": "dream",
        "dream.extraction": "dream",
        "dream.governance": "dream",
        "dream.housekeeping": "dream",
    })

    @field_validator("model_override", "fallback_preset")
    @classmethod
    def _normalize_optional_preset(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("pools")
    @classmethod
    def _validate_pools(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {
            "dream.consolidation",
            "dream.extraction",
            "dream.governance",
            "dream.housekeeping",
        }
        normalized: dict[str, str] = {}
        for raw_key, raw_pool in value.items():
            key = raw_key.strip().lower()
            pool = raw_pool.strip().lower()
            if key not in allowed:
                raise ValueError(f"unsupported Dream workload pool {raw_key!r}")
            if not pool:
                raise ValueError("Dream pool name must not be blank")
            normalized[key] = pool
        return normalized

    def pool_for(self, workload: str) -> str:
        return self.pools.get(workload, "dream")


class InlineFallbackConfig(Base):
    """One inline fallback model configuration."""

    model: str
    provider: str
    max_tokens: int | None = None
    context_window_tokens: int | None = None
    supports_vision: bool = False
    temperature: float | None = None
    reasoning_effort: str | None = None


FallbackCandidate = str | InlineFallbackConfig


class FleetRetentionConfig(Base):
    """Retention for passive model-fleet telemetry."""

    raw_calls_days: int = Field(default=30, ge=1)
    hourly_days: int = Field(default=180, ge=1)
    score_full_days: int = Field(default=30, ge=1)


class ModelFleetConfig(Base):
    """Runtime model-fleet observation and scoring policy."""

    enabled: bool = True
    score_refresh_minutes: int = Field(default=60, ge=1)
    retention: FleetRetentionConfig = Field(default_factory=FleetRetentionConfig)


class ModelPresetConfig(Base):
    """A named model route plus generation and fleet-facing facts."""

    model: str
    provider: str = "auto"
    max_tokens: int = 8192
    context_window_tokens: int = 200_000
    supports_vision: bool = False
    supports_image_generation: bool = False
    temperature: float = 0.1
    reasoning_effort: str | None = None
    # Fleet facts belong to the concrete provider/model route. They describe
    # price/capability only; performance scores come from observed traffic.
    offering_id: str | None = None
    fleet_pools: list[str] = Field(default_factory=list)
    input_cost_per_million: float | None = Field(default=None, ge=0.0)
    output_cost_per_million: float | None = Field(default=None, ge=0.0)
    cached_input_cost_per_million: float | None = Field(default=None, ge=0.0)
    max_concurrent_requests: int | None = Field(default=None, ge=1)

    @field_validator("offering_id")
    @classmethod
    def _validate_offering_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", normalized):
            raise ValueError("offering_id contains unsupported characters")
        return normalized

    @field_validator("fleet_pools")
    @classmethod
    def _normalize_fleet_pools(cls, value: list[str]) -> list[str]:
        pools = [item.strip().lower() for item in value if item.strip()]
        return list(dict.fromkeys(pools))

    def to_generation_settings(self) -> Any:
        from nanobot.providers.base import GenerationSettings
        return GenerationSettings(
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            reasoning_effort=self.reasoning_effort,
        )


class SystemPromptOverrideConfig(Base):
    """A custom system prompt bound to exact model IDs."""

    prompt: str
    models: list[str] = Field(min_length=1)


SubagentRoleName = str
SubagentThinking = Literal[
    "none", "minimal", "low", "medium", "high", "xhigh", "max", "adaptive",
]
SubagentContext = Literal["fresh", "fork"]

_BUILTIN_SUBAGENT_ROLE_NAMES = (
    "general", "researcher", "planner", "coder", "debugger", "tester", "writer", "analyst",
)


class SubagentRoleConfig(Base):
    """A builtin override or a complete custom subagent role definition."""

    description: str | None = None
    system_prompt: str | None = None
    tools: list[str] | None = None
    model: str | None = None
    model_preset: str | None = None
    thinking: SubagentThinking | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    timeout_seconds: float | None = Field(default=None, gt=0.0)
    context: SubagentContext | None = None
    disabled: bool = False

    @model_validator(mode="after")
    def _validate_model_selection(self) -> "SubagentRoleConfig":
        if self.model and self.model_preset:
            raise ValueError("model and model_preset are mutually exclusive")
        return self


class AgentDefaults(Base):
    """Default agent configuration."""

    workspace: str = "~/.nanobot/workspace"
    model_preset: str | None = None
    supports_vision: bool = False
    model: str = "anthropic/claude-opus-4-5"
    provider: str = "auto"
    max_tokens: int = 8192
    context_window_tokens: int = 200_000
    context_block_limit: int | None = None
    temperature: float = 0.1
    fallback_models: list[FallbackCandidate] = Field(default_factory=list)
    max_tool_iterations: int = 200
    max_concurrent_subagents: int = Field(default=16, ge=1)
    max_tool_result_chars: int = 16_000
    provider_retry_mode: Literal["standard", "persistent"] = "standard"
    tool_hint_max_length: int = Field(
        default=40,
        ge=20,
        le=500,
        validation_alias=AliasChoices("toolHintMaxLength"),
        serialization_alias="toolHintMaxLength",
    )
    reasoning_effort: str | None = None
    timezone: str = "UTC"
    timezone_mode: Literal["auto", "manual"] = "auto"
    bot_name: str = "nanobot"
    bot_icon: str = "🐈"
    unified_session: bool = False
    disabled_skills: list[str] = Field(default_factory=list)
    session_ttl_minutes: int = Field(
        default=15,
        ge=0,
        validation_alias=AliasChoices("idleCompactAfterMinutes", "sessionTtlMinutes"),
        serialization_alias="idleCompactAfterMinutes",
    )
    idle_compact_check_interval_seconds: int = Field(default=60, ge=0)
    dream: DreamConfig = Field(default_factory=DreamConfig)

    @model_validator(mode="before")
    @classmethod
    def resolve_timezone(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(cast(dict[str, object], value))
        timezone_mode = data.get("timezoneMode", data.get("timezone_mode"))
        if timezone_mode is None:
            timezone_mode = "manual" if "timezone" in data else "auto"
            data["timezoneMode"] = timezone_mode
        if timezone_mode == "auto":
            data["timezone"] = detect_system_timezone()
        return data

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError(f"unknown timezone {value!r}") from None
        return value


class AgentsConfig(Base):
    """Agent configuration."""
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ProviderConfig(Base):
    """LLM provider configuration."""

    display_name: str | None = Field(default=None, exclude_if=lambda value: value is None)
    api_key: str | None = Field(default=None, repr=False)
    api_base: str | None = None
    api_type: Literal["auto", "chat_completions", "responses"] = "auto"
    extra_headers: dict[str, str] | None = None
    extra_body: dict[str, Any] | None = None
    extra_query: dict[str, str] | None = None
    proxy: str | None = None
    thinking_style: str | None = None
    # Admission settings are provider-account scoped. Distinct provider aliases
    # therefore remain independent even when they route the same model.
    max_concurrent_requests: int | None = Field(default=None, ge=1)
    rate_limit_scope: Literal["provider", "model"] = "provider"

    _VALID_THINKING_STYLES: ClassVar[tuple[str, ...]] = (
        "thinking_type",
        "enable_thinking",
        "reasoning_split",
    )

    @field_validator("thinking_style")
    @classmethod
    def _validate_thinking_style(cls, v: str | None) -> str | None:
        if not v:
            return v
        if v not in cls._VALID_THINKING_STYLES:
            raise ValueError(
                f"Invalid thinking_style {v!r}. "
                f"Must be one of: {', '.join(repr(s) for s in cls._VALID_THINKING_STYLES)} "
                f"(or empty/omitted)."
            )
        return v


class BedrockProviderConfig(ProviderConfig):
    """AWS Bedrock Runtime provider configuration."""
    region: str | None = None
    profile: str | None = None


class ProvidersConfig(Base):
    """Configuration for LLM providers, including dynamic custom providers."""

    model_config = ConfigDict(extra="allow")
    custom: ProviderConfig = Field(default_factory=ProviderConfig)
    azure_openai: ProviderConfig = Field(default_factory=ProviderConfig)
    bedrock: BedrockProviderConfig = Field(default_factory=BedrockProviderConfig)
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    orcarouter: ProviderConfig = Field(default_factory=ProviderConfig)
    assemblyai: ProviderConfig = Field(default_factory=ProviderConfig)
    huggingface: ProviderConfig = Field(default_factory=ProviderConfig)
    skywork: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)
    modelscope: ProviderConfig = Field(default_factory=ProviderConfig)
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    ollama: ProviderConfig = Field(default_factory=ProviderConfig)
    lm_studio: ProviderConfig = Field(default_factory=ProviderConfig)
    atomic_chat: ProviderConfig = Field(default_factory=ProviderConfig)
    ovms: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    kimi_coding: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax_anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    mistral: ProviderConfig = Field(default_factory=ProviderConfig)
    stepfun: ProviderConfig = Field(default_factory=ProviderConfig)
    xiaomi_mimo: ProviderConfig = Field(default_factory=ProviderConfig)
    longcat: ProviderConfig = Field(default_factory=ProviderConfig)
    ant_ling: ProviderConfig = Field(default_factory=ProviderConfig)
    aihubmix: ProviderConfig = Field(default_factory=ProviderConfig)
    siliconflow: ProviderConfig = Field(default_factory=ProviderConfig)
    edenai: ProviderConfig = Field(default_factory=ProviderConfig)
    novita: ProviderConfig = Field(default_factory=ProviderConfig)
    volcengine: ProviderConfig = Field(default_factory=ProviderConfig)
    volcengine_coding_plan: ProviderConfig = Field(default_factory=ProviderConfig)
    byteplus: ProviderConfig = Field(default_factory=ProviderConfig)
    byteplus_coding_plan: ProviderConfig = Field(default_factory=ProviderConfig)
    openai_codex: ProviderConfig = Field(default_factory=ProviderConfig, exclude=True)
    xai_grok: ProviderConfig = Field(default_factory=ProviderConfig, exclude=True)
    github_copilot: ProviderConfig = Field(default_factory=ProviderConfig, exclude=True)
    qianfan: ProviderConfig = Field(default_factory=ProviderConfig)
    nvidia: ProviderConfig = Field(default_factory=ProviderConfig)
    opencode: ProviderConfig = Field(default_factory=ProviderConfig)
    opencode_zen: ProviderConfig = Field(default_factory=ProviderConfig)
    opencode_go: ProviderConfig = Field(default_factory=ProviderConfig)

    @model_validator(mode="after")
    def convert_extra_providers(self):
        if self.model_extra:
            from nanobot.providers.registry import find_by_name
            for key, value in self.model_extra.items():
                if spec := find_by_name(key):
                    raise ValueError(
                        f"providers.{key} conflicts with built-in provider {spec.name!r}; "
                        "use the built-in provider key or choose a different custom provider name"
                    )
                if isinstance(value, dict):
                    self.model_extra[key] = ProviderConfig.model_validate(value)
        return self

    @model_validator(mode="after")
    def _validate_api_type_scope(self) -> "ProvidersConfig":
        for name in self.__class__.model_fields:
            if name == "openai":
                continue
            provider = getattr(self, name, None)
            if isinstance(provider, ProviderConfig) and provider.api_type != "auto":
                raise ValueError("providers.<name>.api_type is only supported for providers.openai")
        for provider in (self.model_extra or {}).values():
            if isinstance(provider, ProviderConfig) and provider.api_type != "auto":
                raise ValueError("providers.<name>.api_type is only supported for providers.openai")
        return self


class HeartbeatConfig(Base):
    """Heartbeat service configuration (now backed by cron)."""
    enabled: bool = True
    interval_s: int = 30 * 60


class ApiConfig(Base):
    """OpenAI-compatible API server configuration."""
    host: str = "127.0.0.1"
    port: int = 8900
    timeout: float = 120.0
    api_key: str = Field(default="", repr=False)

    @model_validator(mode="after")
    def wildcard_host_requires_auth(self) -> "ApiConfig":
        if self.host not in ("0.0.0.0", "::"):
            return self
        if self.api_key.strip():
            return self
        raise ValueError(
            "host is 0.0.0.0 (all interfaces) but api_key is not set "
            "- set api.api_key to prevent unauthenticated access"
        )


class GatewayConfig(Base):
    """Gateway/server configuration."""
    host: str = "127.0.0.1"
    port: int = 18790
    restart_mode: Literal["auto", "exec", "spawn", "exit"] = "auto"
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)


class MCPServerConfig(Base):
    """MCP server connection configuration."""
    type: Literal["stdio", "sse", "streamableHttp"] | None = None
    auth: Literal["oauth"] | None = None
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    tool_timeout: int = 30
    enabled_tools: list[str] = Field(default_factory=lambda: ["*"])


def _lazy_default(module_path: str, class_name: str) -> Any:
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)()


class ToolsConfig(Base):
    """Tool configuration."""
    web: WebToolsConfig = Field(default_factory=lambda: _lazy_default("nanobot.agent.tools.web", "WebToolsConfig"))
    exec: ExecToolConfig = Field(default_factory=lambda: _lazy_default("nanobot.agent.tools.shell", "ExecToolConfig"))
    file: FileToolsConfig = Field(default_factory=lambda: _lazy_default("nanobot.agent.tools.filesystem", "FileToolsConfig"))
    cli_apps: CliAppsToolConfig = Field(default_factory=lambda: _lazy_default("nanobot.agent.tools.cli_apps", "CliAppsToolConfig"))
    my: MyToolConfig = Field(default_factory=lambda: _lazy_default("nanobot.agent.tools.self", "MyToolConfig"))
    image_generation: ImageGenerationToolConfig = Field(
        default_factory=lambda: _lazy_default("nanobot.agent.tools.image_generation", "ImageGenerationToolConfig"),
    )
    image_analysis: ImageAnalysisToolConfig = Field(
        default_factory=lambda: _lazy_default("nanobot.agent.tools.image_analysis", "ImageAnalysisToolConfig"),
    )
    max_session_messages_per_minute: int = Field(default=6, ge=1)
    restrict_to_workspace: bool = False
    webui_allow_local_service_access: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "webuiAllowLocalServiceAccess",
            "webui_allow_local_service_access",
            "allowLocalPreviewAccess",
            "allow_local_preview_access",
        ),
    )
    webui_allow_remote_package_install: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "webuiAllowRemotePackageInstall",
            "webui_allow_remote_package_install",
        ),
    )
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    ssrf_whitelist: list[str] = Field(default_factory=list)


class Config(BaseSettings):
    """Root configuration for nanobot."""

    _source_path: Path | None = PrivateAttr(default=None)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    transcription: TranscriptionConfig = Field(default_factory=TranscriptionConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    permissions: PermissionConfig = Field(default_factory=PermissionConfig)
    model_fleet: ModelFleetConfig = Field(
        default_factory=ModelFleetConfig,
        validation_alias=AliasChoices("modelFleet", "model_fleet"),
        serialization_alias="modelFleet",
    )
    model_presets: dict[str, ModelPresetConfig] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("modelPresets", "model_presets"),
        serialization_alias="modelPresets",
    )
    system_prompt_overrides: list[SystemPromptOverrideConfig] = Field(
        default_factory=list,
        validation_alias=AliasChoices("systemPromptOverrides", "system_prompt_overrides"),
        serialization_alias="systemPromptOverrides",
    )
    subagent_roles: dict[SubagentRoleName, SubagentRoleConfig] = Field(
        default_factory=lambda: {name: SubagentRoleConfig() for name in _BUILTIN_SUBAGENT_ROLE_NAMES},
        validation_alias=AliasChoices("subagentRoles", "subagent_roles"),
        serialization_alias="subagentRoles",
    )

    def __init__(self, **values: Any) -> None:
        if not type(self).__pydantic_complete__:
            _resolve_tool_config_refs()
        super().__init__(**values)

    def bind_source_path(self, path: Path) -> None:
        self._source_path = path.expanduser().resolve(strict=False)

    @property
    def source_path(self) -> Path | None:
        return self._source_path

    @property
    def runtime_data_dir(self) -> Path | None:
        return self._source_path.parent if self._source_path is not None else None

    @model_validator(mode="after")
    def _validate_model_preset(self) -> "Config":
        if "default" in self.model_presets:
            raise ValueError("model_preset name 'default' is reserved for agents.defaults")
        name = self.agents.defaults.model_preset
        if name and name != "default" and name not in self.model_presets:
            raise ValueError(f"model_preset {name!r} not found in model_presets")
        dream = self.agents.defaults.dream
        for field_name, dream_name in (
            ("model_override", dream.model_override),
            ("fallback_preset", dream.fallback_preset),
        ):
            if dream_name and dream_name != "default" and dream_name not in self.model_presets:
                raise ValueError(f"Dream {field_name} preset {dream_name!r} not found in model_presets")
        for fallback in self.agents.defaults.fallback_models:
            if isinstance(fallback, str) and fallback not in self.model_presets:
                raise ValueError(f"fallback_models entry {fallback!r} not found in model_presets")
        image_preset = self.tools.image_analysis.model_preset
        if image_preset:
            if image_preset == "default":
                if not self.resolve_default_preset().supports_vision:
                    raise ValueError("image_analysis model preset 'default' must be marked supports_vision")
            elif image_preset not in self.model_presets:
                raise ValueError(f"image_analysis model preset {image_preset!r} not found in model_presets")
            elif not self.model_presets[image_preset].supports_vision:
                raise ValueError(f"image_analysis model preset {image_preset!r} must be marked supports_vision")
        for role in _BUILTIN_SUBAGENT_ROLE_NAMES:
            self.subagent_roles.setdefault(role, SubagentRoleConfig())
        for role, role_config in self.subagent_roles.items():
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", role):
                raise ValueError(f"Subagent role name {role!r} must match [a-z][a-z0-9_-]{{0,63}}")
            if role not in _BUILTIN_SUBAGENT_ROLE_NAMES and (
                not (role_config.description or "").strip()
                or not (role_config.system_prompt or "").strip()
            ):
                raise ValueError(f"Custom subagent role {role!r} requires description and system_prompt")
            binding = role_config.model_preset
            if binding and binding != "default" and binding not in self.model_presets:
                raise ValueError(f"Subagent role {role!r} refers to unknown model preset {binding!r}")
        return self

    @model_validator(mode="after")
    def _validate_system_prompt_overrides(self) -> "Config":
        bound: set[str] = set()
        for override in self.system_prompt_overrides:
            override.prompt = override.prompt.strip()
            override.models = list(dict.fromkeys(m.strip() for m in override.models))
            if not override.prompt:
                raise ValueError("system prompt override prompt must not be blank")
            if not override.models or any(not model for model in override.models):
                raise ValueError(f"system prompt override {override.prompt!r} binds no model")
            duplicate = next((model for model in override.models if model in bound), None)
            if duplicate:
                raise ValueError(f"model {duplicate!r} is already bound by another system prompt override")
            bound.update(override.models)
        return self

    def resolve_default_preset(self) -> ModelPresetConfig:
        d = self.agents.defaults
        return ModelPresetConfig(
            model=d.model, provider=d.provider, max_tokens=d.max_tokens,
            context_window_tokens=d.context_window_tokens,
            supports_vision=d.supports_vision,
            temperature=d.temperature, reasoning_effort=d.reasoning_effort,
        )

    def resolve_preset(self, name: str | None = None) -> ModelPresetConfig:
        name = self.agents.defaults.model_preset if name is None else name
        if not name or name == "default":
            return self.resolve_default_preset()
        if name not in self.model_presets:
            raise KeyError(f"model_preset {name!r} not found in model_presets")
        return self.model_presets[name]

    def system_prompt_for(self, model: str | None) -> str | None:
        if not model:
            return None
        for override in self.system_prompt_overrides:
            if model in override.models:
                return override.prompt
        return None

    @property
    def workspace_path(self) -> Path:
        return Path(self.agents.defaults.workspace).expanduser()

    def _match_provider(
        self, model: str | None = None, *, preset: ModelPresetConfig | None = None,
    ) -> tuple["ProviderConfig | None", str | None]:
        from nanobot.providers.registry import PROVIDERS, find_by_name

        resolved = preset or self.resolve_preset()
        forced = resolved.provider

        def _custom_provider_by_name(name: str) -> tuple[ProviderConfig, str] | None:
            normalized = name.replace("-", "_").lower()
            for attr_name, provider in (self.providers.model_extra or {}).items():
                if not isinstance(provider, ProviderConfig):
                    continue
                if attr_name.replace("-", "_").lower() == normalized:
                    return provider, attr_name
            return None

        if forced != "auto":
            spec = find_by_name(forced)
            if spec:
                p = getattr(self.providers, spec.name, None)
                return (p, spec.name) if p else (None, None)
            custom = _custom_provider_by_name(forced)
            if custom is not None:
                return custom
            return None, None

        model_lower = (model or resolved.model).lower()
        model_normalized = model_lower.replace("-", "_")
        model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
        normalized_prefix = model_prefix.replace("-", "_")
        prefixed_provider = find_by_name(model_prefix) if model_prefix else None

        def _kw_matches(kw: str) -> bool:
            kw = kw.lower()
            return kw in model_lower or kw.replace("-", "_") in model_normalized

        for spec in PROVIDERS:
            if spec.is_transcription_only:
                continue
            p = getattr(self.providers, spec.name, None)
            if p and model_prefix and normalized_prefix == spec.name:
                if spec.is_oauth or spec.is_local or spec.is_direct or p.api_key:
                    return p, spec.name

        if model_prefix:
            custom = _custom_provider_by_name(normalized_prefix)
            if custom is not None:
                return custom

        for spec in PROVIDERS:
            if spec.is_transcription_only:
                continue
            p = getattr(self.providers, spec.name, None)
            if p and any(_kw_matches(kw) for kw in spec.keywords):
                if spec.is_local:
                    foreign_prefix = bool(prefixed_provider is not None and prefixed_provider.name != spec.name)
                    if not p.api_base or foreign_prefix:
                        continue
                if spec.is_oauth or spec.is_local or spec.is_direct or p.api_key:
                    return p, spec.name

        local_fallback: tuple[ProviderConfig, str] | None = None
        if prefixed_provider is None:
            for spec in PROVIDERS:
                if not spec.is_local:
                    continue
                p = getattr(self.providers, spec.name, None)
                if not (p and p.api_base):
                    continue
                if spec.detect_by_base_keyword and spec.detect_by_base_keyword in p.api_base:
                    return p, spec.name
                if local_fallback is None:
                    local_fallback = (p, spec.name)
        if local_fallback:
            return local_fallback

        for spec in PROVIDERS:
            if spec.is_oauth or spec.is_transcription_only:
                continue
            p = getattr(self.providers, spec.name, None)
            if p and p.api_key:
                return p, spec.name

        for attr_name, p in (self.providers.model_extra or {}).items():
            if isinstance(p, ProviderConfig) and p.api_base:
                return p, attr_name
        return None, None

    def get_provider(self, model: str | None = None, *, preset: ModelPresetConfig | None = None) -> ProviderConfig | None:
        p, _ = self._match_provider(model, preset=preset)
        return p

    def get_provider_name(self, model: str | None = None, *, preset: ModelPresetConfig | None = None) -> str | None:
        _, name = self._match_provider(model, preset=preset)
        return name

    def get_api_key(self, model: str | None = None, *, preset: ModelPresetConfig | None = None) -> str | None:
        p = self.get_provider(model, preset=preset)
        return p.api_key if p else None

    def get_api_base(self, model: str | None = None, *, preset: ModelPresetConfig | None = None) -> str | None:
        from nanobot.providers.registry import find_by_name
        p, name = self._match_provider(model, preset=preset)
        if p and p.api_base:
            return p.api_base
        if name:
            spec = find_by_name(name)
            if spec and spec.default_api_base:
                return spec.default_api_base
        return None

    model_config = SettingsConfigDict(env_prefix="NANOBOT_", env_nested_delimiter="__")


def _resolve_tool_config_refs() -> None:
    import sys

    from nanobot.agent.tools.cli_apps import CliAppsToolConfig
    from nanobot.agent.tools.filesystem import FileToolsConfig
    from nanobot.agent.tools.image_analysis import ImageAnalysisToolConfig
    from nanobot.agent.tools.image_generation import ImageGenerationToolConfig
    from nanobot.agent.tools.self import MyToolConfig
    from nanobot.agent.tools.shell import ExecToolConfig
    from nanobot.agent.tools.web import WebFetchConfig, WebSearchConfig, WebToolsConfig

    mod = sys.modules[__name__]
    mod.ExecToolConfig = ExecToolConfig  # type: ignore[attr-defined]
    mod.FileToolsConfig = FileToolsConfig  # type: ignore[attr-defined]
    mod.CliAppsToolConfig = CliAppsToolConfig  # type: ignore[attr-defined]
    mod.WebToolsConfig = WebToolsConfig  # type: ignore[attr-defined]
    mod.WebSearchConfig = WebSearchConfig  # type: ignore[attr-defined]
    mod.WebFetchConfig = WebFetchConfig  # type: ignore[attr-defined]
    mod.MyToolConfig = MyToolConfig  # type: ignore[attr-defined]
    mod.ImageAnalysisToolConfig = ImageAnalysisToolConfig  # type: ignore[attr-defined]
    mod.ImageGenerationToolConfig = ImageGenerationToolConfig  # type: ignore[attr-defined]
    ToolsConfig.model_rebuild()
    Config.model_rebuild()


try:
    _resolve_tool_config_refs()
except ImportError:
    pass