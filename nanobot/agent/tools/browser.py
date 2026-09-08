"""Browser automation tools for Linux/local Chrome and remote CDP browsers.

The browser capability is intentionally backend-light: nanobot owns the agent-facing
API while Playwright connects either to a local Chromium process or a user-provided
CDP endpoint (for example Browserless or another Chromium service).
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import Field

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.schema import BooleanSchema, IntegerSchema, StringSchema, tool_parameters_schema
from nanobot.config_base import Base
from nanobot.security.network import validate_url_target


class BrowserToolConfig(Base):
    """Browser automation configuration."""

    enabled: bool = False
    backend: Literal["local", "cdp"] = "local"
    cdp_url: str = ""
    headless: bool = True
    user_data_dir: str = "~/.nanobot/browser-profile"
    timeout_ms: int = Field(default=30_000, ge=1_000, le=120_000)
    viewport_width: int = Field(default=1280, ge=320, le=3840)
    viewport_height: int = Field(default=900, ge=240, le=2160)
    allow_downloads: bool = True
    screenshot_dir: str = "browser"
    allow_private_network: bool = False


@dataclass
class _BrowserState:
    playwright: Any | None = None
    browser: Any | None = None
    context: Any | None = None
    page: Any | None = None
    response_headers: dict[str, str] | None = None


_STATES: dict[str, _BrowserState] = {}
_STATE_LOCKS: dict[str, asyncio.Lock] = {}


def _state_key(config: BrowserToolConfig) -> str:
    return f"{config.backend}:{config.cdp_url}:{Path(config.user_data_dir).expanduser()}"


def _looks_like_cloudflare_challenge(*, title: str, body: str, headers: dict[str, str] | None) -> bool:
    if headers and headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    haystack = f"{title}\n{body[:6000]}".lower()
    markers = (
        "just a moment...",
        "checking your browser",
        "verify you are human",
        "performing security verification",
        "cloudflare ray id",
    )
    return any(marker in haystack for marker in markers)


def _validate_navigation(url: str, *, allow_private_network: bool) -> tuple[bool, str]:
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        return False, str(exc)
    if parsed.scheme not in {"http", "https"}:
        return False, "browser navigation only supports http/https URLs"
    if allow_private_network:
        return True, ""
    return validate_url_target(url)


class _BrowserRuntime:
    def __init__(self, config: BrowserToolConfig, workspace: str) -> None:
        self.config = config
        self.workspace = Path(workspace).expanduser()
        self.key = _state_key(config)

    @property
    def state(self) -> _BrowserState:
        return _STATES.setdefault(self.key, _BrowserState())

    @property
    def lock(self) -> asyncio.Lock:
        return _STATE_LOCKS.setdefault(self.key, asyncio.Lock())

    async def ensure_page(self) -> Any:
        async with self.lock:
            state = self.state
            if state.page is not None and not state.page.is_closed():
                return state.page
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise RuntimeError(
                    "browser support is not installed; install nanobot-ai[browser] and run "
                    "'playwright install chromium' for the local backend"
                ) from exc

            state.playwright = await async_playwright().start()
            chromium = state.playwright.chromium
            if self.config.backend == "cdp":
                if not self.config.cdp_url.strip():
                    raise RuntimeError("tools.browser.cdpUrl is required when backend='cdp'")
                state.browser = await chromium.connect_over_cdp(
                    self.config.cdp_url,
                    timeout=self.config.timeout_ms,
                )
                contexts = state.browser.contexts
                state.context = contexts[0] if contexts else await state.browser.new_context(
                    accept_downloads=self.config.allow_downloads,
                    viewport={"width": self.config.viewport_width, "height": self.config.viewport_height},
                )
            else:
                profile = Path(self.config.user_data_dir).expanduser()
                profile.mkdir(parents=True, exist_ok=True)
                state.context = await chromium.launch_persistent_context(
                    str(profile),
                    headless=self.config.headless,
                    accept_downloads=self.config.allow_downloads,
                    viewport={"width": self.config.viewport_width, "height": self.config.viewport_height},
                )
            pages = state.context.pages
            state.page = pages[0] if pages else await state.context.new_page()
            state.page.set_default_timeout(self.config.timeout_ms)
            return state.page

    async def close(self) -> None:
        async with self.lock:
            state = self.state
            try:
                if state.context is not None:
                    await state.context.close()
                elif state.browser is not None:
                    await state.browser.close()
            finally:
                if state.playwright is not None:
                    await state.playwright.stop()
                _STATES[self.key] = _BrowserState()

    async def status(self) -> dict[str, Any]:
        page = await self.ensure_page()
        title = await page.title()
        body = await page.locator("body").inner_text(timeout=min(self.config.timeout_ms, 5000))
        challenge = _looks_like_cloudflare_challenge(
            title=title,
            body=body,
            headers=self.state.response_headers,
        )
        return {
            "url": page.url,
            "title": title,
            "status": "human_required" if challenge else "ok",
            "reason": "cloudflare_challenge" if challenge else None,
        }

    async def snapshot(self, *, max_chars: int = 14_000) -> dict[str, Any]:
        page = await self.ensure_page()
        script = r"""
() => {
  const selectors = 'a,button,input,textarea,select,[role="button"],[role="link"],[contenteditable="true"]';
  const nodes = Array.from(document.querySelectorAll(selectors));
  let index = 1;
  const items = [];
  for (const el of nodes) {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (rect.width <= 0 || rect.height <= 0 || style.visibility === 'hidden' || style.display === 'none') continue;
    let ref = el.getAttribute('data-nanobot-ref');
    if (!ref) {
      ref = `e${index++}`;
      el.setAttribute('data-nanobot-ref', ref);
    }
    const role = el.getAttribute('role') || el.tagName.toLowerCase();
    const text = (el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder') || el.getAttribute('name') || '').trim().replace(/\s+/g, ' ');
    const value = ('value' in el && typeof el.value === 'string') ? el.value : '';
    items.push({ref, role, text: text.slice(0, 240), value: value.slice(0, 240)});
  }
  return {title: document.title, url: location.href, text: (document.body?.innerText || '').slice(0, 10000), items};
}
"""
        data = await page.evaluate(script)
        status = await self.status()
        data["status"] = status["status"]
        data["reason"] = status["reason"]
        rendered = json.dumps(data, ensure_ascii=False)
        if len(rendered) > max_chars:
            data["text"] = data.get("text", "")[: max(1000, max_chars // 3)]
            data["items"] = data.get("items", [])[:120]
            data["truncated"] = True
        return data

    async def locator_for_ref(self, ref: str) -> Any:
        if not re.fullmatch(r"e\d+", ref):
            raise RuntimeError("invalid element ref; call browser_snapshot and use a returned ref")
        page = await self.ensure_page()
        locator = page.locator(f'[data-nanobot-ref="{ref}"]')
        if await locator.count() < 1:
            raise RuntimeError("element ref is stale; call browser_snapshot again")
        return locator.first


def _runtime(ctx: ToolContext) -> _BrowserRuntime:
    return _BrowserRuntime(ctx.config.browser, ctx.workspace)


class _BrowserTool(Tool):
    config_key = "browser"

    def __init__(self, runtime: _BrowserRuntime) -> None:
        self.runtime = runtime

    @classmethod
    def config_cls(cls):
        return BrowserToolConfig

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.browser.enabled

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(_runtime(ctx))

    @property
    def exclusive(self) -> bool:
        return True


@tool_parameters(tool_parameters_schema(url=StringSchema("HTTP or HTTPS URL to open."), required=["url"]))
class BrowserOpenTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_open"

    @property
    def description(self) -> str:
        return "Open a public web URL in the persistent browser session. Returns page status and detects Cloudflare challenges."

    async def execute(self, url: str, **kwargs: Any) -> str:
        ok, error = _validate_navigation(url, allow_private_network=self.runtime.config.allow_private_network)
        if not ok:
            return ToolResult.error(f"Error: blocked browser URL: {error}")
        try:
            page = await self.runtime.ensure_page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=self.runtime.config.timeout_ms)
            self.runtime.state.response_headers = await response.all_headers() if response else None
            return json.dumps(await self.runtime.status(), ensure_ascii=False)
        except Exception as exc:
            return ToolResult.error(f"Error: browser navigation failed: {exc}")


@tool_parameters(tool_parameters_schema(max_chars=IntegerSchema("Maximum approximate snapshot characters.", minimum=2000, maximum=30000)))
class BrowserSnapshotTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_snapshot"

    @property
    def description(self) -> str:
        return "Read the current page as compact text plus interactive element refs. Use returned refs with browser_click or browser_type."

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, max_chars: int = 14_000, **kwargs: Any) -> str:
        try:
            return json.dumps(await self.runtime.snapshot(max_chars=max_chars), ensure_ascii=False)
        except Exception as exc:
            return ToolResult.error(f"Error: browser snapshot failed: {exc}")


@tool_parameters(tool_parameters_schema(ref=StringSchema("Element ref returned by browser_snapshot."), required=["ref"]))
class BrowserClickTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_click"

    @property
    def description(self) -> str:
        return "Click an element by ref from browser_snapshot, then return the updated page status."

    async def execute(self, ref: str, **kwargs: Any) -> str:
        try:
            locator = await self.runtime.locator_for_ref(ref)
            await locator.click()
            page = await self.runtime.ensure_page()
            await page.wait_for_timeout(250)
            return json.dumps(await self.runtime.status(), ensure_ascii=False)
        except Exception as exc:
            return ToolResult.error(f"Error: browser click failed: {exc}")


@tool_parameters(tool_parameters_schema(ref=StringSchema("Element ref returned by browser_snapshot."), text=StringSchema("Text to enter."), submit=BooleanSchema("Press Enter after filling."), required=["ref", "text"]))
class BrowserTypeTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_type"

    @property
    def description(self) -> str:
        return "Fill an input or editable element by ref. Optionally press Enter afterwards."

    async def execute(self, ref: str, text: str, submit: bool = False, **kwargs: Any) -> str:
        try:
            locator = await self.runtime.locator_for_ref(ref)
            await locator.fill(text)
            if submit:
                await locator.press("Enter")
            return json.dumps(await self.runtime.status(), ensure_ascii=False)
        except Exception as exc:
            return ToolResult.error(f"Error: browser type failed: {exc}")


@tool_parameters(tool_parameters_schema(full_page=BooleanSchema("Capture the full scrollable page rather than only the viewport.")))
class BrowserScreenshotTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_screenshot"

    @property
    def description(self) -> str:
        return "Capture the current browser page to a PNG in the workspace and return its local path."

    async def execute(self, full_page: bool = False, **kwargs: Any) -> str:
        try:
            page = await self.runtime.ensure_page()
            out_dir = self.runtime.workspace / self.runtime.config.screenshot_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / "browser-screenshot.png"
            await page.screenshot(path=str(path), full_page=full_page)
            return json.dumps({"status": "ok", "path": str(path), "url": page.url}, ensure_ascii=False)
        except Exception as exc:
            return ToolResult.error(f"Error: browser screenshot failed: {exc}")


@tool_parameters(tool_parameters_schema())
class BrowserBackTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_back"

    @property
    def description(self) -> str:
        return "Navigate the current browser tab back one history entry."

    async def execute(self, **kwargs: Any) -> str:
        try:
            page = await self.runtime.ensure_page()
            await page.go_back(wait_until="domcontentloaded", timeout=self.runtime.config.timeout_ms)
            return json.dumps(await self.runtime.status(), ensure_ascii=False)
        except Exception as exc:
            return ToolResult.error(f"Error: browser back failed: {exc}")


@tool_parameters(tool_parameters_schema())
class BrowserCloseTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_close"

    @property
    def description(self) -> str:
        return "Close the current browser session. Persistent local profile data remains on disk."

    async def execute(self, **kwargs: Any) -> str:
        try:
            await self.runtime.close()
            return json.dumps({"status": "ok"})
        except Exception as exc:
            return ToolResult.error(f"Error: browser close failed: {exc}")
