"""ModelManagement tests cover the canonical control plane, not runtime construction."""

from __future__ import annotations

import json

import pytest

from nanobot.agent.model_management import ModelManagement
from nanobot.agent.tools.model_config import ModelConfigTool
from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config, ProviderConfig, SubagentRoleConfig
from nanobot.model_domain import ModelConfig


def _config() -> Config:
    config = Config()
    config.providers.anthropic.api_key = "private-provider-key"
    config.providers.model_extra["cpa"] = ProviderConfig(
        display_name="CPA",
        api_key="cpa-secret",
        api_base="https://cpa.invalid/v1",
    )
    return config


@pytest.mark.asyncio
async def test_ai_can_create_canonical_model_and_preserve_provider():
    service = ModelManagement(_config())
    result = await service.execute(
        "model_create",
        model_id="gpt-cpa",
        display_name="GPT CPA",
        provider="cpa",
        model="openai/gpt-5.6",
        capabilities={"text": True, "vision": True},
        context_window_tokens=200_000,
        pricing={"input": 1.25, "output": 10.0, "cache_read": 0.125},
        generation_defaults={"temperature": 0.2, "max_tokens": 16_384},
        offering_id="cpa:openai/gpt-5.6",
        pools=["general", "coding"],
        max_concurrent_requests=4,
    )
    assert result["status"] == "ok"
    assert service.config.models["gpt-cpa"].provider == "cpa"
    assert service.config.models["gpt-cpa"].model == "openai/gpt-5.6"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "needle"),
    [
        ({"model_id": "Bad ID", "display_name": "Bad", "provider": "anthropic", "model": "x"}, "model_id"),
        ({"model_id": "auto-model", "display_name": "Bad", "provider": "auto", "model": "x"}, "concrete provider"),
        ({"model_id": "missing-provider", "display_name": "Bad", "provider": "does-not-exist", "model": "x"}, "unknown provider"),
        ({"model_id": "legacy-vision", "display_name": "Bad", "provider": "anthropic", "model": "x", "supports_vision": True}, "supports_vision"),
        ({"model_id": "legacy-fleet", "display_name": "Bad", "provider": "anthropic", "model": "x", "fleet_pools": ["general"]}, "fleet_pools"),
        ({"model_id": "legacy-temperature", "display_name": "Bad", "provider": "anthropic", "model": "x", "temperature": 0.3}, "temperature"),
    ],
)
async def test_model_create_rejects_invalid_or_legacy_fields(params, needle):
    service = ModelManagement(_config())
    result = await service.execute("model_create", **params)
    assert result["status"] == "error"
    assert needle in result["message"]


@pytest.mark.asyncio
async def test_display_name_updates_but_model_id_cannot_rename():
    service = ModelManagement(_config())
    await service.execute(
        "model_create",
        model_id="worker",
        display_name="Worker",
        provider="anthropic",
        model="claude-worker",
    )
    updated = await service.execute("model_update", model_id="worker", display_name="Worker v2")
    assert updated["status"] == "ok"
    assert service.config.models["worker"].display_name == "Worker v2"

    renamed = await service.execute("model_update", model_id="worker", new_name="renamed")
    assert renamed["status"] == "error"
    assert "new_name" in renamed["message"]
    assert "worker" in service.config.models
    assert "renamed" not in service.config.models


@pytest.mark.asyncio
async def test_delete_returns_unified_usages_and_never_rewrites_references():
    config = _config()
    config.models["worker"] = ModelConfig(
        display_name="Worker",
        provider="anthropic",
        model="claude-worker",
    )
    config.agents.defaults.dream.model_id = "worker"
    config.subagent_roles["coder"] = SubagentRoleConfig(model_id="worker")
    service = ModelManagement(config)

    rejected = await service.execute("model_delete", model_id="worker")
    assert rejected == {
        "status": "error",
        "message": "model is in use",
        "usages": ["dream.model_id", "subagent_roles.coder.model_id"],
    }
    assert config.agents.defaults.dream.model_id == "worker"
    assert config.subagent_roles["coder"].model_id == "worker"
    assert "worker" in config.models


@pytest.mark.asyncio
async def test_unreferenced_model_can_be_deleted():
    config = _config()
    config.models["spare"] = ModelConfig(
        display_name="Spare",
        provider="anthropic",
        model="claude-spare",
    )
    service = ModelManagement(config)
    deleted = await service.execute("model_delete", model_id="spare")
    assert deleted == {"status": "ok", "model_id": "spare"}
    assert "spare" not in config.models


@pytest.mark.asyncio
async def test_catalog_contains_only_real_models_and_no_credentials():
    config = _config()
    proxy = "http://user:super-secret-password@proxy.example:8080"
    config.providers.anthropic.proxy = proxy
    config.providers.anthropic.extra_headers = {"Authorization": "Bearer header-secret"}
    config.providers.anthropic.extra_body = {"client_secret": "body-secret"}
    config.providers.anthropic.extra_query = {"access_token": "query-secret"}
    service = ModelManagement(config)
    catalog = await service.execute("list")
    assert catalog["status"] == "ok"
    assert [row["model_id"] for row in catalog["models"]] == ["main"]
    assert all(row["model_id"].casefold() != "default" for row in catalog["models"])
    assert catalog["models"][0]["is_default"] is True

    anthropic = next(row for row in catalog["providers"] if row["provider"] == "anthropic")
    assert "proxy" not in anthropic
    assert "api_key" not in anthropic
    assert "extra_headers" not in anthropic
    assert "extra_body" not in anthropic
    assert "extra_query" not in anthropic

    serialized = json.dumps(catalog)
    for secret in (
        "private-provider-key",
        "cpa-secret",
        "super-secret-password",
        "user:super-secret-password",
        proxy,
        "header-secret",
        "body-secret",
        "query-secret",
    ):
        assert secret not in serialized


@pytest.mark.asyncio
async def test_persistence_and_role_binding_use_model_id(tmp_path):
    path = tmp_path / "instance" / "config.json"
    config = _config()
    config.models["worker"] = ModelConfig(
        display_name="Worker",
        provider="anthropic",
        model="claude-worker",
    )
    save_config(config, path)
    service = ModelManagement(load_config(path))
    result = await service.execute("roles_update", bindings={"writer": "worker"})
    assert result["status"] == "ok"
    saved = load_config(path)
    assert saved.subagent_roles["writer"].model_id == "worker"
    assert saved.models["worker"].display_name == "Worker"


@pytest.mark.asyncio
async def test_memory_management_never_writes_global_config(tmp_path, monkeypatch):
    unrelated = tmp_path / "unrelated.json"
    monkeypatch.setattr("nanobot.config.loader._current_config_path", unrelated)
    config = _config()
    service = ModelManagement(config)
    response = await service.execute("model_create", model_id="worker", display_name="Worker", provider="anthropic", model="claude-worker")
    assert response["status"] == "ok"
    assert "worker" in config.models
    assert not unrelated.exists()


def test_model_config_tool_schema_exposes_only_canonical_model_fields():
    tool = ModelConfigTool(ModelManagement(_config()))
    schema = tool.parameters
    actions = schema["properties"]["action"]["enum"]
    assert "model_get" in actions
    assert "fleet_model_update" in actions
    assert "fleet_profile_update" not in actions
    assert "dream_update" not in actions

    properties = schema["properties"]
    for legacy in (
        "name",
        "new_name",
        "model_preset",
        "supports_vision",
        "supports_image_generation",
        "fleet_pools",
        "input_cost_per_million",
        "output_cost_per_million",
        "cached_input_cost_per_million",
        "temperature",
        "reasoning_effort",
    ):
        assert legacy not in properties
    for canonical in (
        "model_id",
        "display_name",
        "provider",
        "model",
        "capabilities",
        "context_window_tokens",
        "pricing",
        "generation_defaults",
        "offering_id",
        "pools",
        "max_concurrent_requests",
    ):
        assert canonical in properties
