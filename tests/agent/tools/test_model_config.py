"""ModelManagement tests cover management/persistence, not runtime construction."""

from __future__ import annotations

import json

import pytest

from nanobot.agent.model_management import ModelManagement
from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config, ModelPresetConfig


def _config() -> Config:
    config = Config()
    config.providers.openai.api_key = "private-provider-key"
    config.model_presets["worker"] = ModelPresetConfig(model="openai/gpt-worker", provider="openai")
    return config


@pytest.mark.asyncio
async def test_bindings_persist_rename_and_reject_bound_deletion(tmp_path):
    path = tmp_path / "instance" / "config.json"
    save_config(_config(), path)
    service = ModelManagement(load_config(path))
    await service.execute("roles_update", bindings={"coder": "worker"})
    await service.execute("model_update", name="worker", new_name="renamed")
    saved = load_config(path)
    assert saved.subagent_roles["coder"].model_preset == "renamed"
    rejected = await service.execute("model_delete", name="renamed")
    assert rejected["status"] == "error"
    assert "role" in rejected["message"]
    assert "renamed" in load_config(path).model_presets


@pytest.mark.asyncio
async def test_memory_management_never_writes_global_config(tmp_path, monkeypatch):
    unrelated = tmp_path / "unrelated.json"
    monkeypatch.setattr("nanobot.config.loader._current_config_path", unrelated)
    config = _config()
    service = ModelManagement(config)
    response = await service.execute("roles_update", bindings={"writer": "worker"})
    assert response["status"] == "ok"
    assert config.subagent_roles["writer"].model_preset == "worker"
    assert not unrelated.exists()
    assert "private-provider-key" not in json.dumps(await service.execute("list"))
    failure = await service.execute("provider_update", provider="openai", extra_headers={"Authorization": ["private-provider-key"]})
    assert failure["status"] == "error"
    assert "private-provider-key" not in json.dumps(failure)
