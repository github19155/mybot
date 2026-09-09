"""Slash command routing and built-in handlers."""

from nanobot.command import builtin as _builtin
from nanobot.command.builtin import BuiltinCommandSpec
from nanobot.command.context_commands import register_context_commands
from nanobot.command.router import CommandContext, CommandRouter

_CONTEXT_COMMAND_SPECS = (
    BuiltinCommandSpec(
        "/context",
        "Show context",
        "Show current conversation context pressure and compaction status.",
        "activity",
    ),
    BuiltinCommandSpec(
        "/compact",
        "Compact context",
        "Summarize older context now while retaining full persisted history.",
        "archive",
    ),
)

_existing_commands = {spec.command for spec in _builtin.BUILTIN_COMMAND_SPECS}
_builtin.BUILTIN_COMMAND_SPECS = (
    *_builtin.BUILTIN_COMMAND_SPECS,
    *(spec for spec in _CONTEXT_COMMAND_SPECS if spec.command not in _existing_commands),
)


def register_builtin_commands(router: CommandRouter) -> None:
    """Register canonical built-ins plus self context commands."""
    _builtin.register_builtin_commands(router)
    register_context_commands(router)


__all__ = ["CommandContext", "CommandRouter", "register_builtin_commands"]
