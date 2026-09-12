from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-linux.sh"
CANONICAL_REPO = "https://github.com/github19155/mybot.git"


def prepare_clean_checkout(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
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


def run_installer(
    *args: str,
    root: Path = ROOT,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process_env = {**os.environ, "LC_ALL": "C"}
    if env:
        process_env.update(env)
    return subprocess.run(
        ["sh", str(root / "scripts" / "install-linux.sh"), *args],
        cwd=cwd or root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        env=process_env,
    )


def write_executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def prepare_side_effect_guard(tmp_path: Path) -> tuple[dict[str, str], Path]:
    stub_dir = tmp_path / "side-effect-stubs"
    stub_dir.mkdir()
    log = tmp_path / "side-effects.log"
    real_git = shutil.which("git")
    assert real_git

    write_executable(
        stub_dir / "git",
        f'''#!/bin/sh
case "${{1:-}}" in
  clone|fetch|checkout)
    printf 'git %s\\n' "$*" >> "$MYBOT_SIDE_EFFECT_LOG"
    exit 91
    ;;
esac
if [ "${{1:-}}" = "-C" ]; then
  case "${{3:-}}" in
    clone|fetch|checkout)
      printf 'git %s\\n' "$*" >> "$MYBOT_SIDE_EFFECT_LOG"
      exit 91
      ;;
  esac
fi
exec "{real_git}" "$@"
''',
    )
    for command in ("mkdir", "chmod", "install", "systemctl"):
        write_executable(
            stub_dir / command,
            f'''#!/bin/sh
printf '{command} %s\\n' "$*" >> "$MYBOT_SIDE_EFFECT_LOG"
exit 91
''',
        )

    return (
        {
            "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
            "PYTHON": sys.executable,
            "MYBOT_SIDE_EFFECT_LOG": str(log),
        },
        log,
    )


def prepare_host_admin_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, Path, dict[str, str]]:
    checkout = prepare_clean_checkout(tmp_path / "caller")
    prefix = (tmp_path / "managed").resolve()
    data_dir = (tmp_path / "data").resolve()
    source_checkout = prefix / "source"
    venv_dir = prefix / "venv"
    config_path = data_dir / ".nanobot" / "config.json"
    unit_capture = tmp_path / "installed.service"

    prefix.mkdir()
    subprocess.run(
        ["git", "clone", "--quiet", str(checkout), str(source_checkout)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(source_checkout), "remote", "set-url", "origin", CANONICAL_REPO],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    (prefix / "INSTALL-METADATA").write_text(f"repository={CANONICAL_REPO}\n")

    venv.EnvBuilder(with_pip=False).create(venv_dir)
    venv_python = venv_dir / "bin" / "python"
    site_packages = Path(
        subprocess.check_output(
            [str(venv_python), "-I", "-c", "import site; print(site.getsitepackages()[0])"],
            text=True,
        ).strip()
    )
    (site_packages / "mybot-managed-source.pth").write_text(f"{source_checkout}\n")
    fake_pip = site_packages / "pip"
    fake_pip.mkdir()
    (fake_pip / "__init__.py").write_text("")
    (fake_pip / "__main__.py").write_text("raise SystemExit(0)\n")

    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"existingCredential":"keep-me"}\n')

    stub_dir = tmp_path / "host-admin-stubs"
    stub_dir.mkdir()
    real_git = shutil.which("git")
    real_id = shutil.which("id")
    real_cp = shutil.which("cp")
    assert real_git and real_id and real_cp

    write_executable(
        stub_dir / "git",
        f'''#!/bin/sh
if [ "$1" = "-C" ] && [ "${{3:-}}" = "fetch" ]; then
  exit 0
fi
exec "{real_git}" "$@"
''',
    )
    write_executable(
        stub_dir / "id",
        f'''#!/bin/sh
if [ "${{1:-}}" = "-u" ]; then
  echo 0
  exit 0
fi
exec "{real_id}" "$@"
''',
    )
    write_executable(
        stub_dir / "systemctl",
        '''#!/bin/sh
case "${1:-}" in
  is-active) exit 3 ;;
  daemon-reload) exit 0 ;;
  *) exit 0 ;;
esac
''',
    )
    write_executable(
        stub_dir / "install",
        f'''#!/bin/sh
exec "{real_cp}" "$3" "$MYBOT_TEST_UNIT_DEST"
''',
    )

    env = {
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        "PYTHON": sys.executable,
        "PYTHONPATH": str(tmp_path / "unexpected-pythonpath"),
        "MYBOT_TEST_UNIT_DEST": str(unit_capture),
    }
    return checkout, prefix, data_dir, source_checkout, venv_python, env


def site_packages_for(venv_python: Path) -> Path:
    return Path(
        subprocess.check_output(
            [str(venv_python), "-I", "-c", "import site; print(site.getsitepackages()[0])"],
            text=True,
        ).strip()
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


def test_host_admin_dry_run_does_not_require_confirmation(tmp_path: Path) -> None:
    checkout = prepare_clean_checkout(tmp_path)
    result = run_installer("--mode", "host-admin", "--dry-run", root=checkout)
    assert result.returncode == 0, result.stdout
    assert "would require root and --confirm-host-admin" in result.stdout
    assert "would NOT enable, start, or restart" in result.stdout
    assert "no changes made" in result.stdout.lower()


def test_container_root_dry_run_uses_current_repo_source(tmp_path: Path) -> None:
    checkout = prepare_clean_checkout(tmp_path)
    result = run_installer("--mode", "container-root", "--dry-run", root=checkout)
    assert result.returncode == 0, result.stdout
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
    ).strip()
    assert CANONICAL_REPO in result.stdout
    assert f"Source commit: {commit}" in result.stdout
    assert f"MYBOT_SOURCE_COMMIT={commit}" in result.stdout
    assert "docker-compose.root.yml" in result.stdout


def test_installer_has_no_pypi_fallback() -> None:
    text = INSTALLER.read_text()
    assert "nanobot-ai" not in text
    assert "pip install --upgrade nanobot-ai" not in text
    assert f'CANONICAL_REPO="{CANONICAL_REPO}"' in text


def test_host_admin_requires_explicit_confirmation_before_changes(tmp_path: Path) -> None:
    checkout = prepare_clean_checkout(tmp_path)
    result = run_installer("--mode", "host-admin", root=checkout)
    assert result.returncode != 0
    assert "requires --confirm-host-admin" in result.stdout


def test_installer_rejects_dirty_source(tmp_path: Path) -> None:
    checkout = prepare_clean_checkout(tmp_path)
    (checkout / "untracked-test-file").write_text("dirty\n")
    result = run_installer("--mode", "host-admin", "--dry-run", root=checkout)
    assert result.returncode != 0
    assert "source checkout is dirty" in result.stdout


@pytest.mark.parametrize(
    ("flag", "dangerous", "message"),
    [
        ("--install-prefix", "/", "dedicated subdirectory"),
        ("--data-dir", "/etc", "dedicated subdirectory"),
        ("--data-dir", "/tmp/..", "must not contain"),
    ],
)
def test_dangerous_host_admin_paths_fail_before_side_effects(
    tmp_path: Path, flag: str, dangerous: str, message: str
) -> None:
    checkout = prepare_clean_checkout(tmp_path / "checkout")
    safe_prefix = str((tmp_path / "safe-prefix").resolve())
    safe_data = str((tmp_path / "safe-data").resolve())
    args = [
        "--mode",
        "host-admin",
        "--dry-run",
        "--install-prefix",
        safe_prefix,
        "--data-dir",
        safe_data,
    ]
    args[args.index(flag) + 1] = dangerous
    env, side_effect_log = prepare_side_effect_guard(tmp_path)

    result = run_installer(*args, root=checkout, env=env)

    assert result.returncode != 0
    assert message in result.stdout
    assert not side_effect_log.exists() or side_effect_log.read_text() == ""


def test_host_admin_rejects_symlinked_managed_path_before_side_effects(tmp_path: Path) -> None:
    checkout = prepare_clean_checkout(tmp_path / "checkout")
    target = (tmp_path / "real-data").resolve()
    target.mkdir()
    data_link = tmp_path / "data-link"
    data_link.symlink_to(target, target_is_directory=True)
    env, side_effect_log = prepare_side_effect_guard(tmp_path)

    result = run_installer(
        "--mode",
        "host-admin",
        "--dry-run",
        "--install-prefix",
        str((tmp_path / "safe-prefix").resolve()),
        "--data-dir",
        str(data_link),
        root=checkout,
        env=env,
    )

    assert result.returncode != 0
    assert "must not resolve through symbolic links" in result.stdout
    assert not side_effect_log.exists() or side_effect_log.read_text() == ""


@pytest.mark.parametrize("flag", ["--install-prefix", "--data-dir"])
def test_host_admin_rejects_unrelated_existing_directory(tmp_path: Path, flag: str) -> None:
    checkout = prepare_clean_checkout(tmp_path / "checkout")
    unrelated = (tmp_path / f"unrelated-{flag[2:]}").resolve()
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("do not take ownership\n")
    safe_prefix = str((tmp_path / "safe-prefix").resolve())
    safe_data = str((tmp_path / "safe-data").resolve())
    args = [
        "--mode",
        "host-admin",
        "--dry-run",
        "--install-prefix",
        safe_prefix,
        "--data-dir",
        safe_data,
    ]
    args[args.index(flag) + 1] = str(unrelated)
    env, side_effect_log = prepare_side_effect_guard(tmp_path)

    result = run_installer(*args, root=checkout, env=env)

    assert result.returncode != 0
    assert "contains unrelated data" in result.stdout
    assert (unrelated / "keep.txt").read_text() == "do not take ownership\n"
    assert not side_effect_log.exists() or side_effect_log.read_text() == ""


def test_host_admin_dry_run_accepts_dedicated_custom_paths(tmp_path: Path) -> None:
    checkout = prepare_clean_checkout(tmp_path / "checkout")
    prefix = (tmp_path / "dedicated-prefix").resolve()
    data_dir = (tmp_path / "dedicated-data").resolve()

    result = run_installer(
        "--mode",
        "host-admin",
        "--dry-run",
        "--install-prefix",
        str(prefix),
        "--data-dir",
        str(data_dir),
        root=checkout,
    )

    assert result.returncode == 0, result.stdout
    assert f"into {prefix / 'source'}" in result.stdout
    assert f"HOME={data_dir}" in result.stdout
    assert not prefix.exists()
    assert not data_dir.exists()


def test_host_admin_install_uses_managed_source_and_preserves_config(tmp_path: Path) -> None:
    checkout, prefix, data_dir, source_checkout, _, env = prepare_host_admin_fixture(tmp_path)
    config_path = data_dir / ".nanobot" / "config.json"
    original_config = config_path.read_text()

    for _ in range(2):
        result = run_installer(
            "--mode",
            "host-admin",
            "--confirm-host-admin",
            "--install-prefix",
            str(prefix),
            "--data-dir",
            str(data_dir),
            root=checkout,
            cwd=checkout,
            env=env,
        )
        assert result.returncode == 0, result.stdout
        assert f"Verified nanobot import: {source_checkout / 'nanobot' / '__init__.py'}" in result.stdout
        assert config_path.read_text() == original_config

    metadata = (prefix / "INSTALL-METADATA").read_text()
    assert f"repository={CANONICAL_REPO}" in metadata
    assert f"source={source_checkout}" in metadata
    assert (tmp_path / "installed.service").exists()


def test_source_verification_rejects_wrong_venv_even_from_expected_source_cwd(
    tmp_path: Path,
) -> None:
    checkout, prefix, data_dir, source_checkout, venv_python, env = prepare_host_admin_fixture(
        tmp_path
    )
    wrong_source = (tmp_path / "wrong-source").resolve()
    (wrong_source / "nanobot").mkdir(parents=True)
    (wrong_source / "nanobot" / "__init__.py").write_text("ORIGIN = 'wrong'\n")
    pth = site_packages_for(venv_python) / "mybot-managed-source.pth"
    pth.write_text(f"{wrong_source}\n")
    env["PYTHONPATH"] = str(source_checkout)

    result = run_installer(
        "--mode",
        "host-admin",
        "--confirm-host-admin",
        "--install-prefix",
        str(prefix),
        "--data-dir",
        str(data_dir),
        root=checkout,
        cwd=source_checkout,
        env=env,
    )

    assert result.returncode != 0
    assert f"nanobot imports from {wrong_source}" in result.stdout
    assert f"expected source under {source_checkout}" in result.stdout
    assert not (tmp_path / "installed.service").exists()


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
    assert "ExecStart=@PYTHON@ -I -m nanobot gateway --config @CONFIG@" in unit
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
            "--profile",
            "cli",
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
        assert data_mounts[0].get("source") == "nanobot-container-root-data"
        assert all("docker.sock" not in str(m) for m in mounts)

    volume = config["volumes"]["nanobot-container-root-data"]
    assert volume.get("name") == "mybot-container-root-data"

    ports = config["services"]["nanobot-gateway"].get("ports") or []
    published = {str(p.get("published")): p.get("host_ip") for p in ports}
    assert published["18790"] == "127.0.0.1"
    assert published["8765"] == "127.0.0.1"


def test_linux_docs_keep_source_and_mode_data_unambiguous() -> None:
    linux = (ROOT / "docs/linux-install.md").read_text()
    migration = (ROOT / "docs/container-root.md").read_text()
    assert CANONICAL_REPO in linux
    assert "git clone https://github.com/HKUDS/nanobot.git" not in linux
    assert "/opt/mybot/venv/bin/python -I -m nanobot onboard" in linux
    assert "/opt/mybot/venv/bin/python -I -c" in linux
    assert "different active data locations" in migration
    assert "stop the source-mode gateway" in migration
    assert "back up the complete source-mode `.nanobot` data" in migration
    assert "adjust ownership only on the destination copy" in migration


def test_root_readme_routes_fork_installation_to_fork_sources() -> None:
    readme = (ROOT / "README.md").read_text()
    assert "[Linux host-admin / container-root](./docs/linux-install.md)" in readme
    assert "git clone https://github.com/github19155/mybot.git" in readme
    assert "cd mybot" in readme
    assert "Upstream HKUDS/nanobot release" in readme
    assert "do not include this fork's mybot changes" in readme
