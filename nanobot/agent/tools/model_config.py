"""Main-agent-only canonical model/provider and Fleet control plane."""

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
            "list", "model_get", "model_create", "model_update", "model_delete",
            "provider_create", "provider_update", "fleet_status", "fleet_recommend",
            "fleet_feedback", "fleet_model_update", "fleet_provider_update", "roles_update",
        ]},
        "model_id": {"type": "string"},
        "display_name": {"type": "string"},
        "provider": {"type": "string"},
        "model": {"type": "string"},
        "capabilities": {
            "type": "object",
            "properties": {
                "text": {"type": "boolean"},
                "vision": {"type": "boolean"},
                "image_generation": {"type": "boolean"},
                "transcription": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "context_window_tokens": {"type": "integer", "minimum": 1},
        "pricing": {
            "type": "object",
            "properties": {
                "input": {"type": ["number", "null"], "minimum": 0},
                "output": {"type": ["number", "null"], "minimum": 0},
                "cache_read": {"type": ["number", "null"], "minimum": 0},
            },
            "additionalProperties": False,
        },
        "generation_defaults": {
            "type": "object",
            "properties": {
                "temperature": {"type": "number", "minimum": 0, "maximum": 2},
                "max_tokens": {"type": "integer", "minimum": 1},
                "reasoning_effort": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "offering_id": {"type": ["string", "null"]},
        "pools": {"type": "array", "items": {"type": "string"}},
        "max_concurrent_requests": {"type": ["integer", "null"], "minimum": 1},
        "bindings": {"type": "object", "additionalProperties": {"type": ["string", "null"]}},
        "api_key": {"type": ["string", "null"]},
        "api_base": {"type": ["string", "null"]},
        "api_type": {"type": "string", "enum": ["auto", "chat_completions", "responses"]},
        "proxy": {"type": ["string", "null"]},
        "extra_headers": {"type": ["object", "null"], "additionalProperties": {"type": "string"}},
        "extra_body": {"type": ["object", "null"]},
        "extra_query": {"type": ["object", "null"], "additionalProperties": {"type": "string"}},
        "thinking_style": {"type": ["string", "null"]},
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
            "Manage canonical Models, provider connection settings, model references, and Model Fleet control data. "
            "Models are addressed by immutable model_id keys. model_create/model_update write strict ModelConfig fields: "
            "display_name, provider, model, capabilities, context_window_tokens, pricing, generation_defaults, "
            "offering_id, pools, and max_concurrent_requests. Provider is always the concrete configured provider ID; "
            "never infer or replace it from the upstream model string. Invalid or legacy fields are rejected so retry with "
            "the canonical nested structure. list returns only real configured models with derived is_default/usages; "
            "model_get reads one model. model_delete never rewrites references and fails with usages when a model is in use. "
            "Provider credentials/API base/OAuth/proxy/request extras/concurrency remain provider settings, not ModelConfig. "
            "roles_update uses bindings {role: model_id_or_null}. Fleet actions use model_id/offering_id and objective evidence; "
            "never self-grade. Use subagent for child role/model/thinking selection and my for your own direct model selection."
        )

    async def execute(self, **kwargs: Any) -> str | ToolResult:
        result = await self._management.execute(**kwargs)
        text = json.dumps(result, ensure_ascii=False)
        return ToolResult.error(text) if result.get("status") == "error" else text
