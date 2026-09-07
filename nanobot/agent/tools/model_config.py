"""Main-agent-only model/provider and subagent role configuration."""

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
        "action": {"type": "string", "enum": ["list", "roles_update", "model_create", "model_update", "model_delete", "provider_create", "provider_update"]},
        "bindings": {"type": "object", "additionalProperties": {"type": ["string", "null"]}},
        "name": {"type": "string"},
        "new_name": {"type": "string"},
        "model": {"type": "string"},
        "provider": {"type": "string"},
        "max_tokens": {"type": "integer", "minimum": 1},
        "context_window_tokens": {"type": "integer", "minimum": 1},
        "temperature": {"type": "number"},
        "reasoning_effort": {"type": "string"},
        "api_key": {"type": "string"},
        "api_base": {"type": "string"},
        "api_type": {"type": "string"},
        "proxy": {"type": "string"},
        "extra_headers": {"type": "object", "additionalProperties": {"type": "string"}},
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
            "Manage instance model presets, providers and subagent role bindings without changing your selected model. "
            "list shows available names. roles_update uses bindings {role: preset_name_or_null}; null inherits the parent runtime. "
            "model_create requires name, model, provider; model_update uses name and optional new_name/settings; "
            "model_delete requires name (bound presets must be unbound first). provider_create uses name and api_base, "
            "provider_update uses provider and connection settings. Credentials may use ${ENV_VAR} references. "
            "Use the subagent tool for child role/model/thinking selection; use my to select a model for your own direct work."
        )

    async def execute(self, **kwargs: Any) -> str | ToolResult:
        result = await self._management.execute(**kwargs)
        text = json.dumps(result, ensure_ascii=False)
        return ToolResult.error(text) if result.get("status") == "error" else text
