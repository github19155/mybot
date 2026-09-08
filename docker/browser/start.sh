#!/bin/sh
set -eu

export DISPLAY="${DISPLAY:-:99}"
PROFILE_DIR="${NANOBOT_BROWSER_PROFILE_DIR:-/data/profile}"
NOVNC_PORT="${NANOBOT_BROWSER_NOVNC_PORT:-6080}"
CONTROL_PORT="${NANOBOT_BROWSER_CONTROL_PORT:-6081}"
CDP_PORT="${NANOBOT_BROWSER_CDP_PORT:-9222}"
SCREEN="${NANOBOT_BROWSER_SCREEN:-1440x960x24}"

mkdir -p "$PROFILE_DIR"

Xvfb "$DISPLAY" -screen 0 "$SCREEN" -ac -nolisten tcp &
XVFB_PID=$!

cleanup() {
    for pid in "${CHROME_PID:-}" "${VNC_PID:-}" "${NOVNC_PID:-}" "${CONTROL_PID:-}" "$XVFB_PID"; do
        if [ -n "$pid" ]; then
            kill "$pid" 2>/dev/null || true
        fi
    done
}
trap cleanup EXIT INT TERM

# Give Xvfb a moment to create the display socket.
i=0
while [ ! -S "/tmp/.X11-unix/X${DISPLAY#:}" ]; do
    i=$((i + 1))
    if [ "$i" -gt 50 ]; then
        echo "Xvfb did not become ready" >&2
        exit 1
    fi
    sleep 0.1
done

openbox >/tmp/openbox.log 2>&1 &

chromium \
    --no-sandbox \
    --disable-dev-shm-usage \
    --disable-gpu \
    --no-first-run \
    --no-default-browser-check \
    --remote-debugging-address=0.0.0.0 \
    --remote-debugging-port="$CDP_PORT" \
    --user-data-dir="$PROFILE_DIR" \
    --window-size=1440,960 \
    about:blank >/tmp/chromium.log 2>&1 &
CHROME_PID=$!

x11vnc \
    -display "$DISPLAY" \
    -forever \
    -shared \
    -nopw \
    -rfbport 5900 \
    -localhost >/tmp/x11vnc.log 2>&1 &
VNC_PID=$!

websockify \
    --web=/usr/share/novnc \
    "$NOVNC_PORT" \
    localhost:5900 >/tmp/novnc.log 2>&1 &
NOVNC_PID=$!

python3 /opt/nanobot-browser/control.py --port "$CONTROL_PORT" &
CONTROL_PID=$!

# Fail the sidecar if Chromium dies. Docker restart policy will recreate it and
# the persistent profile volume preserves cookies/login state.
wait "$CHROME_PID"
