# Container root mode

The primary Linux `container-root` path is documented in [Linux installation: host-admin or container-root](./linux-install.md#b-container-root-root-inside-the-container-only).

This mode is an explicit opt-in for cases where the Agent must administer the gateway container itself (for example, installing a temporary package with `apt`). It is **not** host-admin mode: container UID 0 does not become host UID 0 authority unless an operator separately grants dangerous host integration.

## Build the traceable image

From a clean checkout of `https://github.com/github19155/mybot.git`:

```bash
./scripts/install-linux.sh --mode container-root --dry-run
./scripts/install-linux.sh --mode container-root
```

The installer validates the merged Compose configuration and builds `nanobot-gateway` and `nanobot-cli` from the current checkout. It passes the current commit into OCI image labels and never falls back to the PyPI `nanobot-ai` package or another repository. It does not start or restart containers.

## Initialize and run with the same identity

Use both Compose files for initialization, CLI operations, and the gateway:

```bash
docker compose -f docker-compose.yml -f docker-compose.root.yml \
  run --rm nanobot-cli onboard --wizard

docker compose -f docker-compose.yml -f docker-compose.root.yml \
  up -d nanobot-gateway
```

The root overlay applies `user: "0:0"` plus `NANOBOT_RUN_AS_ROOT=true` to the gateway and CLI only. Both use the dedicated `mybot-container-root-data` volume at `/home/nanobot/.nanobot`, so initialization and runtime do not alternate between root and the normal UID 1000 path. The browser sidecar is not changed to root.

Verify the running identity and final Compose policy rather than trusting one YAML fragment:

```bash
docker exec nanobot-gateway id
docker compose -f docker-compose.yml -f docker-compose.root.yml config
docker inspect nanobot-gateway --format '{{json .Mounts}}'
docker port nanobot-gateway
docker inspect nanobot-gateway --format '{{index .Config.Labels "org.opencontainers.image.source"}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
```

## Isolation boundary

The supplied root overlay intentionally does not configure:

- `privileged: true`;
- a mount of the host root filesystem;
- the Docker/containerd management socket;
- host PID or host network namespaces;
- host devices or SSH administration keys;
- `SYS_ADMIN` or an unconfined seccomp/AppArmor profile.

It restores Docker's normal default capability set for a root container because the base deployment drops almost all capabilities for the ordinary non-root mode. The base `no-new-privileges:true` option remains enabled, and Docker's normal seccomp/AppArmor defaults remain in force.

The default host-side port mappings for the gateway health endpoint and WebUI/WebSocket are loopback-only. Listeners inside the container still need to use `0.0.0.0` when they must be reached through Docker port forwarding; keep WebSocket authentication configured and use an explicit authenticated remote-access layer if remote administration is needed.

Ordinary Docker isolation reduces host exposure but is not an absolute security boundary. Do not describe container root as host root, and do not add host mounts or management sockets merely to work around an application configuration problem.

## Persistence

The named volume is durable across ordinary container recreation unless it is explicitly deleted. Interactive changes to the container filesystem, including `apt` installs, are different: they can survive a simple restart but disappear when the container is recreated from the image.

Use these rules:

- stable system packages needed for normal operation -> add them to the Dockerfile/build layer;
- config, sessions, memory, agent workspace, and reusable agent-owned caches -> keep them under `/home/nanobot/.nanobot`;
- one-off diagnostic installs -> the replaceable container filesystem is acceptable;
- standard browser binaries/profile -> use the optional browser sidecar rather than installing another browser in the gateway.

Stop the deployment without deleting the named data volume:

```bash
docker compose -f docker-compose.yml -f docker-compose.root.yml down
```

Do not add `-v` when you intend to retain data. See the Linux installation guide for update, rollback, provenance checks, and the distinction between image rollback and data restoration.

## Migrating between host-admin and container-root

The two modes intentionally use different active data locations. Do not make host-admin and container-root write the same live `.nanobot` directory, and do not solve migration by bind-mounting the host-admin data directory into the root container.

For either direction:

1. stop the source-mode gateway so the backup is consistent;
2. record the source commit and back up the complete source-mode `.nanobot` data before copying anything;
3. initialize the destination mode separately and confirm its runtime identity and destination path/volume;
4. copy only as an explicit migration step, then inspect ownership with `stat` on the host and `id`/`stat` inside the container as applicable;
5. adjust ownership only on the destination copy to match the destination runtime identity; rootless Docker or user-namespace remapping can make container UID 0 map to a different host UID;
6. start only the destination mode and verify sessions/config before retiring the source copy.

A source-code rollback and a data restore are still separate decisions. Do not assume that data written by a newer version is compatible with an older commit.
