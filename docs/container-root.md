# Container root mode

nanobot normally starts the gateway as root only long enough to fix mounted-data ownership, then drops privileges to the `nanobot` user. For deployments where the agent is intentionally allowed to administer its own container (for example installing temporary packages with `apt`), an explicit root-mode overlay is available.

## Enable it

Without the browser sidecar:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.root.yml \
  up -d --build nanobot-gateway
```

With the browser sidecar:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.browser.yml \
  -f docker-compose.root.yml \
  up -d --build nanobot-browser nanobot-gateway
```

Verify the gateway identity:

```bash
docker exec nanobot-gateway id
```

Expected output begins with:

```text
uid=0(root) gid=0(root)
```

Commands invoked by the agent's `exec` tool inherit the gateway process identity, so they also run as container root.

## What changes

The root overlay only changes `nanobot-gateway`:

- sets `user: "0:0"`;
- sets `NANOBOT_RUN_AS_ROOT=true`, so `entrypoint.sh` does not drop privileges;
- clears the base Compose `cap_drop: ALL` restriction;
- clears the base `no-new-privileges` security option;
- restores Docker's normal default capability set for a root container.

It deliberately does **not**:

- set `privileged: true`;
- mount `/` from the host;
- mount `/var/run/docker.sock`;
- grant host PID/network namespaces;
- give the container direct root access to the Linux host.

This means the agent can administer the gateway container (including ordinary `apt`/`dpkg` package installation), but it remains inside Docker's normal container isolation unless the deployment separately adds host-level access.

## Persistence

Packages installed interactively with `apt` modify the running container filesystem. They survive a normal container restart, but they are lost when the image/container is recreated (for example after `docker compose up --build` creates a replacement container).

For dependencies that should survive rebuilds, add them to the Dockerfile or another build layer after testing them interactively.

## Disable root mode

Restart without the root overlay:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.browser.yml \
  up -d --build nanobot-browser nanobot-gateway
```

The normal entrypoint will again drop privileges to the `nanobot` user.
