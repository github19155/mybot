import pytest

from nanobot.config.schema import Config


def test_provider_api_type_accepts_exact_values_only() -> None:
    config = Config.model_validate(
        {
            "providers": {
                "openai": {
                    "apiKey": "sk-test",
                    "apiType": "responses",
                }
            }
        }
    )
    assert config.providers.openai.api_type == "responses"

    with pytest.raises(ValueError):
        Config.model_validate(
            {
                "providers": {
                    "openai": {
                        "apiKey": "sk-test",
                        "apiType": "response",
                    }
                }
            }
        )


def test_provider_api_type_is_openai_only() -> None:
    with pytest.raises(ValueError, match="only supported"):
        Config.model_validate(
            {
                "providers": {
                    "custom": {
                        "apiBase": "https://example.test/v1",
                        "apiType": "responses",
                    }
                }
            }
        )

    with pytest.raises(ValueError, match="only supported"):
        Config.model_validate(
            {
                "providers": {
                    "my-company-api": {
                        "apiBase": "https://example.test/v1",
                        "apiType": "responses",
                    }
                }
            }
        )


@pytest.mark.parametrize("provider_name", ["openai-codex", "github-copilot", "lm-studio"])
def test_dynamic_custom_provider_rejects_builtin_provider_aliases(provider_name: str) -> None:
    with pytest.raises(ValueError, match="conflicts with built-in provider"):
        Config.model_validate(
            {
                "providers": {
                    provider_name: {
                        "apiBase": "https://example.test/v1",
                    }
                }
            }
        )
