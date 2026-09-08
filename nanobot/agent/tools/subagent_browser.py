"""Subagent-scoped views of the persistent browser tool family.

Browser remains a capability, not a separate Agent type. These thin wrappers
opt the existing browser tools into ToolLoader's ``subagent`` scope and reuse
the parent agent's message bus for human handoff notifications.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from nanobot.agent.tools import browser as browser_tools
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.browser import (
    BrowserBackTool,
    BrowserClickTool,
    BrowserCloseTool,
    BrowserHandoffTool,
    BrowserOpenTool,
    BrowserScreenshotTool,
    BrowserScrollTool,
    BrowserSnapshotTool,
    BrowserStatusTool,
    BrowserTabsTool,
    BrowserTypeTool,
    BrowserWaitTool,
)

if TYPE_CHECKING:
    from nanobot.agent.tools.context import ToolContext
    from nanobot.bus.queue import MessageBus


_BUSES: dict[str, MessageBus] = {}


def _workspace_key(workspace: str | Path) -> str:
    return str(Path(workspace).expanduser().resolve())


def bind_subagent_browser_bus(workspace: str | Path, bus: MessageBus) -> None:
    """Bind one agent workspace to the bus used for browser handoff notices."""
    _BUSES[_workspace_key(workspace)] = bus


def _runtime(ctx: ToolContext) -> browser_tools._BrowserRuntime:  # pyright: ignore[reportPrivateUsage]
    return browser_tools._BrowserRuntime(  # pyright: ignore[reportPrivateUsage]
        browser_tools.BrowserRuntimeConfig.from_env(),
        ctx.workspace,
        ctx.bus or _BUSES.get(_workspace_key(ctx.workspace)),
    )


class SubagentBrowserOpenTool(BrowserOpenTool):
    _scopes = {"subagent"}

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(_runtime(ctx))


class SubagentBrowserSnapshotTool(BrowserSnapshotTool):
    _scopes = {"subagent"}

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(_runtime(ctx))


class SubagentBrowserClickTool(BrowserClickTool):
    _scopes = {"subagent"}

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(_runtime(ctx))


class SubagentBrowserTypeTool(BrowserTypeTool):
    _scopes = {"subagent"}

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(_runtime(ctx))


class SubagentBrowserScrollTool(BrowserScrollTool):
    _scopes = {"subagent"}

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(_runtime(ctx))


class SubagentBrowserWaitTool(BrowserWaitTool):
    _scopes = {"subagent"}

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(_runtime(ctx))


class SubagentBrowserScreenshotTool(BrowserScreenshotTool):
    _scopes = {"subagent"}

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(_runtime(ctx))


class SubagentBrowserTabsTool(BrowserTabsTool):
    _scopes = {"subagent"}

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(_runtime(ctx))


class SubagentBrowserBackTool(BrowserBackTool):
    _scopes = {"subagent"}

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(_runtime(ctx))


class SubagentBrowserHandoffTool(BrowserHandoffTool):
    _scopes = {"subagent"}

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(_runtime(ctx))


class SubagentBrowserStatusTool(BrowserStatusTool):
    _scopes = {"subagent"}

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(_runtime(ctx))


class SubagentBrowserCloseTool(BrowserCloseTool):
    _scopes = {"subagent"}

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(_runtime(ctx))
