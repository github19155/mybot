"""Persistent remote-browser tools with human handoff support.

The browser process is intentionally external to nanobot.  A Linux sidecar owns
Chromium, its persistent profile, and the noVNC desktop.  nanobot connects over
CDP, keeps ordinary browser failures recoverable, and hands explicit human
verification (CAPTCHA, Cloudflare challenge, OTP/2FA) to the current user.

Playwright is imported lazily so installations that do not enable browser tools
carry no browser dependency or browser binaries.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import ToolContext, current_request_context
from nanobot.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.bus.events import OutboundMessage
from nanobot.security.network import validate_url_target

BrowserOwner = Literal["agent", "human"]


@dataclass(frozen=True)
class BrowserRuntimeConfig:
    """Environment-owned browser runtime configuration.

    Keeping this deployment-oriented configuration out of Config/ToolsConfig
    means browser support can remain an optional Docker profile instead of
    forcing every nanobot install to carry Playwright.
    """

    enabled: bool = False
    cdp_endpoint: str = "http://nanobot-browser:9222"
    control_endpoint: str = "http://nanobot-browser:6081"
    takeover_url: str = ""
    timeout_ms: int = 30_000
    human_timeout_sec: int = 600
    recovery_attempts: int = 2
    screenshot_dir: str = "browser"
    allow_private_network: bool = False

    @classmethod
    def from_env(cls) -> "BrowserRuntimeConfig":
        return cls(
            enabled=_env_bool("NANOBOT_BROWSER_ENABLED", False),
            cdp_endpoint=os.environ.get(
                "NANOBOT_BROWSER_CDP_ENDPOINT", "http://nanobot-browser:9222"
            ).strip(),
            control_endpoint=os.environ.get(
                "NANOBOT_BROWSER_CONTROL_ENDPOINT", "http://nanobot-browser:6081"
            ).strip(),
            takeover_url=os.environ.get("NANOBOT_BROWSER_TAKEOVER_URL", "").strip(),
            timeout_ms=_env_int(
                "NANOBOT_BROWSER_TIMEOUT_MS", 30_000, minimum=1_000, maximum=120_000
            ),
            human_timeout_sec=_env_int(
                "NANOBOT_BROWSER_HUMAN_TIMEOUT_SEC", 600, minimum=30, maximum=3_600
            ),
            recovery_attempts=_env_int(
                "NANOBOT_BROWSER_RECOVERY_ATTEMPTS", 2, minimum=0, maximum=5
            ),
            screenshot_dir=os.environ.get(
                "NANOBOT_BROWSER_SCREENSHOT_DIR", "browser"
            ).strip()
            or "browser",
            allow_private_network=_env_bool("NANOBOT_BROWSER_ALLOW_PRIVATE_NETWORK", False),
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    try:
        value = int(raw) if raw is not None else default
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


@dataclass
class _BrowserState:
    playwright: Any | None = None
    browser: Any | None = None
    context: Any | None = None
    page: Any | None = None
    response_headers: dict[str, str] | None = None
    owner: BrowserOwner = "agent"
    notified_signature: str | None = None
    last_handoff: dict[str, Any] = field(default_factory=dict)


_STATES: dict[str, _BrowserState] = {}
_STATE_LOCKS: dict[str, asyncio.Lock] = {}


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


def _classify_intervention(
    *,
    title: str,
    text: str,
    frames: list[str],
    turnstile: bool,
    captcha: bool,
    otp: bool,
    password: bool,
    headers: dict[str, str] | None = None,
) -> tuple[str, str | None]:
    """Classify page state without attempting to solve human verification."""
    normalized_headers = {k.lower(): v for k, v in (headers or {}).items()}
    if normalized_headers.get("cf-mitigated", "").lower() == "challenge":
        return "human_required", "cloudflare_challenge"

    frame_text = "\n".join(frames).lower()
    if "challenges.cloudflare.com" in frame_text or turnstile:
        return "human_required", "cloudflare_challenge"
    if any(marker in frame_text for marker in ("recaptcha", "hcaptcha", "captcha")) or captcha:
        return "human_required", "captcha"
    if otp:
        return "human_required", "verification_code"

    haystack = f"{title}\n{text[:7000]}".lower()
    challenge_markers = (
        "verify you are human",
        "checking your browser",
        "just a moment...",
        "security verification",
        "attention required! | cloudflare",
        "cloudflare ray id",
        "人机验证",
        "安全验证",
        "验证您是真人",
    )
    if any(marker in haystack for marker in challenge_markers):
        return "human_required", "browser_challenge"

    captcha_markers = (
        "complete the captcha",
        "captcha verification",
        "i'm not a robot",
        "i am not a robot",
        "验证码",
    )
    if any(marker in haystack for marker in captcha_markers):
        return "human_required", "captcha"

    otp_markers = (
        "two-factor authentication",
        "two factor authentication",
        "verification code",
        "one-time code",
        "one time code",
        "短信验证码",
        "动态验证码",
        "双重验证",
    )
    if any(marker in haystack for marker in otp_markers):
        return "human_required", "verification_code"

    if password:
        return "login_required", "login"
    return "ok", None


class _BrowserRuntime:
    def __init__(
        self,
        config: BrowserRuntimeConfig,
        workspace: str,
        bus: Any | None,
    ) -> None:
        self.config = config
        self.workspace = Path(workspace).expanduser()
        self.bus = bus
        self.key = self.config.cdp_endpoint

    @property
    def state(self) -> _BrowserState:
        return _STATES.setdefault(self.key, _BrowserState())

    @property
    def lock(self) -> asyncio.Lock:
        return _STATE_LOCKS.setdefault(self.key, asyncio.Lock())

    async def ensure_page(self, *, reconnect: bool = True) -> Any:
        state = self.state
        if (
            state.page is not None
            and not state.page.is_closed()
            and state.browser is not None
            and state.browser.is_connected()
        ):
            return state.page

        async with self.lock:
            state = self.state
            if (
                state.page is not None
                and not state.page.is_closed()
                and state.browser is not None
                and state.browser.is_connected()
            ):
                return state.page
            if reconnect:
                await self._disconnect_locked()
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise RuntimeError(
                    "browser support is not installed; install nanobot-ai[browser] "
                    "or build the Docker image with NANOBOT_EXTRAS=browser"
                ) from exc

            if not self.config.cdp_endpoint:
                raise RuntimeError("NANOBOT_BROWSER_CDP_ENDPOINT is empty")
            state.playwright = await async_playwright().start()
            state.browser = await state.playwright.chromium.connect_over_cdp(
                self.config.cdp_endpoint,
                timeout=self.config.timeout_ms,
            )
            contexts = state.browser.contexts
            state.context = contexts[0] if contexts else await state.browser.new_context(
                accept_downloads=True,
            )
            pages = state.context.pages
            state.page = pages[0] if pages else await state.context.new_page()
            state.page.set_default_timeout(self.config.timeout_ms)
            return state.page

    async def _disconnect_locked(self) -> None:
        state = self.state
        # Never browser.close(): the sidecar owns the persistent browser and a
        # human may currently be attached through noVNC.  Stopping Playwright
        # tears down only nanobot's client connection.
        playwright = state.playwright
        state.playwright = None
        state.browser = None
        state.context = None
        state.page = None
        state.response_headers = None
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass

    async def disconnect(self) -> None:
        async with self.lock:
            await self._disconnect_locked()

    async def _page_probe(self) -> dict[str, Any]:
        page = await self.ensure_page()
        try:
            return await page.evaluate(
                r"""
() => {
  const bodyText = (document.body?.innerText || '').slice(0, 12000);
  const frameSrcs = Array.from(document.querySelectorAll('iframe'))
    .map((el) => el.getAttribute('src') || '')
    .filter(Boolean)
    .slice(0, 50);
  const turnstile = Boolean(document.querySelector('.cf-turnstile, [data-callback][data-sitekey]'));
  const captcha = Boolean(document.querySelector(
    'iframe[src*="recaptcha" i], iframe[src*="hcaptcha" i], [class*="captcha" i], [id*="captcha" i], input[name*="captcha" i]'
  ));
  const otp = Boolean(document.querySelector(
    'input[autocomplete="one-time-code"], input[name*="otp" i], input[name*="verification" i], input[inputmode="numeric"][maxlength="6"]'
  ));
  const password = Boolean(document.querySelector('input[type="password"]'));
  return {title: document.title || '', text: bodyText, frames: frameSrcs, turnstile, captcha, otp, password};
}
"""
            )
        except Exception:
            return {
                "title": await page.title(),
                "text": "",
                "frames": [],
                "turnstile": False,
                "captcha": False,
                "otp": False,
                "password": False,
            }

    async def status(self) -> dict[str, Any]:
        page = await self.ensure_page()
        probe = await self._page_probe()
        status, reason = _classify_intervention(
            title=str(probe.get("title") or ""),
            text=str(probe.get("text") or ""),
            frames=[str(item) for item in probe.get("frames") or []],
            turnstile=bool(probe.get("turnstile")),
            captcha=bool(probe.get("captcha")),
            otp=bool(probe.get("otp")),
            password=bool(probe.get("password")),
            headers=self.state.response_headers,
        )
        return {
            "status": status,
            "reason": reason,
            "owner": self.state.owner,
            "url": page.url,
            "title": str(probe.get("title") or ""),
            **({"takeover_url": self.config.takeover_url} if self.config.takeover_url else {}),
        }

    async def snapshot(self, *, max_chars: int = 14_000) -> dict[str, Any]:
        page = await self.ensure_page()
        data = await page.evaluate(
            r"""
() => {
  const selectors = [
    'a', 'button', 'input', 'textarea', 'select', 'summary',
    '[role="button"]', '[role="link"]', '[role="checkbox"]', '[role="radio"]',
    '[role="tab"]', '[contenteditable="true"]'
  ].join(',');
  const nodes = Array.from(document.querySelectorAll(selectors));
  let next = 1;
  const items = [];
  for (const el of nodes) {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (rect.width <= 0 || rect.height <= 0 || style.visibility === 'hidden' || style.display === 'none') continue;
    let ref = el.getAttribute('data-nanobot-ref');
    if (!ref) {
      ref = `e${next++}`;
      el.setAttribute('data-nanobot-ref', ref);
    }
    const role = el.getAttribute('role') || el.tagName.toLowerCase();
    const label = (
      el.getAttribute('aria-label') ||
      el.innerText ||
      el.getAttribute('placeholder') ||
      el.getAttribute('name') ||
      ''
    ).trim().replace(/\s+/g, ' ');
    const inputType = (el.getAttribute('type') || '').toLowerCase();
    items.push({
      ref,
      role,
      label: label.slice(0, 240),
      input_type: inputType || undefined,
      checked: typeof el.checked === 'boolean' ? el.checked : undefined,
      disabled: Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true') || undefined
    });
  }
  return {
    title: document.title || '',
    url: location.href,
    text: (document.body?.innerText || '').slice(0, 10000),
    items
  };
}
"""
        )
        current = await self.status()
        data.update({
            "status": current["status"],
            "reason": current["reason"],
            "owner": current["owner"],
        })
        rendered = json.dumps(data, ensure_ascii=False)
        if len(rendered) > max_chars:
            data["text"] = str(data.get("text") or "")[: max(1000, max_chars // 3)]
            data["items"] = list(data.get("items") or [])[:120]
            data["truncated"] = True
        return data

    async def locator_for_ref(self, ref: str) -> Any:
        if re.fullmatch(r"e\d+", ref) is None:
            raise RuntimeError("invalid element ref; call browser_snapshot and use a returned ref")
        page = await self.ensure_page()
        locator = page.locator(f'[data-nanobot-ref="{ref}"]')
        if await locator.count() < 1:
            raise RuntimeError("element ref is stale; call browser_snapshot again")
        return locator.first

    async def _control_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        base = self.config.control_endpoint.rstrip("/")
        if not base:
            return None
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.request(method, f"{base}{path}", json=payload)
                response.raise_for_status()
                body = response.json()
                return body if isinstance(body, dict) else None
        except (httpx.HTTPError, ValueError):
            return None

    async def _notify_human(self, details: dict[str, Any]) -> None:
        ctx = current_request_context()
        if ctx is None or self.bus is None:
            return
        signature = f"{ctx.session_key}:{details.get('reason')}:{details.get('url')}"
        if self.state.notified_signature == signature:
            return
        self.state.notified_signature = signature
        takeover = self.config.takeover_url or "Open the nanobot WebUI browser takeover page."
        content = (
            "Browser needs human assistance.\n"
            f"Site: {details.get('url') or '(unknown)'}\n"
            f"Reason: {details.get('reason') or 'human_verification'}\n"
            f"Take over: {takeover}\n"
            "The current browser session is paused for you. Complete the verification; "
            "the agent will resume automatically when the page is clear."
        )
        try:
            await self.bus.publish_outbound(
                OutboundMessage(channel=ctx.channel, chat_id=ctx.chat_id, content=content)
            )
        except Exception:
            # A failed notification must not corrupt browser ownership state.
            pass

    async def _enter_handoff(
        self,
        details: dict[str, Any],
        *,
        force_release: bool = False,
    ) -> dict[str, Any]:
        self.state.owner = "human"
        self.state.last_handoff = dict(details)
        await self._control_request("POST", "/handoff", details)
        await self._notify_human(details)

        deadline = asyncio.get_running_loop().time() + self.config.human_timeout_sec
        initial_url = str(details.get("url") or "")
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(2.0)
            try:
                current = await self.status()
            except Exception:
                continue
            remote = await self._control_request("GET", "/state")
            released = bool(remote and remote.get("owner") == "agent")
            still_human_required = current["status"] == "human_required"
            manual_page_completed = (
                force_release
                and current["url"] != initial_url
                and current["status"] not in {"human_required", "login_required"}
            )
            if (released and not still_human_required) or (
                not force_release and not still_human_required
            ) or manual_page_completed:
                self.state.owner = "agent"
                self.state.notified_signature = None
                await self._control_request("POST", "/release")
                current["owner"] = "agent"
                current["resumed_after_human"] = True
                return current
            if released and still_human_required:
                # The user clicked Done too early. Keep ownership with the human
                # rather than allowing the model to race the challenge UI.
                await self._control_request("POST", "/handoff", details)

        current = await self.status()
        current.update(
            {
                "status": "human_required",
                "owner": "human",
                "timed_out": True,
                "message": (
                    "Human takeover is still active. Ask the user to finish verification, "
                    "then call browser_status or browser_handoff again."
                ),
            }
        )
        return current

    async def post_action(self) -> dict[str, Any]:
        current = await self.status()
        if current["status"] == "human_required":
            return await self._enter_handoff(current)
        return current

    async def run_with_recovery(self, label: str, operation: Any) -> Any:
        last_error: BaseException | None = None
        for attempt in range(self.config.recovery_attempts + 1):
            try:
                return await operation()
            except Exception as exc:
                last_error = exc
                name = type(exc).__name__.lower()
                text = str(exc).lower()
                recoverable = any(
                    marker in name or marker in text
                    for marker in (
                        "targetclosed",
                        "browser has been closed",
                        "connection closed",
                        "websocket",
                        "econnreset",
                        "timeout",
                    )
                )
                if not recoverable or attempt >= self.config.recovery_attempts:
                    break
                await self.disconnect()
                await asyncio.sleep(min(0.5 * (2**attempt), 2.0))
                await self.ensure_page()
        assert last_error is not None
        raise RuntimeError(f"{label} failed after recovery attempts: {last_error}") from last_error

    def assert_agent_owner(self) -> None:
        if self.state.owner != "agent":
            raise RuntimeError(
                "browser is currently owned by the human operator; wait for takeover to finish"
            )


def _runtime(ctx: ToolContext) -> _BrowserRuntime:
    return _BrowserRuntime(BrowserRuntimeConfig.from_env(), ctx.workspace, ctx.bus)


class _BrowserTool(Tool):
    """Shared deployment and ownership behavior for browser tools."""

    config_key = "browser"

    def __init__(self, runtime: _BrowserRuntime) -> None:
        self.runtime = runtime

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return BrowserRuntimeConfig.from_env().enabled

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(_runtime(ctx))

    @property
    def exclusive(self) -> bool:
        # A browser session is intentionally serialized. This also prevents the
        # model from issuing a second action while a human handoff is pending.
        return True


@tool_parameters(
    tool_parameters_schema(
        url=StringSchema("Public HTTP or HTTPS URL to open."),
        required=["url"],
    )
)
class BrowserOpenTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_open"

    @property
    def description(self) -> str:
        return (
            "Open a URL in the persistent remote browser. Use for JavaScript-heavy or "
            "interactive pages after lightweight web fetch is insufficient. Human verification "
            "is detected and handed off instead of being bypassed."
        )

    async def execute(self, url: str, **kwargs: Any) -> str:
        self.runtime.assert_agent_owner()
        ok, error = _validate_navigation(
            url,
            allow_private_network=self.runtime.config.allow_private_network,
        )
        if not ok:
            return ToolResult.error(f"Error: blocked browser URL: {error}")

        async def operation() -> dict[str, Any]:
            page = await self.runtime.ensure_page()
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.runtime.config.timeout_ms,
            )
            self.runtime.state.response_headers = await response.all_headers() if response else None
            return await self.runtime.post_action()

        try:
            return json.dumps(
                await self.runtime.run_with_recovery("browser navigation", operation),
                ensure_ascii=False,
            )
        except Exception as exc:
            return ToolResult.error(f"Error: {exc}")


@tool_parameters(
    tool_parameters_schema(
        max_chars=IntegerSchema(
            description="Maximum approximate snapshot characters.",
            minimum=2_000,
            maximum=30_000,
        )
    )
)
class BrowserSnapshotTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_snapshot"

    @property
    def description(self) -> str:
        return (
            "Read the current page as compact text plus interactive element refs. "
            "Use refs with browser_click/browser_type. Password values are never exposed."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, max_chars: int = 14_000, **kwargs: Any) -> str:
        if self.runtime.state.owner != "agent":
            return json.dumps(await self.runtime.status(), ensure_ascii=False)
        try:
            data = await self.runtime.snapshot(max_chars=max_chars)
            if data["status"] == "human_required":
                data = await self.runtime._enter_handoff(data)  # noqa: SLF001
            return json.dumps(data, ensure_ascii=False)
        except Exception as exc:
            return ToolResult.error(f"Error: browser snapshot failed: {exc}")


@tool_parameters(
    tool_parameters_schema(
        ref=StringSchema("Element ref returned by browser_snapshot."),
        required=["ref"],
    )
)
class BrowserClickTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_click"

    @property
    def description(self) -> str:
        return "Click an element ref and return updated page state."

    async def execute(self, ref: str, **kwargs: Any) -> str:
        self.runtime.assert_agent_owner()

        async def operation() -> dict[str, Any]:
            locator = await self.runtime.locator_for_ref(ref)
            await locator.click()
            page = await self.runtime.ensure_page()
            await page.wait_for_timeout(250)
            return await self.runtime.post_action()

        try:
            return json.dumps(
                await self.runtime.run_with_recovery("browser click", operation),
                ensure_ascii=False,
            )
        except Exception as exc:
            return ToolResult.error(f"Error: {exc}")


@tool_parameters(
    tool_parameters_schema(
        ref=StringSchema("Element ref returned by browser_snapshot."),
        text=StringSchema("Text to enter."),
        submit=BooleanSchema(description="Press Enter after filling."),
        required=["ref", "text"],
    )
)
class BrowserTypeTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_type"

    @property
    def description(self) -> str:
        return "Fill an input/editable element by ref and optionally press Enter."

    async def execute(
        self,
        ref: str,
        text: str,
        submit: bool = False,
        **kwargs: Any,
    ) -> str:
        self.runtime.assert_agent_owner()

        async def operation() -> dict[str, Any]:
            locator = await self.runtime.locator_for_ref(ref)
            await locator.fill(text)
            if submit:
                await locator.press("Enter")
            return await self.runtime.post_action()

        try:
            return json.dumps(
                await self.runtime.run_with_recovery("browser input", operation),
                ensure_ascii=False,
            )
        except Exception as exc:
            return ToolResult.error(f"Error: {exc}")


@tool_parameters(
    tool_parameters_schema(
        delta_y=IntegerSchema(
            description="Vertical pixels to scroll; positive moves down, negative moves up.",
            minimum=-20_000,
            maximum=20_000,
        )
    )
)
class BrowserScrollTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_scroll"

    @property
    def description(self) -> str:
        return "Scroll the active page, then return updated browser state."

    async def execute(self, delta_y: int = 700, **kwargs: Any) -> str:
        self.runtime.assert_agent_owner()
        try:
            page = await self.runtime.ensure_page()
            await page.mouse.wheel(0, delta_y)
            await page.wait_for_timeout(150)
            return json.dumps(await self.runtime.post_action(), ensure_ascii=False)
        except Exception as exc:
            return ToolResult.error(f"Error: browser scroll failed: {exc}")


@tool_parameters(
    tool_parameters_schema(
        milliseconds=IntegerSchema(
            description="Milliseconds to wait before re-reading page state.",
            minimum=50,
            maximum=30_000,
        )
    )
)
class BrowserWaitTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_wait"

    @property
    def description(self) -> str:
        return "Wait briefly for dynamic page content, then return current state."

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, milliseconds: int = 1_000, **kwargs: Any) -> str:
        try:
            page = await self.runtime.ensure_page()
            await page.wait_for_timeout(milliseconds)
            current = await self.runtime.status()
            if current["status"] == "human_required" and self.runtime.state.owner == "agent":
                current = await self.runtime._enter_handoff(current)  # noqa: SLF001
            return json.dumps(current, ensure_ascii=False)
        except Exception as exc:
            return ToolResult.error(f"Error: browser wait failed: {exc}")


@tool_parameters(
    tool_parameters_schema(
        full_page=BooleanSchema(
            description="Capture the full scrollable page instead of only the viewport."
        )
    )
)
class BrowserScreenshotTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_screenshot"

    @property
    def description(self) -> str:
        return (
            "Capture the active page to a PNG in the workspace. Use this for canvas, maps, "
            "charts, image-only controls, or when a vision-capable model/tool needs the page."
        )

    async def execute(self, full_page: bool = False, **kwargs: Any) -> str:
        self.runtime.assert_agent_owner()
        try:
            page = await self.runtime.ensure_page()
            out_dir = self.runtime.workspace / self.runtime.config.screenshot_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"browser-{int(time.time() * 1000)}.png"
            await page.screenshot(path=str(path), full_page=full_page)
            return json.dumps(
                {"status": "ok", "path": str(path), "url": page.url},
                ensure_ascii=False,
            )
        except Exception as exc:
            return ToolResult.error(f"Error: browser screenshot failed: {exc}")


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "Tab action.",
            enum=("list", "switch", "new", "close"),
        ),
        index=IntegerSchema(
            description="Zero-based tab index for switch/close.",
            minimum=0,
            maximum=100,
        ),
        url=StringSchema("Optional URL for a new tab."),
        required=["action"],
    )
)
class BrowserTabsTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_tabs"

    @property
    def description(self) -> str:
        return "List, switch, create, or close tabs in the persistent browser context."

    async def execute(
        self,
        action: str,
        index: int | None = None,
        url: str | None = None,
        **kwargs: Any,
    ) -> str:
        if action != "list":
            self.runtime.assert_agent_owner()
        try:
            await self.runtime.ensure_page()
            context = self.runtime.state.context
            assert context is not None
            pages = list(context.pages)
            if action == "list":
                result = []
                for i, page in enumerate(pages):
                    result.append({"index": i, "url": page.url, "title": await page.title()})
                return json.dumps({"tabs": result}, ensure_ascii=False)
            if action == "new":
                page = await context.new_page()
                self.runtime.state.page = page
                if url:
                    ok, error = _validate_navigation(
                        url,
                        allow_private_network=self.runtime.config.allow_private_network,
                    )
                    if not ok:
                        await page.close()
                        return ToolResult.error(f"Error: blocked browser URL: {error}")
                    await page.goto(url, wait_until="domcontentloaded")
                return json.dumps(await self.runtime.post_action(), ensure_ascii=False)
            if index is None or index >= len(pages):
                return ToolResult.error("Error: valid tab index is required")
            if action == "switch":
                self.runtime.state.page = pages[index]
                await pages[index].bring_to_front()
                return json.dumps(await self.runtime.status(), ensure_ascii=False)
            if action == "close":
                await pages[index].close()
                remaining = list(context.pages)
                self.runtime.state.page = remaining[-1] if remaining else await context.new_page()
                return json.dumps(await self.runtime.status(), ensure_ascii=False)
            return ToolResult.error(f"Error: unsupported tab action {action!r}")
        except Exception as exc:
            return ToolResult.error(f"Error: browser tab operation failed: {exc}")


@tool_parameters(tool_parameters_schema())
class BrowserBackTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_back"

    @property
    def description(self) -> str:
        return "Navigate the active tab back one history entry."

    async def execute(self, **kwargs: Any) -> str:
        self.runtime.assert_agent_owner()
        try:
            page = await self.runtime.ensure_page()
            response = await page.go_back(
                wait_until="domcontentloaded",
                timeout=self.runtime.config.timeout_ms,
            )
            self.runtime.state.response_headers = await response.all_headers() if response else None
            return json.dumps(await self.runtime.post_action(), ensure_ascii=False)
        except Exception as exc:
            return ToolResult.error(f"Error: browser back failed: {exc}")


@tool_parameters(
    tool_parameters_schema(
        reason=StringSchema(
            "Why human input is needed, e.g. login confirmation, account choice, or payment approval."
        )
    )
)
class BrowserHandoffTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_handoff"

    @property
    def description(self) -> str:
        return (
            "Deliberately hand the current browser to the human operator. Use when a page "
            "requires credentials, account selection, consent, 2FA, or another decision the "
            "user should make. Never use this to automate or bypass CAPTCHA/challenges."
        )

    async def execute(self, reason: str = "human_action_required", **kwargs: Any) -> str:
        self.runtime.assert_agent_owner()
        try:
            current = await self.runtime.status()
            current.update({"status": "human_required", "reason": reason})
            return json.dumps(
                await self.runtime._enter_handoff(current, force_release=True),  # noqa: SLF001
                ensure_ascii=False,
            )
        except Exception as exc:
            return ToolResult.error(f"Error: browser handoff failed: {exc}")


@tool_parameters(tool_parameters_schema())
class BrowserStatusTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_status"

    @property
    def description(self) -> str:
        return (
            "Read browser health, URL, page title, verification state, and current owner. "
            "This is the only browser operation safe to call after a handoff timeout."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        try:
            current = await self.runtime.status()
            remote = await self.runtime._control_request("GET", "/state")  # noqa: SLF001
            if remote:
                current["sidecar"] = remote
            if (
                self.runtime.state.owner == "human"
                and current["status"] not in {"human_required", "login_required"}
            ):
                self.runtime.state.owner = "agent"
                self.runtime.state.notified_signature = None
                current["owner"] = "agent"
                current["resumed_after_human"] = True
                await self.runtime._control_request("POST", "/release")  # noqa: SLF001
            return json.dumps(current, ensure_ascii=False)
        except Exception as exc:
            return ToolResult.error(f"Error: browser status failed: {exc}")


@tool_parameters(tool_parameters_schema())
class BrowserCloseTool(_BrowserTool):
    @property
    def name(self) -> str:
        return "browser_close"

    @property
    def description(self) -> str:
        return (
            "Disconnect nanobot from the remote browser. The sidecar browser, persistent profile, "
            "cookies, and human takeover desktop remain alive."
        )

    async def execute(self, **kwargs: Any) -> str:
        if self.runtime.state.owner != "agent":
            return ToolResult.error("Error: cannot disconnect while a human takeover is active")
        await self.runtime.disconnect()
        return json.dumps({"status": "ok", "browser_process": "preserved"})
