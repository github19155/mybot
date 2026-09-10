"""Slash command routing and built-in handlers."""

from nanobot.command import builtin as _builtin
from nanobot.command.builtin import BuiltinCommandSpec
from nanobot.command.context_commands import register_context_commands
from nanobot.command.dream_commands import register_dream_commands
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
_DREAM_COMMAND_SPECS = (
    BuiltinCommandSpec(
        "/dream",
        "Request Dream",
        "Queue a manual Dream background cognition request.",
        "sparkles",
    ),
    BuiltinCommandSpec(
        "/dream-log",
        "Show Dream audit",
        "Show recent validated Dream findings and proposals.",
        "book-open",
        "[1-10]",
        accepts_args=True,
    ),
)
_RETIRED_DREAM_COMMANDS = {"/dream", "/dream-log", "/dream-restore"}

_base_specs = tuple(
    spec for spec in _builtin.BUILTIN_COMMAND_SPECS
    if spec.command not in _RETIRED_DREAM_COMMANDS
)
_existing_commands = {spec.command for spec in _base_specs}
_builtin.BUILTIN_COMMAND_SPECS = (
    *_base_specs,
    *(spec for spec in _DREAM_COMMAND_SPECS if spec.command not in _existing_commands),
    *(spec for spec in _CONTEXT_COMMAND_SPECS if spec.command not in _existing_commands),
)


def register_builtin_commands(router: CommandRouter) -> None:
    """Register canonical built-ins plus Dream and self-context commands."""
    _builtin.register_builtin_commands(router)
    for command in _RETIRED_DREAM_COMMANDS:
        router.remove(command)
    register_dream_commands(router)
    register_context_commands(router)


__all__ = ["CommandContext", "CommandRouter", "register_builtin_commands"]
