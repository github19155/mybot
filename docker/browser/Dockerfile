FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    HOME=/home/browser

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    curl \
    fonts-liberation \
    novnc \
    openbox \
    python3 \
    tini \
    websockify \
    x11vnc \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 -s /bin/bash browser && \
    mkdir -p /data/profile /opt/nanobot-browser && \
    chown -R browser:browser /data /opt/nanobot-browser

COPY docker/browser/start.sh /opt/nanobot-browser/start.sh
COPY docker/browser/control.py /opt/nanobot-browser/control.py
RUN chmod +x /opt/nanobot-browser/start.sh

USER browser
WORKDIR /home/browser

VOLUME ["/data/profile"]
EXPOSE 9222 6080 6081

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/nanobot-browser/start.sh"]
