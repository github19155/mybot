"""Subagent-scoped views of the persistent Browser capability.

The Browser implementation remains in ``browser.py``. This module only exposes
that same tool family to ToolLoader's ``subagent`` scope and supplies the parent
message bus so human handoff and progress notifications can reach the originating
user.
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
    """Bind the stable agent workspace to the bus used for child notifications."""
    _BUSES[_workspace_key(workspace)] = bus


def subagent_bus_for_workspace(workspace: str | Path) -> MessageBus | None:
    """Return the parent bus bound to a subagent workspace, when available."""
    return _BUSES.get(_workspace_key(workspace))


def _runtime(ctx: ToolContext) -> browser_tools._BrowserRuntime:  # pyright: ignore[reportPrivateUsage]
    return browser_tools._BrowserRuntime(  # pyright: ignore[reportPrivateUsage]
        browser_tools.BrowserRuntimeConfig.from_env(),
        ctx.workspace,
        ctx.bus or subagent_bus_for_workspace(ctx.workspace),
    )


class _SubagentBrowserView:
    """Scope adapter shared by every Browser tool view."""

    _scopes = {"subagent"}

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(_runtime(ctx))  # type: ignore[call-arg]


class SubagentBrowserOpenTool(_SubagentBrowserView, BrowserOpenTool):
    pass


class SubagentBrowserSnapshotTool(_SubagentBrowserView, BrowserSnapshotTool):
    pass


class SubagentBrowserClickTool(_SubagentBrowserView, BrowserClickTool):
    pass


class SubagentBrowserTypeTool(_SubagentBrowserView, BrowserTypeTool):
    pass


class SubagentBrowserScrollTool(_SubagentBrowserView, BrowserScrollTool):
    pass


class SubagentBrowserWaitTool(_SubagentBrowserView, BrowserWaitTool):
    pass


class SubagentBrowserScreenshotTool(_SubagentBrowserView, BrowserScreenshotTool):
    pass


class SubagentBrowserTabsTool(_SubagentBrowserView, BrowserTabsTool):
    pass


class SubagentBrowserBackTool(_SubagentBrowserView, BrowserBackTool):
    pass


class SubagentBrowserHandoffTool(_SubagentBrowserView, BrowserHandoffTool):
    pass


class SubagentBrowserStatusTool(_SubagentBrowserView, BrowserStatusTool):
    pass


class SubagentBrowserCloseTool(_SubagentBrowserView, BrowserCloseTool):
    pass
