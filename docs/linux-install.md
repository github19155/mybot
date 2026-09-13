# Linux installation: host-admin or container-root

This repository has two explicit Linux server modes. Both install from a clean checkout of
`https://github.com/github19155/mybot.git`; neither mode falls back to the `nanobot-ai`
release on PyPI or to another repository.

- **host-admin** runs the gateway as host `root` under systemd. Its existing file and exec
  tools can operate outside the workspace when the existing application configuration allows it.
  This gives the Agent server-administration authority and mistakes can affect the entire machine.
- **container-root** runs the gateway and its CLI as UID 0 inside Docker. It does not grant
  host root access by default. The supplied Compose files do not use `privileged`, host PID/network,
  the Docker socket, a host-root mount, `SYS_ADMIN`, or an unconfined seccomp profile.

The package and CLI are still named `nanobot` / `nanobot-ai` for compatibility. That name does
not determine source provenance; the commands below verify the repository and commit actually used.

## Scope and prerequisites

The installation entrypoint is Linux-only and requires a clean git checkout with a remote pointing
to `github19155/mybot`. Python **3.11+** is required for host-admin. Debian 13 is the primary Linux
target for this path. The host-admin installer uses `python -m venv`, `git`, and systemd and now builds
the bundled WebUI by default before installing the editable Python package. It prefers Bun when
available; otherwise it requires Node.js **20+** with npm. On apt-based systems it may install
`nodejs` and `npm` automatically when neither supported runner is present, and it fails if the final
Node.js version is still too old. A successful default build must produce
`nanobot/web/dist/index.html`.

Set `NANOBOT_SKIP_WEBUI_BUILD=1` only when you intentionally want to skip the frontend build. In that
explicit opt-out mode the Python/gateway installation can still proceed, but a usable bundled WebUI is
not guaranteed.

Container-root requires Docker Engine and Docker Compose v2. The Dockerfile builds the WebUI in a
separate Node stage, so Node is not required on the host. Browser sidecars and extra channels remain
optional.

Clone the exact repository first:

```bash
git clone https://github.com/github19155/mybot.git
cd mybot
git status --short
git rev-parse HEAD
```

`git status --short` must be empty. Inspect the two modes without changing anything:

```bash
./scripts/install-linux.sh --help
./scripts/install-linux.sh --mode host-admin --dry-run
./scripts/install-linux.sh --mode container-root --dry-run
```

A mode is always required. Running the script through `sudo` does **not** choose host-admin.

## A. host-admin: Agent manages the Linux host

### Install

Read the warning printed by the dry run. A real install requires both an explicit mode and an
explicit high-privilege acknowledgement:

```bash
sudo ./scripts/install-linux.sh \
  --mode host-admin \
  --confirm-host-admin
```

Defaults:

| Item | Path / value |
|---|---|
| managed source checkout | `/opt/mybot/source` |
| virtual environment | `/opt/mybot/venv` |
| service | `/etc/systemd/system/mybot-host-admin.service` |
| service user | `root` |
| service HOME | `/var/lib/mybot-host-admin` |
| config | `/var/lib/mybot-host-admin/.nanobot/config.json` |
| default workspace | `/var/lib/mybot-host-admin/.nanobot/workspace` |
| install provenance | `/opt/mybot/INSTALL-METADATA` |
| logs | systemd journal for `mybot-host-admin.service` |

Custom `--install-prefix` and `--data-dir` values must be dedicated absolute directories. The
installer validates them before any host write: `.`/`..` path components, paths that resolve through
symbolic links, shared system directories such as `/` or `/etc`, and existing non-mybot directories
are rejected. A successful existing installation remains reusable, and the default paths above are
accepted.

The installer clones/fetches only this repository, checks out the commit from the clean source
checkout in detached mode, creates a dedicated venv, and verifies in Python isolated mode (`-I`) that
`import nanobot` resolves under `/opt/mybot/source`. This prevents the administrator checkout, current
working directory, or `PYTHONPATH` from masquerading as the managed installation. Dependency
installation failure is fatal; there is no package/repository fallback.

Unless `NANOBOT_SKIP_WEBUI_BUILD=1` is set explicitly, the installer then builds the WebUI from the
managed source checkout, uses lockfile-aware installs (`bun install --frozen-lockfile` or `npm ci`
when the matching lockfile is present), and refuses to continue if
`/opt/mybot/source/nanobot/web/dist/index.html` was not produced. The following editable Python install
sets `NANOBOT_SKIP_WEBUI_BUILD=1` only to avoid rebuilding the same frontend a second time; it does not
mean the normal host-admin path skips WebUI generation.

Re-running the installer preserves an existing config and never generates replacement credentials. It
updates the managed code/venv/unit but deliberately does not enable, start, or restart the service. If
the service is already running, the installer refuses to replace the source until you stop it
explicitly.

The installer does **not** configure passwordless sudo, change SSH root-login policy, disable a
firewall, or expose ports publicly.

### Initialize configuration

For a first install only, use the managed interpreter in isolated mode so the administrator's current
checkout or `PYTHONPATH` cannot supply a different `nanobot` package:

```bash
sudo env HOME=/var/lib/mybot-host-admin \
  /opt/mybot/venv/bin/python -I -m nanobot onboard \
  --config /var/lib/mybot-host-admin/.nanobot/config.json \
  --workspace /var/lib/mybot-host-admin/.nanobot/workspace \
  --wizard
```

If the config already exists, do not overwrite it. To add newly introduced fields while preserving
existing values, use the repository's non-destructive refresh path:

```bash
sudo env HOME=/var/lib/mybot-host-admin \
  /opt/mybot/venv/bin/python -I -m nanobot onboard \
  --config /var/lib/mybot-host-admin/.nanobot/config.json \
  --workspace /var/lib/mybot-host-admin/.nanobot/workspace \
  --refresh
```

Keep provider keys, bot tokens, and other credentials in that root-owned config or in a protected
service environment source that you manage separately. Do not paste real secrets into documentation,
issues, or shell history.

### Confirm host-management scope

Host-admin deliberately uses existing application controls rather than a new privilege bypass.
For whole-host file/command reach, the relevant existing settings are:

```json
{
  "tools": {
    "restrictToWorkspace": false,
    "exec": {
      "sandbox": ""
    }
  }
}
```

`restrictToWorkspace` already defaults to `false`, and an empty exec sandbox is the normal bare-metal
setting. With those settings, the existing file tools are not confined to the workspace and the exec
tool can choose working directories elsewhere on the host. The systemd process itself is `root`, so
those tools run with host-root OS credentials. Existing Exec deny patterns, application permissions,
approval requirements, channel `allowFrom`/pairing, and WebSocket authentication are still enforced.
Root runtime identity is not authorization for arbitrary remote senders.

Do not enable `bwrap` for this mode if your intent is whole-host administration: the sandbox is a
separate process-isolation boundary and intentionally changes filesystem reach.

### Start and check

After configuration is complete:

```bash
sudo systemctl enable --now mybot-host-admin.service
sudo systemctl status mybot-host-admin.service
sudo journalctl -u mybot-host-admin.service -f
```

Confirm the service definition and the actual Python/source it uses. The import check uses `-I` for
the same reason as installation: it must prove the managed venv relationship, not whichever checkout
happens to be the shell's current directory.

```bash
sudo systemctl show mybot-host-admin.service -p User -p Group -p ExecStart -p WorkingDirectory -p Environment
cat /opt/mybot/INSTALL-METADATA
git -C /opt/mybot/source rev-parse HEAD
/opt/mybot/venv/bin/python -I -c 'import nanobot,sys; print(sys.executable); print(nanobot.__file__)'
test -f /opt/mybot/source/nanobot/web/dist/index.html && echo "WebUI bundle present"
```

A normal host-admin install already builds and verifies that bundled WebUI asset before the editable
Python install. If you explicitly installed with `NANOBOT_SKIP_WEBUI_BUILD=1`, the asset check above
may fail by design. Asset presence still does not prove that WebSocket configuration/authentication or
browser connectivity works, and a running systemd process does not prove that a provider/model call
works; those are separate checks and a real model request may incur API cost.

Gateway defaults are loopback-only. The WebUI/WebSocket surface remains on `127.0.0.1:8765` by
default. Do not change listeners to public interfaces merely to make remote access convenient. Prefer
an authenticated reverse proxy, VPN, or an SSH tunnel, for example:

```bash
ssh -L 18790:127.0.0.1:18790 -L 8765:127.0.0.1:8765 your-server
```

### Update and rollback

Before changing code, record the running provenance and back up data:

```bash
cat /opt/mybot/INSTALL-METADATA
sudo systemctl stop mybot-host-admin.service
sudo cp -a /var/lib/mybot-host-admin /var/lib/mybot-host-admin.backup
```

In a separate administrator checkout of this repository, fetch and select the commit you want, make
sure it is clean, then rerun the installer:

```bash
git fetch origin
git checkout <wanted-commit>
git status --short
sudo ./scripts/install-linux.sh --mode host-admin --confirm-host-admin
sudo systemctl start mybot-host-admin.service
```

Rollback uses the same procedure with a previously recorded commit. Code rollback and data rollback
are separate operations: an older application version is **not** guaranteed to understand data written
by a newer version. Restore the data backup only as an explicit, separately reviewed step.

To remove the service while retaining user data:

```bash
sudo systemctl disable --now mybot-host-admin.service
sudo rm -f /etc/systemd/system/mybot-host-admin.service
sudo systemctl daemon-reload
sudo rm -rf /opt/mybot
```

The uninstall sequence intentionally does not delete `/var/lib/mybot-host-admin`.

## B. container-root: root inside the container only

### Build from this repository

```bash
./scripts/install-linux.sh --mode container-root
```

The installer validates the merged Compose configuration and builds from the current clean checkout.
It passes the commit SHA into the Docker build and records it in OCI image labels. It does not start or
restart an existing deployment.

The root overlay reuses `NANOBOT_RUN_AS_ROOT=true` from `entrypoint.sh`. Both `nanobot-gateway` and
`nanobot-cli` use UID/GID `0:0`, so initialization and later CLI commands write the same data with the
same identity. Only the dedicated Docker volume `mybot-container-root-data` is persisted at
`/home/nanobot/.nanobot`; it is separate from host-admin data and from the base Compose `~/.nanobot`
bind mount.

The root overlay restores Docker's normal default capability set instead of `cap_drop: ALL`, but it does
not add `SYS_ADMIN`, use `privileged`, mount `/` or `/var/run/docker.sock`, share host PID/network, pass
host devices, or disable Docker's default seccomp/AppArmor confinement. The base
`no-new-privileges:true` remains enabled. Container root is powerful inside that container, but ordinary
Docker isolation is not an absolute security boundary and container root is not host root.

### Initialize, configure, and start

Use the same overlay for initialization and later CLI operations:

```bash
docker compose -f docker-compose.yml -f docker-compose.root.yml \
  run --rm nanobot-cli onboard --wizard
```

For host port forwarding, the listeners inside the container must bind to `0.0.0.0`, while the Compose
publish rules bind the host side to `127.0.0.1`. For WebUI/WebSocket access, keep authentication
configured. A minimal shape is:

```json
{
  "gateway": {"host": "0.0.0.0"},
  "channels": {
    "websocket": {
      "host": "0.0.0.0",
      "port": 8765,
      "tokenIssueSecret": "set-your-own-secret"
    }
  },
  "tools": {
    "restrictToWorkspace": false,
    "exec": {"sandbox": ""}
  }
}
```

Do not reuse the example secret. Store a real secret only in your private config. The host publish rules
remain `127.0.0.1:18790:18790` and `127.0.0.1:8765:8765`; use an authenticated proxy/VPN/SSH tunnel for
remote access instead of publishing the management surface to every interface.

Start without rebuilding (the installer already built the traceable image):

```bash
docker compose -f docker-compose.yml -f docker-compose.root.yml \
  up -d nanobot-gateway
```

Check identity, mounts, published ports, and source labels:

```bash
docker exec nanobot-gateway id
docker exec nanobot-gateway sh -lc 'id -u; command -v python; python -c "import nanobot; print(nanobot.__file__)"'
docker inspect nanobot-gateway --format '{{json .Mounts}}'
docker port nanobot-gateway
docker inspect nanobot-gateway --format '{{index .Config.Labels "org.opencontainers.image.source"}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
docker compose -f docker-compose.yml -f docker-compose.root.yml config
```

Expected gateway/tool OS identity is UID 0 **inside the container**. The merged Compose config should
show only the named data volume for the root-mode nanobot services, loopback host port publishing, no
`privileged`, no host PID/network, and no Docker/containerd management socket.

Interactive `apt` installs affect the running container filesystem. They survive a simple container
restart but are lost when the container is recreated. Put stable system dependencies in the Dockerfile;
put reusable agent data/caches under `/home/nanobot/.nanobot`; keep one-off diagnostics in the
replaceable container filesystem.

### Update and rollback

Before update, record the image label and back up the `mybot-container-root-data` volume with your normal
Docker volume backup procedure. Then select a clean repository commit and rebuild:

```bash
git fetch origin
git checkout <wanted-commit>
git status --short
./scripts/install-linux.sh --mode container-root
docker compose -f docker-compose.yml -f docker-compose.root.yml up -d nanobot-gateway
```

Rollback selects the earlier recorded commit, rebuilds, and recreates the gateway in the same way.
Do not equate image rollback with data rollback; restore the volume backup only when compatibility has
been reviewed.

Stopping/removing containers does not remove the named data volume unless you explicitly ask Docker to
delete volumes:

```bash
docker compose -f docker-compose.yml -f docker-compose.root.yml down
```

Do not add `-v` if your intent is to preserve user data.

## What each check proves

Keep these results separate:

1. installer `--dry-run` succeeds -> argument/source planning only;
2. Python import plus the default host-admin WebUI asset check, or a container image build -> installation/source and bundled frontend wiring;
3. systemd/Compose gateway stays running -> local process startup;
4. WebUI assets and authenticated WebSocket work -> UI transport is available;
5. a provider/model answers -> real model integration works and may cost money;
6. a disposable host test proves root tool reach -> host-admin operational authority is verified.

Do not claim later stages from an earlier check.
