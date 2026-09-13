"""Main-agent-only model/provider, fleet, and subagent role configuration."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters

if TYPE_CHECKING:
    from nanobot.agent.model_management import ModelManagement
    from nanobot.agent.tools.context import ToolContext


@tool_parameters({
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": [
            "list", "roles_update", "model_create", "model_update", "model_delete",
            "provider_create", "provider_update", "fleet_status", "fleet_recommend",
            "fleet_feedback", "fleet_profile_update", "fleet_provider_update",
        ]},
        "bindings": {"type": "object", "additionalProperties": {"type": ["string", "null"]}},
        "name": {"type": "string"},
        "new_name": {"type": "string"},
        "model": {"type": "string"},
        "model_preset": {"type": "string"},
        "provider": {"type": "string"},
        "max_tokens": {"type": "integer", "minimum": 1},
        "context_window_tokens": {"type": "integer", "minimum": 1},
        "temperature": {"type": "number"},
        "supports_vision": {"type": "boolean"},
        "supports_image_generation": {"type": "boolean"},
        "reasoning_effort": {"type": "string"},
        "api_key": {"type": "string"},
        "api_base": {"type": "string"},
        "api_type": {"type": "string"},
        "proxy": {"type": "string"},
        "extra_headers": {"type": "object", "additionalProperties": {"type": "string"}},
        "offering_id": {"type": "string"},
        "fleet_pools": {"type": "array", "items": {"type": "string"}},
        "input_cost_per_million": {"type": "number", "minimum": 0},
        "output_cost_per_million": {"type": "number", "minimum": 0},
        "cached_input_cost_per_million": {"type": "number", "minimum": 0},
        "max_concurrent_requests": {"type": "integer", "minimum": 1},
        "rate_limit_scope": {"type": "string", "enum": ["provider", "model"]},
        "pool": {"type": "string"},
        "task_type": {"type": "string"},
        "requires_vision": {"type": "boolean"},
        "min_context_tokens": {"type": "integer", "minimum": 1},
        "dimension": {"type": "string"},
        "outcome": {"type": "number", "minimum": -1, "maximum": 1},
        "weight": {"type": "number", "minimum": 0.01, "maximum": 10},
        "evidence": {"type": "string"},
    },
    "required": ["action"],
    "additionalProperties": False,
})
class ModelConfigTool(Tool):
    _scopes = {"core"}

    def __init__(self, management: ModelManagement) -> None:
        self._management = management

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.model_management is not None

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.model_management is None:
            raise RuntimeError("model_config requires model management")
        return cls(ctx.model_management)

    @property
    def name(self) -> str:
        return "model_config"

    @property
    def description(self) -> str:
        return (
            "Manage model presets/providers and the runtime Model Fleet without changing your own selected model. "
            "Model preset capabilities include supports_vision and supports_image_generation; use explicit capability flags instead of inferring them from model names. "
            "Use fleet_status to inspect each concrete provider+model offering's observed quality/speed/reliability/cost/confidence, capacity, queues and cooldown. "
            "Use fleet_recommend before dispatch when the user did not explicitly choose a model; pass pool/task_type/capability constraints and prefer its recommended preset. "
            "A user's explicit model or preset always wins over Fleet recommendations. Do not infer performance from model names. "
            "fleet_feedback records quality evidence only when there is an objective result such as tests, validators, accepted/rejected task output or required rework; never self-grade. "
            "fleet_profile_update stores stable facts for a named preset: offering_id, pools, price and optional request cap. "
            "fleet_provider_update stores provider-account concurrency and whether unknown rate limits are provider- or model-scoped. "
            "list shows configured names and fleet profiles. roles_update uses bindings {role: preset_name_or_null}; null inherits the parent runtime. "
            "Use subagent for child role/model/thinking selection; use my to select a model for your own direct work."
        )

    async def execute(self, **kwargs: Any) -> str | ToolResult:
        result = await self._management.execute(**kwargs)
        text = json.dumps(result, ensure_ascii=False)
        return ToolResult.error(text) if result.get("status") == "error" else text
