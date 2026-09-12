from __future__ import annotations

import os
from pathlib import Path
import subprocess


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
