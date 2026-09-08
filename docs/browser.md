# Browser runtime (Linux)

nanobot can optionally use a persistent Chromium sidecar for JavaScript-heavy and interactive websites. The agent controls the browser over Chrome DevTools Protocol (CDP); Chromium itself stays outside the nanobot process and keeps a persistent profile on disk.

The browser capability is disabled by default and does not install a browser on the host.

## What it is designed for

- JavaScript/SPA pages that `web_fetch` cannot handle.
- Clicking, typing, scrolling, tabs, screenshots, and persistent login sessions.
- Automatic reconnect/retry for ordinary browser crashes, CDP disconnects, and timeouts.
- Human handoff for CAPTCHA, Cloudflare challenges, OTP/2FA, login confirmation, account choice, and other user-only decisions.
- A shared browser desktop: the human and the agent operate the same Chromium profile/session.

It does **not** attempt to solve or bypass CAPTCHA or anti-bot challenges. When explicit human verification is detected, browser write operations are locked to the human until the page becomes usable again.

## Docker deployment

The optional overlay starts Chromium, Xvfb, Openbox, x11vnc, and noVNC in a separate container. The gateway image installs only the Playwright client library; it does not download a second Chromium binary.

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.browser.yml \
  up -d nanobot-browser nanobot-gateway
```

Browser profile data is persisted in the Docker volume `nanobot-browser-profile`. Keeping it in a Docker-managed volume avoids host UID/permission problems while preserving cookies, local storage, and login state across container restarts.

The default noVNC listener is published only on server loopback:

```text
http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale
```

From a workstation, the safest quick way to test takeover is an SSH tunnel:

```bash
ssh -L 6080:127.0.0.1:6080 user@your-server
```

Then open the local noVNC URL above. You are controlling the exact Chromium desktop used by the agent.

## Phone / remote takeover

Do not expose the raw CDP port (`9222`) or the control port (`6081`) to the Internet. They stay inside the Docker network in the provided compose overlay.

For phone takeover, put the noVNC endpoint behind the same authenticated HTTPS reverse proxy, VPN, or Tailscale access policy used for your nanobot WebUI. Set the externally reachable, protected URL:

```bash
NANOBOT_BROWSER_TAKEOVER_URL=https://nanobot.example.com/browser/vnc.html?autoconnect=1\&resize=scale
```

When the agent detects a human-required page, it sends this takeover URL back to the channel that started the browser task (for example WeChat). The agent keeps the browser session alive and pauses browser writes while you interact with it.

A future WebUI integration can embed the same protected noVNC URL; the runtime/handoff protocol deliberately does not depend on a particular WebUI layout or browser vendor.

## Runtime configuration

The Docker overlay configures these automatically for the gateway:

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `NANOBOT_BROWSER_ENABLED` | `false` outside overlay | Register browser tools |
| `NANOBOT_BROWSER_CDP_ENDPOINT` | `http://nanobot-browser:9222` | CDP endpoint |
| `NANOBOT_BROWSER_CONTROL_ENDPOINT` | `http://nanobot-browser:6081` | Internal ownership endpoint |
| `NANOBOT_BROWSER_TAKEOVER_URL` | loopback noVNC URL in overlay | Human takeover link sent to the user |
| `NANOBOT_BROWSER_HUMAN_TIMEOUT_SEC` | `600` | Maximum time one tool call waits for takeover |
| `NANOBOT_BROWSER_RECOVERY_ATTEMPTS` | `2` | Automatic recovery attempts for ordinary browser failures |
| `NANOBOT_BROWSER_ALLOW_PRIVATE_NETWORK` | `false` | Allow navigation to private/internal addresses |

Keep `NANOBOT_BROWSER_ALLOW_PRIVATE_NETWORK=false` unless the bot is intentionally meant to browse trusted internal services. This preserves nanobot's normal SSRF protection for browser navigation.

## Agent behavior

The normal policy is:

1. Prefer lightweight search/fetch for simple pages.
2. Use the persistent browser for interactive/JavaScript pages.
3. Recover ordinary browser failures automatically with bounded retries.
4. Reuse the persistent browser profile instead of creating a fresh identity for every task.
5. If the page clearly requires a human, switch ownership to `human`, notify the originating channel, and wait.
6. Once the challenge/login step is gone, switch ownership back to `agent` and continue the same task.

Useful browser tools include `browser_open`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_scroll`, `browser_wait`, `browser_screenshot`, `browser_tabs`, `browser_back`, `browser_handoff`, `browser_status`, and `browser_close`.

`browser_handoff` is also available when the agent knows a user decision is required even if automatic challenge detection did not trigger, such as choosing an account or approving a sensitive login prompt.
