#!/usr/bin/env python3
"""Private browser-desktop control endpoint for human takeover.

Port 6081 stays inside the Docker network.  The public WebUI proxies only the
small authenticated surface it needs: state, screenshot, pointer/keyboard input,
and release.  A fresh handoff token is minted whenever ownership moves to the
human so stale browser pages cannot keep controlling the desktop.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "owner": "agent",
    "reason": None,
    "url": None,
    "title": None,
    "handoff_token": None,
    "width": 1440,
    "height": 960,
}


def _snapshot(*, include_token: bool = True) -> dict[str, Any]:
    with _LOCK:
        state = dict(_STATE)
    if not include_token:
        state.pop("handoff_token", None)
    return state


def _update(**values: Any) -> dict[str, Any]:
    with _LOCK:
        _STATE.update(values)
        return dict(_STATE)


def _authorized(token: object) -> bool:
    if not isinstance(token, str) or not token:
        return False
    with _LOCK:
        expected = _STATE.get("handoff_token")
        return _STATE.get("owner") == "human" and isinstance(expected, str) and secrets.compare_digest(token, expected)


def _run_xdotool(*args: str, text: str | None = None) -> None:
    env = dict(os.environ)
    command = ["xdotool", *args]
    if text is not None:
        command.extend(["--", text])
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        env=env,
    )


def _screenshot_png() -> bytes:
    result = subprocess.run(
        ["import", "-window", "root", "png:-"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=5,
        env=dict(os.environ),
    )
    return result.stdout


class Handler(BaseHTTPRequestHandler):
    server_version = "nanobot-browser-control/2"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _png(self, status: int, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 256 * 1024:
            return {}
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/health":
            self._json(200, {"status": "ok"})
            return
        if self.path == "/state":
            self._json(200, _snapshot())
            return
        if self.path == "/screenshot":
            token = self.headers.get("X-Nanobot-Browser-Token", "")
            if not _authorized(token):
                self._json(403, {"error": "invalid_handoff_token"})
                return
            try:
                self._png(200, _screenshot_png())
            except (OSError, subprocess.SubprocessError):
                self._json(503, {"error": "screenshot_unavailable"})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/handoff":
            body = self._body()
            token = secrets.token_urlsafe(24)
            self._json(
                200,
                _update(
                    owner="human",
                    reason=body.get("reason"),
                    url=body.get("url"),
                    title=body.get("title"),
                    handoff_token=token,
                ),
            )
            return

        body = self._body()
        token = body.get("token")
        if self.path in {"/action", "/release"} and not _authorized(token):
            self._json(403, {"error": "invalid_handoff_token"})
            return

        if self.path == "/release":
            self._json(
                200,
                _update(owner="agent", reason=None, handoff_token=None),
            )
            return

        if self.path != "/action":
            self._json(404, {"error": "not_found"})
            return

        action = body.get("action")
        try:
            if action == "click":
                x = int(body.get("x"))
                y = int(body.get("y"))
                width = int(_STATE["width"])
                height = int(_STATE["height"])
                if not (0 <= x < width and 0 <= y < height):
                    raise ValueError("coordinates out of bounds")
                _run_xdotool("mousemove", str(x), str(y), "click", "1")
            elif action == "type":
                text = body.get("text")
                if not isinstance(text, str) or len(text) > 4096:
                    raise ValueError("invalid text")
                _run_xdotool("type", "--clearmodifiers", "--delay", "8", text=text)
            elif action == "key":
                key = body.get("key")
                allowed = {
                    "Return": "Return",
                    "Tab": "Tab",
                    "Escape": "Escape",
                    "BackSpace": "BackSpace",
                    "Delete": "Delete",
                    "Up": "Up",
                    "Down": "Down",
                    "Left": "Left",
                    "Right": "Right",
                    "space": "space",
                }
                if not isinstance(key, str) or key not in allowed:
                    raise ValueError("unsupported key")
                _run_xdotool("key", "--clearmodifiers", allowed[key])
            elif action == "scroll":
                delta = int(body.get("delta", 0))
                if delta == 0 or abs(delta) > 20:
                    raise ValueError("invalid scroll delta")
                button = "4" if delta < 0 else "5"
                for _ in range(abs(delta)):
                    _run_xdotool("click", button)
            else:
                raise ValueError("unsupported action")
        except (TypeError, ValueError, OSError, subprocess.SubprocessError):
            self._json(400, {"error": "invalid_action"})
            return
        self._json(200, {"ok": True, "state": _snapshot(include_token=False)})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=6081)
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=960)
    args = parser.parse_args()
    _update(width=args.width, height=args.height)
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
