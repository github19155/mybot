from __future__ import annotations

from typing import Any

from nanobot.agent.permissions import MAIN_SUBJECT
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.context import RequestContext, ToolContext, request_context
from nanobot.agent.tools.loader import ToolLoader
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import ToolsConfig


class _NamedTool(Tool):
    NAME = "tool"

    @property
    def name(self) -> str:
        return self.NAME

    @property
    def description(self) -> str:
        return self.NAME

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


class _SubagentTool(_NamedTool):
    NAME = "subagent"


class _ExecTool(_NamedTool):
    NAME = "exec"


class _WebTool(_NamedTool):
    NAME = "web_search"


class _ReadTool(_NamedTool):
    NAME = "read_file"


class _ImageAnalyzeTool(_NamedTool):
    NAME = "image_analyze"


class _ImageGenerateTool(_NamedTool):
    NAME = "generate_image"


def _definition_names(registry: ToolRegistry) -> list[str]:
    names: list[str] = []
    for schema in registry.get_definitions():
        function = schema.get("function")
        if isinstance(function, dict):
            names.append(str(function.get("name")))
        else:
            names.append(str(schema.get("name")))
    return names


def test_main_registry_keeps_system_catalog_but_model_sees_control_plane_only(tmp_path) -> None:
    registry = ToolRegistry(permission_subject=MAIN_SUBJECT)
    loader = ToolLoader(test_classes=[
        _SubagentTool,
        _ExecTool,
        _WebTool,
        _ReadTool,
        _ImageAnalyzeTool,
        _ImageGenerateTool,
    ])
    ctx = ToolContext(config=ToolsConfig(), workspace=str(tmp_path))

    registered = loader.load(ctx, registry, scope="core")

    assert set(registered) == {
        "subagent", "exec", "web_search", "read_file", "image_analyze", "generate_image"
    }
    assert set(registry.tool_names) == set(registered)
    assert _definition_names(registry) == ["subagent"]


def test_main_model_cannot_call_worker_execution_tools() -> None:
    registry = ToolRegistry(permission_subject=MAIN_SUBJECT)
    for tool in (_ExecTool(), _WebTool(), _ReadTool(), _ImageAnalyzeTool(), _ImageGenerateTool()):
        registry.register(tool)

    for name in ("exec", "web_search", "read_file", "image_analyze", "generate_image"):
        tool, _, error = registry.prepare_call(name, {})
        assert tool is None
        assert error is not None
        assert "not available to the Main orchestrator" in error
        assert "Delegate execution to a subagent" in error


def test_worker_scope_keeps_heavy_tools(tmp_path) -> None:
    class _WorkerExecTool(_ExecTool):
        _scopes = {"subagent"}

    registry = ToolRegistry(permission_subject="work")
    loader = ToolLoader(test_classes=[_WorkerExecTool])
    ctx = ToolContext(config=ToolsConfig(), workspace=str(tmp_path))

    registered = loader.load(ctx, registry, scope="subagent")

    assert registered == ["exec"]
    assert _definition_names(registry) == ["exec"]


def test_main_control_plane_has_no_special_per_turn_tool_budget() -> None:
    registry = ToolRegistry(permission_subject=MAIN_SUBJECT)
    registry.register(_SubagentTool())
    request = RequestContext(channel="test", chat_id="chat", attributes={})

    with request_context(request):
        for _ in range(5):
            tool, _, error = registry.prepare_call("subagent", {})
            assert tool is not None
            assert error is None
            assert registry.get_definitions()


def test_subagent_registry_uses_normal_runner_limits() -> None:
    registry = ToolRegistry(permission_subject="work")
    registry.register(_ExecTool())
    request = RequestContext(channel="test", chat_id="chat", attributes={})

    with request_context(request):
        for _ in range(5):
            tool, _, error = registry.prepare_call("exec", {})
            assert tool is not None
            assert error is None
        assert registry.get_definitions()
