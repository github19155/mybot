#!/bin/sh
set -eu

CANONICAL_REPO="https://github.com/github19155/mybot.git"
SERVICE_NAME="mybot-host-admin"
DEFAULT_PREFIX="/opt/mybot"
DEFAULT_DATA_DIR="/var/lib/mybot-host-admin"

mode=""
dry_run=0
confirm_host_admin=0
prefix="$DEFAULT_PREFIX"
data_dir="$DEFAULT_DATA_DIR"

info() { printf '%s\n' "$*"; }
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
Usage: scripts/install-linux.sh --mode host-admin|container-root [options]

Linux deployment entrypoint for this repository. A mode is always required.

Modes:
  host-admin       Install a root-run systemd gateway from this repository.
                   Requires --confirm-host-admin for a real install.
  container-root   Build the repository image for root inside the container only.

Options:
  --mode MODE                 Required deployment mode.
  --dry-run                   Print the planned actions; make no changes.
  --confirm-host-admin        Explicitly authorize host-admin root installation.
  --install-prefix PATH       Host-admin code/venv prefix (default: /opt/mybot).
  --data-dir PATH             Host-admin HOME/data root (default: /var/lib/mybot-host-admin).
  -h, --help                  Show this help.

The installer never falls back to PyPI or another repository. Run it from a clean
checkout that has a remote pointing to https://github.com/github19155/mybot.git.
USAGE
}

is_canonical_remote() {
  case "$1" in
    https://github.com/github19155/mybot|https://github.com/github19155/mybot.git|\
    git@github.com:github19155/mybot|git@github.com:github19155/mybot.git|\
    ssh://git@github.com/github19155/mybot|ssh://git@github.com/github19155/mybot.git|\
    git://github.com/github19155/mybot|git://github.com/github19155/mybot.git)
      return 0
      ;;
  esac
  return 1
}

find_python() {
  if [ -n "${PYTHON:-}" ]; then
    command -v "$PYTHON" >/dev/null 2>&1 || fail "PYTHON=$PYTHON was not found"
    printf '%s\n' "$PYTHON"
    return
  fi
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -I - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
      then
        printf '%s\n' "$candidate"
        return
      fi
    fi
  done
  fail "Python 3.11 or newer was not found"
}

node_is_supported() {
  command -v node >/dev/null 2>&1 || return 1
  node_major=$(node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || printf '0')
  case "$node_major" in
    ''|*[!0-9]*) return 1 ;;
  esac
  [ "$node_major" -ge 20 ]
}

ensure_webui_build_runner() {
  if command -v bun >/dev/null 2>&1; then
    return
  fi
  if command -v npm >/dev/null 2>&1 && node_is_supported; then
    return
  fi

  command -v apt-get >/dev/null 2>&1 || \
    fail "WebUI build requires bun or Node.js 20+ with npm; install one or set NANOBOT_SKIP_WEBUI_BUILD=1"

  info "Installing Node.js/npm for the host-admin WebUI build..."
  DEBIAN_FRONTEND=noninteractive apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nodejs npm

  command -v npm >/dev/null 2>&1 || fail "npm is still unavailable after apt installation"
  node_is_supported || \
    fail "Node.js 20 or newer is required to build the WebUI; install a newer Node.js or set NANOBOT_SKIP_WEBUI_BUILD=1"
}

build_host_admin_webui() {
  if [ "${NANOBOT_SKIP_WEBUI_BUILD:-}" = "1" ]; then
    info "Skipping host-admin WebUI build via NANOBOT_SKIP_WEBUI_BUILD=1."
    return
  fi

  webui_dir="$source_checkout/webui"
  webui_index="$source_checkout/nanobot/web/dist/index.html"
  [ -f "$webui_dir/package.json" ] || fail "missing WebUI source: $webui_dir/package.json"

  ensure_webui_build_runner
  if command -v bun >/dev/null 2>&1; then
    info "Building host-admin WebUI with bun..."
    if [ -f "$webui_dir/bun.lock" ]; then
      (cd "$webui_dir" && bun install --frozen-lockfile && bun run build)
    else
      (cd "$webui_dir" && bun install && bun run build)
    fi
  else
    info "Building host-admin WebUI with npm..."
    if [ -f "$webui_dir/package-lock.json" ]; then
      (cd "$webui_dir" && npm ci && npm run build)
    else
      (cd "$webui_dir" && npm install && npm run build)
    fi
  fi

  [ -f "$webui_index" ] || fail "WebUI build completed without producing $webui_index"
  info "Host-admin WebUI ready: $webui_index"
}

validate_managed_path() {
  label="$1"
  value="$2"
  kind="$3"
  case "$value" in
    /*) ;;
    *) fail "$label must be an absolute path" ;;
  esac
  case "$value" in
    *[!A-Za-z0-9_./-]*) fail "$label contains unsupported characters: $value" ;;
  esac

  "$python_bin" -I - "$label" "$value" "$kind" <<'PY'
import os
import sys

label, raw, kind = sys.argv[1:]
parts = raw.split("/")
if any(part in {".", ".."} for part in parts):
    raise SystemExit(f"Error: {label} must not contain '.' or '..' path components: {raw}")

normalized = os.path.normpath(raw)
resolved = os.path.realpath(normalized)
if resolved != normalized:
    raise SystemExit(f"Error: {label} must not resolve through symbolic links: {raw} -> {resolved}")

protected = {
    "/",
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/home",
    "/lib",
    "/lib32",
    "/lib64",
    "/media",
    "/mnt",
    "/opt",
    "/proc",
    "/root",
    "/run",
    "/sbin",
    "/srv",
    "/sys",
    "/tmp",
    "/usr",
    "/usr/local",
    "/var",
    "/var/cache",
    "/var/lib",
    "/var/local",
    "/var/log",
    "/var/tmp",
}
if resolved in protected:
    raise SystemExit(
        f"Error: {label} must be a dedicated subdirectory, not shared system directory {resolved}"
    )

if os.path.lexists(resolved):
    if not os.path.isdir(resolved):
        raise SystemExit(f"Error: {label} exists but is not a directory: {resolved}")
    entries = list(os.scandir(resolved))
    marker = os.path.join(resolved, "INSTALL-METADATA" if kind == "prefix" else ".nanobot")
    if entries and not os.path.exists(marker):
        raise SystemExit(
            f"Error: {label} already contains unrelated data and is not a recognized "
            f"mybot {kind} directory: {resolved}"
        )

    managed_children = (
        ("source", "venv", "INSTALL-METADATA") if kind == "prefix" else (".nanobot",)
    )
    for child in managed_children:
        child_path = os.path.join(resolved, child)
        if os.path.islink(child_path):
            raise SystemExit(
                f"Error: {label} contains managed path through a symbolic link: {child_path}"
            )

print(resolved)
PY
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode)
      [ "$#" -ge 2 ] || fail "--mode requires a value"
      mode="$2"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    --confirm-host-admin)
      confirm_host_admin=1
      shift
      ;;
    --install-prefix)
      [ "$#" -ge 2 ] || fail "--install-prefix requires a value"
      prefix="$2"
      shift 2
      ;;
    --data-dir)
      [ "$#" -ge 2 ] || fail "--data-dir requires a value"
      data_dir="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
done

case "$mode" in
  host-admin|container-root) ;;
  "") fail "--mode is required (host-admin or container-root)" ;;
  *) fail "unsupported mode: $mode" ;;
esac

[ "$(uname -s)" = "Linux" ] || fail "this installer supports Linux only"
command -v git >/dev/null 2>&1 || fail "git is required"

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
source_root=$(git -C "$script_dir/.." rev-parse --show-toplevel 2>/dev/null) || \
  fail "run this installer from a git checkout of github19155/mybot"

canonical_remote=0
for remote in $(git -C "$source_root" remote); do
  url=$(git -C "$source_root" remote get-url "$remote" 2>/dev/null || true)
  if is_canonical_remote "$url"; then
    canonical_remote=1
    break
  fi
done
[ "$canonical_remote" = "1" ] || \
  fail "checkout has no remote pointing to github19155/mybot; refusing an ambiguous source"

commit=$(git -C "$source_root" rev-parse HEAD)
if [ -n "$(git -C "$source_root" status --porcelain --untracked-files=normal)" ]; then
  fail "source checkout is dirty; commit or stash changes so the installed version is traceable"
fi

info "Repository: $CANONICAL_REPO"
info "Source checkout: $source_root"
info "Source commit: $commit"
info "Mode: $mode"

if [ "$mode" = "container-root" ]; then
  compose_base="$source_root/docker-compose.yml"
  compose_root="$source_root/docker-compose.root.yml"
  [ -f "$compose_base" ] || fail "missing $compose_base"
  [ -f "$compose_root" ] || fail "missing $compose_root"

  if [ "$dry_run" = "1" ]; then
    info "Dry run: would validate the merged Compose configuration."
    info "  MYBOT_SOURCE_COMMIT=$commit docker compose -f $compose_base -f $compose_root config"
    info "Dry run: would build nanobot-gateway and nanobot-cli from this checkout."
    info "  MYBOT_SOURCE_COMMIT=$commit docker compose -f $compose_base -f $compose_root build nanobot-gateway nanobot-cli"
    info "Dry run: would not create/start containers or alter existing configuration/data."
    info "Dry run: no changes made."
    exit 0
  fi

  command -v docker >/dev/null 2>&1 || fail "docker is required for container-root mode"
  docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
  MYBOT_SOURCE_COMMIT="$commit" docker compose -f "$compose_base" -f "$compose_root" config >/dev/null
  MYBOT_SOURCE_COMMIT="$commit" docker compose -f "$compose_base" -f "$compose_root" build nanobot-gateway nanobot-cli
  info "Built container-root image from commit $commit."
  info "No container was started. Follow docs/linux-install.md to initialize data and start the gateway."
  exit 0
fi

python_bin=$(find_python)
"$python_bin" -I - <<'PY' >/dev/null 2>&1 || fail "nanobot requires Python 3.11 or newer"
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
prefix=$(validate_managed_path "--install-prefix" "$prefix" "prefix")
data_dir=$(validate_managed_path "--data-dir" "$data_dir" "data")

source_checkout="$prefix/source"
venv_dir="$prefix/venv"
venv_python="$venv_dir/bin/python"
config_dir="$data_dir/.nanobot"
config_path="$config_dir/config.json"
unit_template="$source_checkout/deploy/systemd/mybot-host-admin.service.in"
unit_path="/etc/systemd/system/$SERVICE_NAME.service"
metadata_path="$prefix/INSTALL-METADATA"

cat <<EOF
WARNING: host-admin gives the Agent a root process on the Linux host.
Its file and exec tools can affect the entire server when application policy permits.
Existing nanobot authentication, approvals, tool deny-rules, and channel allow-lists still apply.
This installer does NOT configure passwordless sudo, root SSH login, firewall rules, or public binds.
EOF

if [ "$dry_run" = "1" ]; then
  info "Dry run: would require root and --confirm-host-admin for a real install."
  info "Dry run: would clone/fetch only $CANONICAL_REPO and detach at $commit into $source_checkout."
  info "Dry run: would create/reuse venv $venv_dir with $python_bin."
  if [ "${NANOBOT_SKIP_WEBUI_BUILD:-}" = "1" ]; then
    info "Dry run: would skip the WebUI build via NANOBOT_SKIP_WEBUI_BUILD=1."
  else
    info "Dry run: would build the bundled WebUI into $source_checkout/nanobot/web/dist."
    info "Dry run: would use bun when available, otherwise Node.js 20+/npm; apt systems may install nodejs/npm if needed."
  fi
  info "Dry run: would install editable source after the WebUI build step."
  info "Dry run: would preserve any existing $config_path; no credentials would be generated."
  info "Dry run: would install $unit_path with User=root, HOME=$data_dir, WorkingDirectory=$source_checkout."
  info "Dry run: would run systemctl daemon-reload only; it would NOT enable, start, or restart the service."
  info "Dry run: no changes made."
  exit 0
fi

[ "$confirm_host_admin" = "1" ] || \
  fail "host-admin requires --confirm-host-admin to acknowledge host root authority"
[ "$(id -u)" = "0" ] || fail "host-admin installation must be run as root after explicitly selecting --mode host-admin"
command -v systemctl >/dev/null 2>&1 || fail "systemctl is required for host-admin mode"
if systemctl is-active --quiet "$SERVICE_NAME.service" 2>/dev/null; then
  fail "$SERVICE_NAME.service is running; stop it explicitly before changing the deployed source"
fi

mkdir -p "$prefix" "$config_dir"
chmod 700 "$data_dir" "$config_dir" || fail "could not secure $data_dir"

if [ -e "$source_checkout" ]; then
  [ ! -L "$source_checkout" ] || fail "$source_checkout must not be a symbolic link"
  git -C "$source_checkout" rev-parse --is-inside-work-tree >/dev/null 2>&1 || \
    fail "$source_checkout exists but is not a git checkout"
  deployed_origin=$(git -C "$source_checkout" remote get-url origin 2>/dev/null || true)
  is_canonical_remote "$deployed_origin" || \
    fail "$source_checkout origin is not github19155/mybot; refusing to replace it"
  [ -z "$(git -C "$source_checkout" status --porcelain --untracked-files=normal)" ] || \
    fail "$source_checkout has local changes; refusing to overwrite them"
  git -C "$source_checkout" fetch origin "$commit"
else
  git clone --no-checkout "$CANONICAL_REPO" "$source_checkout"
  git -C "$source_checkout" fetch origin "$commit"
fi

git -C "$source_checkout" checkout --detach "$commit"
[ "$(git -C "$source_checkout" rev-parse HEAD)" = "$commit" ] || fail "deployed checkout did not resolve to $commit"

if [ -e "$venv_dir" ]; then
  [ ! -L "$venv_dir" ] || fail "$venv_dir must not be a symbolic link"
fi
if [ ! -x "$venv_python" ]; then
  "$python_bin" -m venv "$venv_dir"
fi
"$venv_python" -I - <<'PY' >/dev/null 2>&1 || fail "managed venv uses Python older than 3.11"
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY

build_host_admin_webui
NANOBOT_SKIP_WEBUI_BUILD=1 "$venv_python" -I -m pip install --upgrade --editable "$source_checkout"

"$venv_python" -I - "$source_checkout" <<'PY'
from pathlib import Path
import nanobot
import sys
source = Path(sys.argv[1]).resolve()
module = Path(nanobot.__file__).resolve()
try:
    module.relative_to(source)
except ValueError as exc:
    raise SystemExit(f"nanobot imports from {module}, expected source under {source}") from exc
print(f"Verified nanobot import: {module}")
PY

[ -f "$unit_template" ] || fail "missing systemd template: $unit_template"
tmp_unit=$(mktemp)
trap 'rm -f "$tmp_unit"' EXIT HUP INT TERM
sed \
  -e "s|@PYTHON@|$venv_python|g" \
  -e "s|@WORKDIR@|$source_checkout|g" \
  -e "s|@HOME@|$data_dir|g" \
  -e "s|@CONFIG@|$config_path|g" \
  "$unit_template" > "$tmp_unit"
install -m 0644 "$tmp_unit" "$unit_path"

cat > "$metadata_path" <<EOF
repository=$CANONICAL_REPO
commit=$commit
source=$source_checkout
python=$venv_python
data_dir=$data_dir
config=$config_path
service=$SERVICE_NAME.service
EOF
chmod 0644 "$metadata_path"
systemctl daemon-reload

info "Installed host-admin from $CANONICAL_REPO at commit $commit."
if [ -f "$config_path" ]; then
  info "Preserved existing config: $config_path"
else
  info "Config does not exist yet: $config_path"
  info "Initialize it explicitly before starting the service; see docs/linux-install.md."
fi
info "Service installed but not enabled or started: $SERVICE_NAME.service"
