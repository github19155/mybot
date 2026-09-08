#!/usr/bin/env python3
"""Tiny sidecar ownership endpoint for nanobot browser human handoff.

This endpoint is intentionally not a browser automation API. It only records
whether the persistent desktop is currently owned by the agent or the human.
Keep port 6081 private to the Docker network.
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "owner": "agent",
    "reason": None,
    "url": None,
    "title": None,
}


def _snapshot() -> dict[str, Any]:
    with _LOCK:
        return dict(_STATE)


def _update(**values: Any) -> dict[str, Any]:
    with _LOCK:
        _STATE.update(values)
        return dict(_STATE)


class Handler(BaseHTTPRequestHandler):
    server_version = "nanobot-browser-control/1"

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

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 64 * 1024:
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
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/handoff":
            body = self._body()
            self._json(
                200,
                _update(
                    owner="human",
                    reason=body.get("reason"),
                    url=body.get("url"),
                    title=body.get("title"),
                ),
            )
            return
        if self.path == "/release":
            self._json(200, _update(owner="agent", reason=None))
            return
        self._json(404, {"error": "not_found"})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=6081)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
