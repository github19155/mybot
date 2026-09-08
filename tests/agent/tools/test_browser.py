from types import SimpleNamespace

import pytest

from nanobot.agent.tools.browser import (
    BrowserOpenTool,
    BrowserRuntimeConfig,
    _BrowserRuntime,
    _classify_intervention,
)


def _classify(**overrides):
    values = {
        "title": "Example",
        "text": "Hello world",
        "frames": [],
        "turnstile": False,
        "captcha": False,
        "otp": False,
        "password": False,
        "headers": None,
    }
    values.update(overrides)
    return _classify_intervention(**values)


def test_cloudflare_header_requires_human():
    assert _classify(headers={"CF-Mitigated": "challenge"}) == (
        "human_required",
        "cloudflare_challenge",
    )


def test_turnstile_frame_requires_human():
    assert _classify(frames=["https://challenges.cloudflare.com/turnstile/v0/"]) == (
        "human_required",
        "cloudflare_challenge",
    )


def test_captcha_requires_human():
    assert _classify(captcha=True) == ("human_required", "captcha")


def test_otp_requires_human():
    assert _classify(otp=True) == ("human_required", "verification_code")


def test_password_page_is_login_required_not_captcha():
    assert _classify(password=True) == ("login_required", "login")


def test_normal_page_is_ok():
    assert _classify() == ("ok", None)


def test_browser_runtime_config_defaults_disabled(monkeypatch):
    monkeypatch.delenv("NANOBOT_BROWSER_ENABLED", raising=False)
    config = BrowserRuntimeConfig.from_env()
    assert config.enabled is False
    assert config.cdp_endpoint == "http://nanobot-browser:9222"


def test_browser_runtime_config_reads_environment(monkeypatch):
    monkeypatch.setenv("NANOBOT_BROWSER_ENABLED", "true")
    monkeypatch.setenv("NANOBOT_BROWSER_CDP_ENDPOINT", "http://browser:9222")
    monkeypatch.setenv("NANOBOT_BROWSER_RECOVERY_ATTEMPTS", "4")
    config = BrowserRuntimeConfig.from_env()
    assert config.enabled is True
    assert config.cdp_endpoint == "http://browser:9222"
    assert config.recovery_attempts == 4


def test_browser_tool_enablement_is_environment_owned(monkeypatch):
    ctx = SimpleNamespace()
    monkeypatch.setenv("NANOBOT_BROWSER_ENABLED", "0")
    assert BrowserOpenTool.enabled(ctx) is False
    monkeypatch.setenv("NANOBOT_BROWSER_ENABLED", "1")
    assert BrowserOpenTool.enabled(ctx) is True


def test_human_owner_blocks_agent_writes(tmp_path):
    runtime = _BrowserRuntime(
        BrowserRuntimeConfig(enabled=True),
        workspace=str(tmp_path),
        bus=None,
    )
    runtime.state.owner = "human"
    with pytest.raises(RuntimeError, match="human operator"):
        runtime.assert_agent_owner()


def test_agent_owner_allows_agent_writes(tmp_path):
    runtime = _BrowserRuntime(
        BrowserRuntimeConfig(enabled=True),
        workspace=str(tmp_path),
        bus=None,
    )
    runtime.state.owner = "agent"
    runtime.assert_agent_owner()
