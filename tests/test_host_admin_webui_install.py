from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-linux.sh"
CANONICAL_REPO = "https://github.com/github19155/mybot.git"


def _clean_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "repo"
    subprocess.run(
        ["git", "clone", "--quiet", str(ROOT), str(checkout)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "remote", "set-url", "origin", CANONICAL_REPO],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return checkout


def _run_dry_run(checkout: Path, *, skip_webui: bool = False) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "LC_ALL": "C", "PYTHON": sys.executable}
    if skip_webui:
        env["NANOBOT_SKIP_WEBUI_BUILD"] = "1"
    else:
        env.pop("NANOBOT_SKIP_WEBUI_BUILD", None)
    return subprocess.run(
        ["sh", str(checkout / "scripts" / "install-linux.sh"), "--mode", "host-admin", "--dry-run"],
        cwd=checkout,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def test_host_admin_webui_build_is_default(tmp_path: Path) -> None:
    checkout = _clean_checkout(tmp_path)
    result = _run_dry_run(checkout)

    assert result.returncode == 0, result.stdout
    assert "would build the bundled WebUI" in result.stdout
    assert "Node.js 20+/npm" in result.stdout
    assert "install editable source after the WebUI build step" in result.stdout


def test_host_admin_webui_build_can_be_explicitly_skipped(tmp_path: Path) -> None:
    checkout = _clean_checkout(tmp_path)
    result = _run_dry_run(checkout, skip_webui=True)

    assert result.returncode == 0, result.stdout
    assert "would skip the WebUI build via NANOBOT_SKIP_WEBUI_BUILD=1" in result.stdout


def test_host_admin_build_verifies_dist_and_uses_lockfiles() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")

    assert 'webui_index="$source_checkout/nanobot/web/dist/index.html"' in installer
    assert 'npm ci && npm run build' in installer
    assert 'bun install --frozen-lockfile && bun run build' in installer
    assert 'WebUI build completed without producing $webui_index' in installer


def test_generated_webui_bundle_is_git_ignored_but_packaged() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "nanobot/web/dist/" in gitignore
    assert '"nanobot/web/dist/**/*"' in pyproject
