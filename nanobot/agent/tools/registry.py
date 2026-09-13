"""Tool registry for dynamic tool management."""

from __future__ import annotations

import json
from collections.abc import Iterable
from copy import copy
from typing import TYPE_CHECKING, Any, cast

from nanobot.agent.permissions import MAIN_SUBJECT
from nanobot.agent.tools.base import Tool, ToolResult
from nanobot.agent.tools.context import ContextAware, current_request_context

if TYPE_CHECKING:
    from nanobot.agent.permissions import PermissionManager
    from nanobot.runtime_context import RuntimeContextProvider

_MAIN_TOOL_CALL_BUDGET = 2
_MAIN_TOOL_CALL_COUNT_ATTR = "_main_orchestrator_tool_calls"
_MAIN_ORCHESTRATOR_TOOLS = frozenset({
    "subagent",
    "message",
    "context",
    "my",
    "model_config",
    "cron",
    "create_goal",
    "update_goal",
    "list_sessions",
    "search_sessions",
    "read_session",
    "send_session_message",
    "image_analyze",
    "generate_image",
})


def is_tool_error_result(result: Any) -> bool:
    return isinstance(result, ToolResult) and result.is_error


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(
        self,
        *,
        permission_manager: "PermissionManager | None" = None,
        permission_subject: str | None = None,
    ):
        self._tools: dict[str, Tool] = {}
        self._cached_definitions: list[dict[str, Any]] | None = None
        self.permission_manager = permission_manager
        self.permission_subject = permission_subject

    def bind_permissions(self, manager: "PermissionManager", subject: str) -> None:
        self.permission_manager = manager
        self.permission_subject = subject
        self._cached_definitions = None

    def without(self, names: Iterable[str]) -> "ToolRegistry":
        """Return a filtered registry while preserving its bound runtime context."""
        excluded = set(names)
        if not excluded.intersection(self._tools):
            return self
        registry = copy(self)
        registry._tools = {
            name: tool for name, tool in self._tools.items() if name not in excluded
        }
        registry._cached_definitions = None
        return registry

    def _tool_allowed(self, name: str) -> bool:
        manager = self.permission_manager
        subject = self.permission_subject
        return manager is None or subject is None or manager.tool_allowed(subject, name)

    def _main_orchestrator_tool_allowed(self, name: str) -> bool:
        """Keep Main model-facing tools to orchestration/control capabilities only."""
        if self.permission_subject != MAIN_SUBJECT:
            return True
        tool = self.get(name)
        if tool is None:
            return False
        if name in _MAIN_ORCHESTRATOR_TOOLS:
            return True
        return "orchestrator" in getattr(tool, "_scopes", set())

    def _model_tool_allowed(self, name: str) -> bool:
        return self._tool_allowed(name) and self._main_orchestrator_tool_allowed(name)

    def _permission_error(self, name: str) -> str | None:
        if self._tool_allowed(name):
            return None
        assert self.permission_manager is not None
        assert self.permission_subject is not None
        capability = self.permission_manager.tool_capability(name)
        return str(ToolResult.error(
            f"Error: permission denied for tool {name!r} "
            f"(subject={self.permission_subject!r}, capability={capability!r})"
        ))

    def _main_tool_call_count(self) -> int:
        if self.permission_subject != MAIN_SUBJECT:
            return 0
        request = current_request_context()
        if request is None:
            return 0
        value = request.attributes.get(_MAIN_TOOL_CALL_COUNT_ATTR, 0)
        return value if isinstance(value, int) and value >= 0 else 0

    def _main_tool_budget_exhausted(self) -> bool:
        return (
            self.permission_subject == MAIN_SUBJECT
            and self._main_tool_call_count() >= _MAIN_TOOL_CALL_BUDGET
        )

    def _consume_main_tool_call(self) -> None:
        if self.permission_subject != MAIN_SUBJECT:
            return
        request = current_request_context()
        if request is None:
            return
        request.attributes[_MAIN_TOOL_CALL_COUNT_ATTR] = self._main_tool_call_count() + 1

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
        self._cached_definitions = None

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)
        self._cached_definitions = None

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_runtime_context_providers(self) -> list[RuntimeContextProvider]:
        """Return tool-owned providers in stable tool-name order."""
        providers: list[RuntimeContextProvider] = []
        for name in sorted(self._tools):
            provider = self._tools[name].runtime_context_provider()
            if provider is not None:
                providers.append(provider)
        return providers

    @staticmethod
    def _lookup_key(name: str) -> str:
        """Normalize names for suggestions only; never for execution."""
        return "".join(ch.lower() for ch in name if ch.isalnum())

    def _suggest_name(self, name: str) -> str | None:
        key = self._lookup_key(str(name or ""))
        if not key:
            return None
        matches = [
            registered
            for registered in self._tools
            if self._model_tool_allowed(registered)
            and self._lookup_key(registered) == key
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return self.get(name) is not None

    @staticmethod
    def _schema_name(schema: dict[str, Any]) -> str:
        """Extract a normalized tool name from either OpenAI or flat schemas."""
        fn = schema.get("function")
        if isinstance(fn, dict):
            name = cast(dict[str, Any], fn).get("name")
            if isinstance(name, str):
                return name
        name = schema.get("name")
        return name if isinstance(name, str) else ""

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get model-facing tool definitions with stable ordering.

        The internal registry may contain worker tools used by trusted product
        surfaces (for example explicit user shell execution), but the Main model
        only sees orchestration/control tools. Subagent registries keep their
        existing worker tool exposure. Main also stops advertising tools after
        its small per-turn orchestration budget is consumed.
        """
        if self._main_tool_budget_exhausted():
            return []
        if self._cached_definitions is None:
            definitions = [
                tool.to_schema()
                for name, tool in self._tools.items()
                if self._model_tool_allowed(name)
            ]
            builtins: list[dict[str, Any]] = []
            mcp_tools: list[dict[str, Any]] = []
            for schema in definitions:
                name = self._schema_name(schema)
                if name.startswith("mcp_"):
                    mcp_tools.append(schema)
                else:
                    builtins.append(schema)

            builtins.sort(key=self._schema_name)
            mcp_tools.sort(key=self._schema_name)
            self._cached_definitions = builtins + mcp_tools

        return self._cached_definitions

    def prepare_call(
        self,
        name: str,
        params: Any,
    ) -> tuple[Tool | None, Any, str | None]:
        """Resolve, cast, and validate one model tool call."""
        tool = self.get(name)
        if tool is not None and not self._main_orchestrator_tool_allowed(name):
            return None, params, str(ToolResult.error(
                f"Error: Tool {name!r} is not available to the Main orchestrator. "
                "Delegate execution to a subagent."
            ))

        permission_error = self._permission_error(name)
        if permission_error:
            return None, params, permission_error
        if not tool:
            suggestion = self._suggest_name(str(name))
            hint = f" Did you mean '{suggestion}'? Tool names must match exactly." if suggestion else ""
            available = [registered for registered in self._tools if self._model_tool_allowed(registered)]
            return None, params, (
                ToolResult.error(
                    f"Error: Tool '{name}' not found.{hint} Available: {', '.join(available)}"
                )
            )
        # Compatibility for external tools that still implement the legacy
        # setter protocol. Built-ins read the authoritative ContextVar
        # directly and never copy routing state.
        if isinstance(tool, ContextAware) and (ctx := current_request_context()) is not None:
            tool.set_context(ctx)

        params = self._coerce_params(tool, params)
        if not isinstance(params, dict):
            return tool, params, (
                ToolResult.error(
                    f"Error: Tool '{name}' parameters must be a JSON object, got "
                    f"{type(params).__name__}. Use named parameters like "
                    'tool_name(param1="value1", param2="value2") matching the tool schema.'
                )
            )

        cast_params = tool.cast_params(cast(dict[str, Any], params))
        errors = tool.validate_params(cast_params)
        if errors:
            return tool, cast_params, (
                ToolResult.error(f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors))
            )
        if self._main_tool_budget_exhausted():
            return None, cast_params, str(ToolResult.error(
                "Error: Main orchestration tool budget exhausted for this turn. "
                "Respond to the user now; delegate further execution to a subagent on a later turn."
            ))
        self._consume_main_tool_call()
        return tool, cast_params, None

    @classmethod
    def _coerce_argument_value(cls, value: Any) -> Any:
        if value is None:
            return {}
        if not isinstance(value, str):
            return value

        stripped = value.strip()
        if not stripped:
            return {}

        if not stripped.startswith(("{", "[")):
            return value

        try:
            parsed = json.loads(stripped)
        except Exception:
            return value

        return parsed

    @classmethod
    def _coerce_params(cls, tool: Tool, params: Any) -> Any:
        params = cls._coerce_argument_value(params)
        return cls._unwrap_arguments_payload(tool, params)

    @classmethod
    def _unwrap_arguments_payload(cls, tool: Tool, params: Any) -> Any:
        if not isinstance(params, dict):
            return params
        arguments_payload = cast(dict[str, Any], params)
        if set(arguments_payload) != {"arguments"}:
            return arguments_payload
        properties = (tool.parameters or {}).get("properties", {})
        if isinstance(properties, dict) and "arguments" in properties:
            return arguments_payload
        return cls._coerce_argument_value(arguments_payload.get("arguments"))

    async def execute(self, name: str, params: Any) -> Any:
        """Execute a model tool call by name with given parameters."""
        hint = "\n\n[Analyze the error above and try a different approach.]"
        tool, params, error = self.prepare_call(name, params)
        if error:
            return ToolResult.error(str(error) + hint)

        try:
            assert tool is not None  # guarded by prepare_call()
            result = await tool.execute(**params)
            if is_tool_error_result(result):
                return ToolResult.error(str(result) + hint)
            return result
        except Exception as e:
            return ToolResult.error(f"Error executing {name}: {str(e)}" + hint)

    @property
    def tool_names(self) -> list[str]:
        """Get list of all internally registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return self.has(name)
