from unittest.mock import MagicMock

import pytest

from nanobot.agent.model_runtime import ModelRuntimeResolver
from nanobot.model_domain import ModelConfig
from nanobot.providers.base import GenerationSettings
from nanobot.providers.factory import ProviderSnapshot
from nanobot.utils.llm_runtime import runtime_from_provider_snapshot


def _model(upstream: str) -> ModelConfig:
    return ModelConfig(display_name=upstream, provider="cpa", model=upstream)


def _snapshot(model_id: str, model: str, version: str) -> ProviderSnapshot:
    provider = MagicMock()
    provider.generation = GenerationSettings(max_tokens=1024, temperature=0.1)
    return ProviderSnapshot(
        model_id=model_id,
        provider=provider,
        model=model,
        context_window_tokens=32768,
        signature=(model_id, model, version),
        generation=GenerationSettings(max_tokens=4096, temperature=0.7),
        system_prompt_prefix=f"prompt:{model_id}:{version}",
    )


def test_invalidation_refreshes_selected_model_id_on_next_admission() -> None:
    initial = _snapshot("main", "upstream-v1", "v1")
    refreshed = _snapshot("main", "upstream-v2", "v2")
    loader = MagicMock(return_value=refreshed)
    resolver = ModelRuntimeResolver(
        runtime_from_provider_snapshot(initial),
        models={"main": _model("upstream-v2")},
        provider_snapshot_loader=loader,
    )

    resolver.invalidate()
    assert loader.call_count == 0
    runtime = resolver.admit()

    assert runtime.model_id == "main"
    assert runtime.model == "upstream-v2"
    assert runtime.system_prompt_prefix == "prompt:main:v2"
    loader.assert_called_once_with(model_id="main")


def test_refresh_failure_surfaces_configuration_error() -> None:
    initial = _snapshot("main", "upstream", "v1")

    def fail(**_kwargs) -> ProviderSnapshot:
        raise ValueError("invalid config")

    resolver = ModelRuntimeResolver(
        runtime_from_provider_snapshot(initial),
        models={"main": _model("upstream")},
        provider_snapshot_loader=fail,
    )
    resolver.invalidate()
    with pytest.raises(ValueError, match="invalid config"):
        resolver.admit()


def test_refresh_does_not_rewrite_already_admitted_runtime() -> None:
    initial = _snapshot("main", "upstream-v1", "v1")
    captured = runtime_from_provider_snapshot(initial)
    resolver = ModelRuntimeResolver(
        captured,
        models={"main": _model("upstream-v2")},
        provider_snapshot_loader=lambda **_kwargs: _snapshot("main", "upstream-v2", "v2"),
    )
    resolver.invalidate()
    fresh = resolver.admit()
    assert fresh is not captured
    assert captured.model == "upstream-v1"
    assert fresh.model == "upstream-v2"
