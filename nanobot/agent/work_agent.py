"""Task-scoped WorkAgent snapshots and launch adapter.

WorkAgent has no independent runtime or persistence. It supplies one ephemeral
``ResolvedSubagentRole`` to the existing ``SubagentManager`` launch path.
"""

from __future__ import annotations

from typing import Any

from nanobot.agent.subagent_roles import ALL_SUBAGENT_TOOL_NAMES, ResolvedSubagentRole


def _nonempty(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty string")
    return normalized


def build_work_role_definition(
    general: dict[str, Any],
    *,
    description: str | None = None,
    system_prompt: str | None = None,
    tools: list[str] | None = None,
) -> ResolvedSubagentRole:
    """Build one non-persistent WorkAgent snapshot.

    General contributes only its normal-worker tool set. WorkAgent does not
    inherit General's prompt, model, thinking, temperature, timeout, or context
    tuning; unspecified runtime settings continue from the current Main runtime.
    """
    description = _nonempty(description, "description")
    system_prompt = _nonempty(system_prompt, "system_prompt")
    requested_tools = tuple(tools if tools is not None else general.get("tools", ()))
    unknown = set(requested_tools) - ALL_SUBAGENT_TOOL_NAMES
    if "subagent" in requested_tools:
        unknown.add("subagent")
    if unknown:
        raise ValueError(
            f"Unknown or forbidden WorkAgent tools: {', '.join(sorted(unknown))}"
        )

    effective_description = description or (
        "Task-scoped worker for one assigned responsibility; destroyed when the task ends."
    )
    effective_prompt = system_prompt or description or (
        "Execute only the assigned task with the provided capabilities. "
        "Return concise results and concrete evidence to the Main Agent."
    )
    return ResolvedSubagentRole(
        name="work",
        description=effective_description,
        system_prompt=effective_prompt,
        tools=requested_tools,
        model=None,
        model_preset=None,
        thinking=None,
        temperature=None,
        timeout_seconds=None,
        context="fresh",
        disabled=False,
        builtin=False,
        permissions="read-write-exec",
        category="work",
        source="ephemeral",
        status="active",
        version=1,
        created_by=None,
        usage={},
    )


def has_work_override(
    *,
    description: str | None,
    system_prompt: str | None,
    tools: list[str] | None,
    model: str | None,
    model_preset: str | None,
    thinking: str | None,
    temperature: float | None,
    timeout_seconds: float | None,
    context: str | None,
) -> bool:
    """Return whether an omitted-role run is a task-scoped WorkAgent launch."""
    return any(value is not None for value in (
        description,
        system_prompt,
        tools,
        model,
        model_preset,
        thinking,
        temperature,
        timeout_seconds,
        context,
    ))


async def run_work_agent(
    manager: Any,
    *,
    role_definition: ResolvedSubagentRole,
    wait: bool,
    **launch: Any,
) -> str:
    """Run one WorkAgent through the manager's native ephemeral lifecycle."""
    method = manager.run_inline_ephemeral if wait else manager.spawn_ephemeral
    return await method(role_definition=role_definition, **launch)
