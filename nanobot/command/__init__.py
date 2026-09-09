"""Slash command routing and built-in handlers."""

from nanobot.command.builtin import register_builtin_commands as _register_builtin_commands
from nanobot.command.context_commands import register_context_commands
from nanobot.command.router import CommandContext, CommandRouter


def register_builtin_commands(router: CommandRouter) -> None:
    """Register canonical built-ins plus self context commands."""
    _register_builtin_commands(router)
    register_context_commands(router)


__all__ = ["CommandContext", "CommandRouter", "register_builtin_commands"]
