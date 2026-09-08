"""Subagent-scoped views of the persistent browser tool family.

Browser remains a capability, not a separate Agent type.  These thin wrappers
opt the existing browser tools into ToolLoader's ``subagent`` scope and reuse
the parent agent's message bus for human handoff notifications.
"""

from __future__ import annotations

import asyncio
import weakref
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools import browser as browser_tools
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
_LOOP_LOCKS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, asyncio.Lock]
] = weakref.WeakKeyDictionary()


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


def _operation_lock(endpoint: str) -> asyncio.Lock:
    """Serialize browser operations across isolated subagent tool registries."""
    loop = asyncio.get_running_loop()
    locks = _LOOP_LOCKS.setdefault(loop, {})
    return locks.setdefault(endpoint, asyncio.Lock())


class _SubagentBrowserToolMixin:
    _scopes = {"subagent"}

    @classmethod
    def create(cls, ctx: ToolContext):
        return cls(_runtime(ctx))

    async def execute(self, *args: Any, **kwargs: Any) -> str:
        # ``exclusive`` only serializes calls inside one ToolRegistry. Subagents
        # own isolated registries, so add a process-wide endpoint lock as the
        # last-resort guard against simultaneous clicks/navigation on the same
        # persistent Chromium session. Main is still expected to avoid launching
        # parallel browser workers because task-level interleaving is undesirable.
        async with _operation_lock(self.runtime.key):
            return await super().execute(*args, **kwargs)


class SubagentBrowserOpenTool(_SubagentBrowserToolMixin, BrowserOpenTool):
    pass


class SubagentBrowserSnapshotTool(_SubagentBrowserToolMixin, BrowserSnapshotTool):
    pass


class SubagentBrowserClickTool(_SubagentBrowserToolMixin, BrowserClickTool):
    pass


class SubagentBrowserTypeTool(_SubagentBrowserToolMixin, BrowserTypeTool):
    pass


class SubagentBrowserScrollTool(_SubagentBrowserToolMixin, BrowserScrollTool):
    pass


class SubagentBrowserWaitTool(_SubagentBrowserToolMixin, BrowserWaitTool):
    pass


class SubagentBrowserScreenshotTool(_SubagentBrowserToolMixin, BrowserScreenshotTool):
    pass


class SubagentBrowserTabsTool(_SubagentBrowserToolMixin, BrowserTabsTool):
    pass


class SubagentBrowserBackTool(_SubagentBrowserToolMixin, BrowserBackTool):
    pass


class SubagentBrowserHandoffTool(_SubagentBrowserToolMixin, BrowserHandoffTool):
    pass


class SubagentBrowserStatusTool(_SubagentBrowserToolMixin, BrowserStatusTool):
    pass


class SubagentBrowserCloseTool(_SubagentBrowserToolMixin, BrowserCloseTool):
    pass
