# Browser Runtime Troubleshooting

Read `docs/browser.md` first. This guide assumes the standard Linux browser sidecar deployment.

## Expected topology

```text
AI browser tools
  -> gateway Playwright client
  -> http://nanobot-browser:9222
  -> Chromium in browser sidecar

WebUI takeover
  -> gateway authenticated proxy
  -> http://nanobot-browser:6081
  -> browser control service
  -> same Chromium desktop
```

The takeover UI and AI must operate the same Chromium session/profile.

## Diagnose before redesigning

When browser access fails, check the existing chain in this order:

1. `nanobot-browser` container is running.
2. CDP responds on `9222` from the gateway.
3. control service responds on `6081` from the gateway.
4. Docker DNS resolves `nanobot-browser` to the browser container, not the gateway itself.
5. takeover URL points to the public WebUI route, not to `9222` or `6081`.

Useful checks:

```bash
docker compose -f docker-compose.yml -f docker-compose.browser.yml ps

docker compose -f docker-compose.yml -f docker-compose.browser.yml \
  exec nanobot-gateway getent hosts nanobot-browser

docker compose -f docker-compose.yml -f docker-compose.browser.yml \
  exec nanobot-gateway python3 -c \
  'import urllib.request; print(urllib.request.urlopen("http://nanobot-browser:6081/health", timeout=3).read().decode())'
```

Expected DNS result is a Docker-network address such as `172.x.x.x`, not `127.0.0.1`.

## Important failure pattern: stale `/etc/hosts`

A manual entry such as:

```text
127.0.0.1 nanobot-browser
```

inside `nanobot-gateway` overrides Docker DNS and causes both CDP/control requests to hit the gateway itself. Recreate the gateway to restore normal Docker-managed hostname resolution, then verify with `getent hosts nanobot-browser`.

Do not treat a manual hosts rewrite as the normal fix for sidecar connectivity.

## `browser_control_unavailable`

The WebUI returns this when the gateway cannot reach the configured control endpoint. Check `NANOBOT_BROWSER_CONTROL_ENDPOINT` and test `http://nanobot-browser:6081/health` from inside the gateway.

Note that the browser container healthcheck may validate CDP without proving the control service is reachable, so test `6081` explicitly.

## Avoid accidental parallel browser stacks

The standard architecture does not require the gateway to install its own Chromium, create a second CDP port, or add a localhost proxy. Those can be valid experiments, but they should be treated as an intentional alternate design, not the first repair step.

Before introducing such a workaround, explain why the existing sidecar topology cannot satisfy the task and record the new design in project docs.
