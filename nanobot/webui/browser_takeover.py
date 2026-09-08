"""Authenticated WebUI proxy for the private browser-takeover sidecar.

The browser sidecar never needs a public listener.  This module exposes a very
small same-origin surface through the existing WebUI gateway and reuses its
normal authentication (Bearer API token or trusted proxy assertion).
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from nanobot.webui.gateway_tokens import GatewayTokenStore
from nanobot.webui.http_utils import (
    case_insensitive_header,
    http_error,
    http_json_response,
    http_response,
    is_trusted_proxy_authenticated_request,
    parse_request_path,
)

_PREFIX = "/api/webui/browser-takeover"
_NO_STORE = [("Cache-Control", "no-store")]


def _control_endpoint() -> str:
    return os.environ.get(
        "NANOBOT_BROWSER_CONTROL_ENDPOINT",
        "http://nanobot-browser:6081",
    ).strip().rstrip("/")


def _browser_enabled() -> bool:
    return os.environ.get("NANOBOT_BROWSER_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _authorized(
    connection: Any,
    request: WsRequest,
    *,
    config: Any,
    tokens: GatewayTokenStore,
) -> bool:
    if is_trusted_proxy_authenticated_request(connection, request.headers, config):
        return True
    return tokens.check_api_token(request)


def _header(request: WsRequest, name: str) -> str:
    return case_insensitive_header(request.headers, name)


async def _json_request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, Any]]:
    endpoint = _control_endpoint()
    if not endpoint:
        return 503, {"error": "browser_control_unconfigured"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, f"{endpoint}{path}", json=payload)
    except httpx.HTTPError:
        return 503, {"error": "browser_control_unavailable"}
    try:
        body = response.json()
    except ValueError:
        body = {"error": "browser_control_invalid_response"}
    if not isinstance(body, dict):
        body = {"error": "browser_control_invalid_response"}
    return response.status_code, body


async def _screenshot(token: str) -> tuple[int, bytes, str]:
    endpoint = _control_endpoint()
    if not endpoint:
        return 503, b"browser control is unconfigured", "text/plain; charset=utf-8"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                f"{endpoint}/screenshot",
                headers={"X-Nanobot-Browser-Token": token},
            )
    except httpx.HTTPError:
        return 503, b"browser screenshot is unavailable", "text/plain; charset=utf-8"
    content_type = response.headers.get("content-type", "application/octet-stream")
    return response.status_code, response.content, content_type


async def handle_browser_takeover_request(
    connection: Any,
    request: WsRequest,
    *,
    config: Any,
    tokens: GatewayTokenStore,
) -> Response | None:
    """Handle WebUI takeover routes or return ``None`` for unrelated paths."""
    path, _query = parse_request_path(request.path)
    if not path.startswith(_PREFIX):
        return None

    if not _authorized(connection, request, config=config, tokens=tokens):
        return http_error(401, "Unauthorized")
    if not _browser_enabled():
        return http_error(503, "browser runtime is disabled")

    suffix = path[len(_PREFIX) :]
    if suffix == "/state":
        status, body = await _json_request("GET", "/state")
        # Do not leak the sidecar token anywhere except the authenticated WebUI.
        return http_json_response(body, status=status, extra_headers=_NO_STORE)

    token = _header(request, "X-Nanobot-Browser-Token")
    if not token:
        return http_error(400, "missing browser handoff token")

    if suffix == "/screenshot":
        status, body, content_type = await _screenshot(token)
        return http_response(
            body,
            status=status,
            content_type=content_type,
            extra_headers=_NO_STORE,
        )

    if suffix == "/action":
        action = _header(request, "X-Nanobot-Browser-Action")
        payload: dict[str, Any] = {"token": token, "action": action}
        if action == "click":
            payload["x"] = _header(request, "X-Nanobot-Browser-X")
            payload["y"] = _header(request, "X-Nanobot-Browser-Y")
        elif action == "type":
            encoded = _header(request, "X-Nanobot-Browser-Text")
            try:
                payload["text"] = json.loads(encoded)
            except json.JSONDecodeError:
                return http_error(400, "invalid browser text payload")
        elif action == "key":
            payload["key"] = _header(request, "X-Nanobot-Browser-Key")
        elif action == "scroll":
            payload["delta"] = _header(request, "X-Nanobot-Browser-Delta")
        elif action == "release":
            status, body = await _json_request(
                "POST",
                "/release",
                payload={"token": token},
            )
            return http_json_response(body, status=status, extra_headers=_NO_STORE)
        else:
            return http_error(400, "unsupported browser takeover action")
        status, body = await _json_request("POST", "/action", payload=payload)
        return http_json_response(body, status=status, extra_headers=_NO_STORE)

    return http_error(404, "browser takeover route not found")
