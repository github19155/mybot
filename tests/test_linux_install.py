from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-linux.sh"


def run_installer(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(INSTALLER), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        env={**os.environ, "LC_ALL": "C"},
    )


def test_help_is_side_effect_free() -> None:
    result = run_installer("--help")
    assert result.returncode == 0
    assert "host-admin" in result.stdout
    assert "container-root" in result.stdout
    assert "--dry-run" in result.stdout


def test_mode_is_required() -> None:
    result = run_installer("--dry-run")
    assert result.returncode != 0
    assert "--mode is required" in result.stdout


def test_host_admin_dry_run_does_not_require_confirmation() -> None:
    result = run_installer("--mode", "host-admin", "--dry-run")
    assert result.returncode == 0, result.stdout
    assert "would require root and --confirm-host-admin" in result.stdout
    assert "would NOT enable, start, or restart" in result.stdout
    assert "no changes made" in result.stdout.lower()


def test_container_root_dry_run_uses_current_repo_source() -> None:
    result = run_installer("--mode", "container-root", "--dry-run")
    assert result.returncode == 0, result.stdout
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert "https://github.com/github19155/mybot.git" in result.stdout
    assert f"Source commit: {commit}" in result.stdout
    assert f"MYBOT_SOURCE_COMMIT={commit}" in result.stdout
    assert "docker-compose.root.yml" in result.stdout


def test_installer_has_no_pypi_fallback() -> None:
    text = INSTALLER.read_text()
    assert "nanobot-ai" not in text
    assert "pip install --upgrade nanobot-ai" not in text
    assert 'CANONICAL_REPO="https://github.com/github19155/mybot.git"' in text


def test_host_admin_requires_explicit_confirmation_before_changes() -> None:
    result = run_installer("--mode", "host-admin")
    assert result.returncode != 0
    assert "requires --confirm-host-admin" in result.stdout


def test_container_root_overlay_keeps_host_isolation_defaults() -> None:
    base = (ROOT / "docker-compose.yml").read_text()
    root = (ROOT / "docker-compose.root.yml").read_text()
    assert "127.0.0.1:8765:8765" in base
    assert 'user: "0:0"' in root
    assert "nanobot-gateway:" in root and "nanobot-cli:" in root
    assert "nanobot-container-root-data:/home/nanobot/.nanobot" in root
    assert "privileged:" not in root
    assert "- SYS_ADMIN" not in root
    assert "docker.sock" not in root
    assert "pid: host" not in root
    assert "network_mode: host" not in root
    assert "security_opt: !reset" not in root


def test_image_records_source_revision_label() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert 'org.opencontainers.image.source="https://github.com/github19155/mybot"' in dockerfile
    assert 'org.opencontainers.image.revision="$MYBOT_SOURCE_COMMIT"' in dockerfile


def test_systemd_unit_uses_supported_gateway_command() -> None:
    unit = (ROOT / "deploy/systemd/mybot-host-admin.service.in").read_text()
    assert "User=root" in unit
    assert "Group=root" in unit
    assert "ExecStart=@PYTHON@ -m nanobot gateway --config @CONFIG@" in unit
    assert "--foreground" not in unit


def test_default_container_mode_still_drops_root() -> None:
    base = (ROOT / "docker-compose.yml").read_text()
    entrypoint = (ROOT / "entrypoint.sh").read_text()
    assert 'user: "0:0"' not in base
    assert "NANOBOT_RUN_AS_ROOT" not in base
    assert "dropping privileges to nanobot via setpriv" in entrypoint
    assert "refusing to run as root" in entrypoint


def test_merged_container_root_compose_when_available() -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker is not available in this environment")
    probe = subprocess.run(
        ["docker", "compose", "version"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("Docker Compose v2 is not available in this environment")

    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.root.yml",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env={**os.environ, "MYBOT_SOURCE_COMMIT": "test-revision"},
    )
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)

    for name in ("nanobot-gateway", "nanobot-cli"):
        service = config["services"][name]
        assert service.get("user") == "0:0"
        assert service.get("privileged") is not True
        assert service.get("network_mode") != "host"
        assert service.get("pid") != "host"
        assert "SYS_ADMIN" not in (service.get("cap_add") or [])
        assert "no-new-privileges:true" in (service.get("security_opt") or [])
        mounts = service.get("volumes") or []
        data_mounts = [m for m in mounts if m.get("target") == "/home/nanobot/.nanobot"]
        assert len(data_mounts) == 1
        assert data_mounts[0].get("type") == "volume"
        assert data_mounts[0].get("source") == "mybot-container-root-data"
        assert all("docker.sock" not in str(m) for m in mounts)

    ports = config["services"]["nanobot-gateway"].get("ports") or []
    published = {str(p.get("published")): p.get("host_ip") for p in ports}
    assert published["18790"] == "127.0.0.1"
    assert published["8765"] == "127.0.0.1"


def test_linux_docs_keep_source_and_mode_data_unambiguous() -> None:
    linux = (ROOT / "docs/linux-install.md").read_text()
    migration = (ROOT / "docs/container-root.md").read_text()
    assert "https://github.com/github19155/mybot.git" in linux
    assert "git clone https://github.com/HKUDS/nanobot.git" not in linux
    assert "different active data locations" in migration
    assert "stop the source-mode gateway" in migration
    assert "back up the complete source-mode `.nanobot` data" in migration
    assert "adjust ownership only on the destination copy" in migration
