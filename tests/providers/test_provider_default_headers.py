from nanobot.config.schema import Config, ProviderConfig
from nanobot.providers.factory import (
    _openai_compat_extra_headers,
    _provider_extra_headers,
    provider_signature,
)
from nanobot.providers.registry import find_by_name


def test_kimi_coding_uses_default_user_agent_header() -> None:
    spec = find_by_name("kimi_coding")

    assert spec is not None
    assert _provider_extra_headers(spec, ProviderConfig()) == {
        "User-Agent": "claude-code/0.1.0",
    }


def test_provider_config_extra_headers_override_defaults() -> None:
    spec = find_by_name("kimi_coding")
    provider = ProviderConfig.model_validate({
        "extraHeaders": {
            "User-Agent": "custom-client/1.0",
            "X-Test": "1",
        },
    })

    assert _provider_extra_headers(spec, provider) == {
        "User-Agent": "custom-client/1.0",
        "X-Test": "1",
    }


def test_openai_compat_uses_neutral_user_agent_by_default() -> None:
    spec = find_by_name("custom")

    assert spec is not None
    assert _openai_compat_extra_headers(spec, ProviderConfig()) == {
        "User-Agent": "nanobot",
    }


def test_openai_compat_allows_user_agent_override() -> None:
    spec = find_by_name("custom")
    provider = ProviderConfig.model_validate({
        "extraHeaders": {
            "User-Agent": "custom-client/1.0",
            "X-Test": "1",
        },
    })

    assert spec is not None
    assert _openai_compat_extra_headers(spec, provider) == {
        "User-Agent": "custom-client/1.0",
        "X-Test": "1",
    }


def test_provider_signature_tracks_default_extra_headers() -> None:
    config = Config.model_validate({
        "providers": {
            "kimiCoding": {
                "apiKey": "sk-kimi-test",
            },
        },
        "models": {
            "main": {
                "displayName": "kimi_coding",
                "provider": "kimi_coding",
                "model": "kimi-for-coding",
                "capabilities": {"text": True},
            }
        },
        "agents": {
            "defaults": {
                "modelId": "main",
            },
        },
    })

    assert {"User-Agent": "claude-code/0.1.0"} in provider_signature(config)
