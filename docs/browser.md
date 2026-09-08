# Browser runtime (Linux)

nanobot can optionally use a persistent Chromium sidecar for JavaScript-heavy and interactive websites. The agent controls Chromium over Chrome DevTools Protocol (CDP); Chromium itself stays outside the nanobot process and keeps a persistent profile on disk.

The browser capability is disabled by default and does not install a browser on the Linux host.

## Intended deployment

A typical remote deployment can keep the same public surface nanobot already uses:

```text
WeChat
  |
  | normal conversation / takeover notification
  v
nanobot gateway on Linux
  |\
  | \-- browser sidecar (private Docker network)
  |       |- Chromium + persistent profile
  |       |- CDP :9222 (private)
  |       `- takeover control :6081 (private)
  |
  `-- WebUI
        ^
        |
Cloudflare / authenticated reverse proxy
        ^
        |
phone or desktop browser
```

Neither CDP nor the browser takeover control service needs a host port. Cloudflare continues to publish only the existing nanobot WebUI.

## What it is designed for

- JavaScript/SPA pages that `web_fetch` cannot handle.
- Clicking, typing, scrolling, tabs, screenshots, and persistent login sessions.
- Automatic reconnect/retry for ordinary browser crashes, CDP disconnects, and timeouts.
- Human handoff for CAPTCHA, Cloudflare challenges, OTP/2FA, login confirmation, account choice, and other user-only decisions.
- A shared browser session: the human takeover panel and the agent operate the same Chromium desktop/profile.

It does **not** attempt to solve or bypass CAPTCHA or anti-bot challenges. When explicit human verification is detected, browser write operations are locked to the human until the page becomes usable again.

## Docker deployment

The optional overlay starts Chromium and a virtual X11 desktop in a separate container. The gateway image installs only the Playwright client library; it does not download a second Chromium binary.

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.browser.yml \
  up -d nanobot-browser nanobot-gateway
```

Browser profile data is persisted in the Docker volume `nanobot-browser-profile`. This preserves cookies, local storage, and login state across sidecar restarts without host UID/permission problems.

The overlay does **not** publish ports `9222` or `6081` to the host. They are reachable only from services on the Compose network.

## WebUI takeover behind Cloudflare

The existing WebUI gateway exposes an authenticated same-origin takeover API. The Browser settings page contains the takeover panel.

For a WebUI published at:

```text
https://bot.example.com
```

configure the link sent back to WeChat as:

```bash
NANOBOT_BROWSER_TAKEOVER_URL='https://bot.example.com/#/settings?section=browser&takeover=1'
```

No additional Cloudflare origin or browser port is required. Keep your current Cloudflare/WebUI authentication policy; the takeover API accepts the same WebUI API token or the same configured trusted-proxy assertion.

When the agent reaches a human-required page:

1. Browser ownership switches from `agent` to `human`.
2. The originating chat (for example WeChat) receives a takeover notification and the configured WebUI URL.
3. Open the link on a phone or desktop.
4. `Settings -> Browser -> Browser takeover` shows the current Chromium desktop.
5. Tap/click the screenshot to click the browser, scroll with the controls, or focus a field and send text/Enter/Tab/Esc.
6. Click **Done — return to AI** when the verification/login step is complete.
7. The agent re-reads the same browser page and continues the original task.

The handoff uses a fresh random token for each takeover. The token is available only through the authenticated WebUI and is invalidated when ownership returns to the agent. Text typed in the takeover panel is sent to the private sidecar and is not added to the AI conversation transcript.

## Runtime configuration

The Docker overlay configures these automatically for the gateway:

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `NANOBOT_BROWSER_ENABLED` | `false` outside overlay | Register browser tools and takeover API |
| `NANOBOT_BROWSER_CDP_ENDPOINT` | `http://nanobot-browser:9222` | Private CDP endpoint |
| `NANOBOT_BROWSER_CONTROL_ENDPOINT` | `http://nanobot-browser:6081` | Private ownership/input endpoint |
| `NANOBOT_BROWSER_TAKEOVER_URL` | empty | Public WebUI takeover link sent to the originating chat |
| `NANOBOT_BROWSER_HUMAN_TIMEOUT_SEC` | `600` | Maximum time one tool call waits for takeover |
| `NANOBOT_BROWSER_RECOVERY_ATTEMPTS` | `2` | Automatic recovery attempts for ordinary browser failures |
| `NANOBOT_BROWSER_ALLOW_PRIVATE_NETWORK` | `false` | Allow browser navigation to private/internal addresses |
| `NANOBOT_BROWSER_SCREEN` | `1440x960x24` | Virtual browser desktop geometry |

Keep `NANOBOT_BROWSER_ALLOW_PRIVATE_NETWORK=false` unless the bot is intentionally meant to browse trusted internal services. This preserves nanobot's normal SSRF protection for browser navigation.

## Agent behavior

The normal policy is:

1. Prefer lightweight search/fetch for simple pages.
2. Use the persistent browser for interactive/JavaScript pages.
3. Recover ordinary browser failures automatically with bounded retries.
4. Reuse the persistent browser profile instead of creating a fresh identity for every task.
5. If the page clearly requires a human, switch ownership to `human`, notify the originating chat, and wait.
6. During human ownership, agent browser write tools are blocked.
7. Once the challenge/login step is complete, return ownership to `agent`, re-read the page, and continue.

Useful browser tools include `browser_open`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_scroll`, `browser_wait`, `browser_screenshot`, `browser_tabs`, `browser_back`, `browser_handoff`, `browser_status`, and `browser_close`.

`browser_handoff` is also available when the agent knows a user decision is required even if automatic challenge detection did not trigger, such as choosing an account or approving a sensitive login prompt.
