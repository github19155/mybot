"""Task-scoped WorkAgent role snapshots.

WorkAgent has no independent runtime or persistence. It only builds an ephemeral
``ResolvedSubagentRole`` consumed by the normal ``SubagentManager`` launch path.
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

    The permanent General role supplies only the default normal-worker tool set.
    WorkAgent does not inherit General's prompt or runtime tuning; unspecified
    model/generation settings continue from the current Main runtime.
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
