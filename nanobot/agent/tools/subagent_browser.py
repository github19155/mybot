"""Subagent-scoped views of the persistent browser tool family.

Browser remains a capability, not a separate Agent type.  The core browser
implementations stay unchanged; these thin subclasses only opt the same tools
into ToolLoader's ``subagent`` scope.
"""

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


class SubagentBrowserOpenTool(BrowserOpenTool):
    _scopes = {"subagent"}


class SubagentBrowserSnapshotTool(BrowserSnapshotTool):
    _scopes = {"subagent"}


class SubagentBrowserClickTool(BrowserClickTool):
    _scopes = {"subagent"}


class SubagentBrowserTypeTool(BrowserTypeTool):
    _scopes = {"subagent"}


class SubagentBrowserScrollTool(BrowserScrollTool):
    _scopes = {"subagent"}


class SubagentBrowserWaitTool(BrowserWaitTool):
    _scopes = {"subagent"}


class SubagentBrowserScreenshotTool(BrowserScreenshotTool):
    _scopes = {"subagent"}


class SubagentBrowserTabsTool(BrowserTabsTool):
    _scopes = {"subagent"}


class SubagentBrowserBackTool(BrowserBackTool):
    _scopes = {"subagent"}


class SubagentBrowserHandoffTool(BrowserHandoffTool):
    _scopes = {"subagent"}


class SubagentBrowserStatusTool(BrowserStatusTool):
    _scopes = {"subagent"}


class SubagentBrowserCloseTool(BrowserCloseTool):
    _scopes = {"subagent"}
