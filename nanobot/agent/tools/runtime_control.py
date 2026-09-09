"""Explicit runtime state boundary used by :class:`MyTool`."""

from __future__ import annotations

from collections.abc import Coroutine, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, runtime_checkable

from nanobot.context_management import AgentContextControl

if TYPE_CHECKING:
    import asyncio

    from nanobot.agent.memory import Consolidator
    from nanobot.agent.subagent import SubagentManager, SubagentStatus
    from nanobot.agent.tools.shell import ExecToolConfig
    from nanobot.agent.tools.web import WebToolsConfig
    from nanobot.config.schema import ModelPresetConfig
    from nanobot.session.manager import Session, SessionManager
    from nanobot.utils.llm_runtime import LLMRuntime


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

RUNTIME_SNAPSHOT_KEYS = frozenset({
    "model", "model_preset", "model_presets", "max_iterations",
    "context_window_tokens", "workspace", "provider_retry_mode",
    "max_tool_result_chars", "tool_names", "web_config", "exec_config", "subagents",
})
RUNTIME_COMMAND_KEYS = frozenset({
    "model", "model_preset", "max_iterations", "context_window_tokens",
    "provider_retry_mode", "max_tool_result_chars", "workspace",
})


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    model: str
    model_preset: str | None
    model_presets: dict[str, dict[str, object]]
    max_iterations: int
    context_window_tokens: int
    workspace: Path | str
    provider_retry_mode: str
    max_tool_result_chars: int
    tool_names: list[str]
    web_config: dict[str, object]
    exec_config: dict[str, object]
    subagent_statuses: dict[str, dict[str, object]]
    scratchpad: dict[str, JsonValue]

    def as_mapping(self) -> Mapping[str, object]:
        values: dict[str, object] = {
            "model": self.model,
            "model_preset": self.model_preset,
            "model_presets": self.model_presets,
            "max_iterations": self.max_iterations,
            "context_window_tokens": self.context_window_tokens,
            "workspace": self.workspace,
            "provider_retry_mode": self.provider_retry_mode,
            "max_tool_result_chars": self.max_tool_result_chars,
            "tool_names": self.tool_names,
            "web_config": self.web_config,
            "exec_config": self.exec_config,
            "subagents": {"_task_statuses": self.subagent_statuses},
        }
        assert values.keys() == RUNTIME_SNAPSHOT_KEYS
        return values


@runtime_checkable
class RuntimeControl(Protocol):
    def snapshot(self) -> RuntimeSnapshot: ...
    def set_model(self, model: str) -> LLMRuntime: ...
    def set_model_preset(self, name: str, *, session_key: str | None) -> LLMRuntime: ...
    def set_max_iterations(self, value: int) -> None: ...
    def set_context_window_tokens(self, value: int) -> LLMRuntime: ...
    def set_provider_retry_mode(self, value: str) -> None: ...
    def set_max_tool_result_chars(self, value: int) -> None: ...
    def set_workspace_display(self, value: str) -> None: ...
    def set_scratchpad(self, key: str, value: JsonValue, *, max_keys: int) -> None: ...
    def context_status(self, session_key: str, *, runtime: LLMRuntime | None = None) -> dict[str, object]: ...
    async def context_compact(self, session_key: str, *, runtime: LLMRuntime | None = None) -> dict[str, object]: ...


class _RuntimeControlTarget(Protocol):
    max_iterations: int
    provider_retry_mode: str
    max_tool_result_chars: int
    web_config: WebToolsConfig
    exec_config: ExecToolConfig
    subagents: SubagentManager
    sessions: SessionManager
    consolidator: Consolidator
    context_block_limit: int | None

    @property
    def model(self) -> str: ...
    @property
    def model_preset(self) -> str | None: ...
    @property
    def model_presets(self) -> Mapping[str, ModelPresetConfig]: ...
    @property
    def context_window_tokens(self) -> int: ...
    @property
    def workspace(self) -> Path: ...
    @property
    def tool_names(self) -> list[str]: ...
    def set_runtime_model(self, model: str) -> LLMRuntime: ...
    def set_runtime_context_window(self, context_window_tokens: int) -> LLMRuntime: ...
    def set_model_preset(self, name: str | None) -> LLMRuntime: ...
    def set_session_model_preset(self, session_key: str, name: str) -> LLMRuntime: ...
    def runtime_for_session(self, session: Session, *, recover_removed: bool = True) -> LLMRuntime: ...
    def _get_session_lock(self, session_key: str) -> asyncio.Lock: ...
    def schedule_background(self, coro: Coroutine[Any, Any, Any]) -> None: ...


class AgentRuntimeControl:
    """Allowlisted adapter from agent-loop state to self-management tools."""

    def __init__(self, target: _RuntimeControlTarget) -> None:
        self.__target = target
        self.__context = AgentContextControl(target)
        self.__pending_context_compactions: set[str] = set()
        self.__scratchpad: dict[str, JsonValue] = {}
        self.__workspace_display: str | None = None

    def snapshot(self) -> RuntimeSnapshot:
        target = self.__target
        return RuntimeSnapshot(
            model=target.model,
            model_preset=target.model_preset,
            model_presets=_snapshot_model_presets(target.model_presets),
            max_iterations=target.max_iterations,
            context_window_tokens=target.context_window_tokens,
            workspace=self.__workspace_display if self.__workspace_display is not None else target.workspace,
            provider_retry_mode=target.provider_retry_mode,
            max_tool_result_chars=target.max_tool_result_chars,
            tool_names=list(target.tool_names),
            web_config=_snapshot_web_config(target.web_config),
            exec_config=_snapshot_exec_config(target.exec_config),
            subagent_statuses=_snapshot_subagent_statuses(target.subagents),
            scratchpad=_snapshot_json_mapping(self.__scratchpad),
        )

    def set_model(self, model: str) -> LLMRuntime:
        return self.__target.set_runtime_model(model)

    def set_model_preset(self, name: str, *, session_key: str | None) -> LLMRuntime:
        if session_key is not None:
            return self.__target.set_session_model_preset(session_key, name)
        return self.__target.set_model_preset(name)

    def set_max_iterations(self, value: int) -> None:
        self.__target.max_iterations = value
        self.__target.subagents.max_iterations = value

    def set_context_window_tokens(self, value: int) -> LLMRuntime:
        return self.__target.set_runtime_context_window(value)

    def set_provider_retry_mode(self, value: str) -> None:
        self.__target.provider_retry_mode = value

    def set_max_tool_result_chars(self, value: int) -> None:
        self.__target.max_tool_result_chars = value

    def set_workspace_display(self, value: str) -> None:
        self.__workspace_display = value

    def set_scratchpad(self, key: str, value: JsonValue, *, max_keys: int) -> None:
        if key not in self.__scratchpad and len(self.__scratchpad) >= max_keys:
            raise ValueError(f"scratchpad is full (max {max_keys} keys)")
        self.__scratchpad[key] = value

    def context_status(self, session_key: str, *, runtime: LLMRuntime | None = None) -> dict[str, object]:
        data = self.__context.status(session_key, runtime=runtime).as_dict()
        data["pending_compaction"] = session_key in self.__pending_context_compactions
        return data

    async def context_compact(self, session_key: str, *, runtime: LLMRuntime | None = None) -> dict[str, object]:
        snapshot = self.__context.status(session_key, runtime=runtime)
        if not snapshot.can_compact:
            return {
                "status": "noop",
                "reason": "not_enough_replayable_history",
                "context": snapshot.as_dict(),
            }
        if session_key in self.__pending_context_compactions:
            return {
                "status": "scheduled",
                "reason": "already_pending",
                "context": self.context_status(session_key, runtime=runtime),
            }

        self.__pending_context_compactions.add(session_key)

        async def _compact_after_current_turn() -> None:
            try:
                lock = self.__target._get_session_lock(session_key)
                async with lock:
                    await self.__context.compact(session_key, runtime=runtime)
            finally:
                self.__pending_context_compactions.discard(session_key)

        self.__target.schedule_background(_compact_after_current_turn())
        return {
            "status": "scheduled",
            "reason": "after_current_turn",
            "applies_to": "next_turn",
            "context": self.context_status(session_key, runtime=runtime),
        }


def _snapshot_model_presets(presets: Mapping[str, ModelPresetConfig]) -> dict[str, dict[str, object]]:
    return {
        name: {
            "model": preset.model,
            "provider": preset.provider,
            "max_tokens": preset.max_tokens,
            "context_window_tokens": preset.context_window_tokens,
            "temperature": preset.temperature,
            "reasoning_effort": preset.reasoning_effort,
        }
        for name, preset in presets.items()
    }


def _snapshot_web_config(config: WebToolsConfig) -> dict[str, object]:
    return {
        "enable": config.enable,
        "proxy": "<configured>" if config.proxy else config.proxy,
        "user_agent": config.user_agent,
        "search": {
            "provider": config.search.provider,
            "base_url": config.search.base_url,
            "max_results": config.search.max_results,
            "timeout": config.search.timeout,
        },
        "fetch": {"use_jina_reader": config.fetch.use_jina_reader},
    }


def _snapshot_exec_config(config: ExecToolConfig) -> dict[str, object]:
    return {
        "enable": config.enable,
        "timeout": config.timeout,
        "path_prepend": config.path_prepend,
        "path_append": config.path_append,
        "sandbox": config.sandbox,
        "sandbox_ro_binds": list(config.sandbox_ro_binds),
        "sandbox_rw_binds": list(config.sandbox_rw_binds),
        "allowed_env_keys": list(config.allowed_env_keys),
        "allow_patterns": list(config.allow_patterns),
        "deny_patterns": list(config.deny_patterns),
    }


def _snapshot_subagent_statuses(manager: SubagentManager) -> dict[str, dict[str, object]]:
    return {task_id: _snapshot_subagent_status(status) for task_id, status in manager.runtime_statuses().items()}


def _snapshot_subagent_status(status: SubagentStatus) -> dict[str, object]:
    return {
        "task_id": status.task_id,
        "label": status.label,
        "task_description": status.task_description,
        "role": status.role,
        "model": status.model,
        "started_at": status.started_at,
        "phase": status.phase,
        "state": status.state,
        "thinking": status.thinking,
        "context": status.context,
        "timeout_seconds": status.timeout_seconds,
        "iteration": status.iteration,
        "tool_events": [dict(event) for event in status.tool_events],
        "usage": status.usage.to_dict() if status.usage is not None else None,
        "stop_reason": status.stop_reason,
        "error": status.error,
    }


def _snapshot_json_mapping(values: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return {key: _snapshot_json_value(value) for key, value in values.items()}


def _snapshot_json_value(value: JsonValue) -> JsonValue:
    if isinstance(value, list):
        return [_snapshot_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _snapshot_json_value(item) for key, item in value.items()}
    return value
