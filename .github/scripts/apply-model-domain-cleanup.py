from __future__ import annotations

from pathlib import Path
import re


def replace(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text()
    if old not in text:
        if new in text:
            return
        raise RuntimeError(f"pattern not found in {path}: {old[:80]!r}")
    p.write_text(text.replace(old, new))


# WebUI session cache is rebuildable: canonicalize selector/output and bump version.
p = Path("nanobot/webui/session_list_index.py")
text = p.read_text()
text = text.replace("model_preset_from_metadata", "model_id_from_metadata")
text = text.replace("_MODEL_PRESET_FIELD", "_MODEL_ID_FIELD")
text = text.replace('"model_preset"', '"model_id"')
text = text.replace("model_preset", "model_id")
text = text.replace("_INDEX_VERSION = 8", "_INDEX_VERSION = 9")
p.write_text(text)

# Existing Fleet DBs need a real in-place column migration; CREATE TABLE IF NOT EXISTS is insufficient.
replace(
    "nanobot/model_fleet.py",
    '''                """\n            )\n\n    def upsert_offering''',
    '''                """\n            )\n            offering_columns = {\n                str(row["name"])\n                for row in self._db.execute("PRAGMA table_info(offerings)")\n            }\n            if "preset_name" in offering_columns and "model_id" not in offering_columns:\n                self._db.execute(\n                    "ALTER TABLE offerings RENAME COLUMN preset_name TO model_id"\n                )\n\n    def upsert_offering''',
)

# Provider restart identity for Image Generation comes from the canonical model route.
replace(
    "nanobot/model_settings.py",
    "from nanobot.model_domain import ModelConfig, validate_model_id",
    "from nanobot.model_domain import ModelConfig, get_model, validate_model_id",
)
replace(
    "nanobot/model_settings.py",
    '''    image_config = config.tools.image_generation\n    restart_required = changed and image_config.enabled and image_config.provider == provider_key and get_image_gen_provider(provider_key) is not None\n    return changed, restart_required''',
    '''    image_config = config.tools.image_generation\n    image_provider: str | None = None\n    if image_config.model_id is not None:\n        image_provider = get_model(config.models, image_config.model_id).provider\n    restart_required = (\n        changed\n        and image_config.enabled\n        and image_provider == provider_key\n        and get_image_gen_provider(provider_key) is not None\n    )\n    return changed, restart_required''',
)

# Worker tools consume the same validated Config.models snapshot as Main.
replace(
    "nanobot/agent/subagent.py",
    '''        cfg = tools_config if tools_config is not None else self._subagent_tools_config()\n        resolver = self.runtime_resolver\n        ctx = ToolContext(''',
    '''        cfg = tools_config if tools_config is not None else self._subagent_tools_config()\n        resolver = self.runtime_resolver\n        config_snapshot = self._role_config()\n        ctx = ToolContext(''',
)
replace(
    "nanobot/agent/subagent.py",
    '''            file_state_store=FileStates(),\n            provider_snapshot_loader=(''',
    '''            file_state_store=FileStates(),\n            models=config_snapshot.models if config_snapshot is not None else {},\n            provider_snapshot_loader=(''',
)

# No-side-effect worker capability discovery must use the same canonical model registry.
replace(
    "nanobot/agent/tools/subagent.py",
    '''        config = config_builder()\n        resolver = getattr(self._manager, "runtime_resolver", None)''',
    '''        config = config_builder()\n        config_snapshot_loader = getattr(self._manager, "_role_config", None)\n        config_snapshot = (\n            config_snapshot_loader() if callable(config_snapshot_loader) else None\n        )\n        models = config_snapshot.models if config_snapshot is not None else {}\n        resolver = getattr(self._manager, "runtime_resolver", None)''',
)
replace(
    "nanobot/agent/tools/subagent.py",
    '''            subagent_manager=self._manager,\n            exec_session_manager=getattr(self._manager, "_exec_session_manager", None),''',
    '''            subagent_manager=self._manager,\n            exec_session_manager=getattr(self._manager, "_exec_session_manager", None),\n            models=models,''',
)
replace(
    "nanobot/agent/tools/subagent.py",
    '''        if "generate_image" in available and (\n            not image_provider_configs\n            or config.image_generation.provider not in image_provider_configs\n        ):\n            available.discard("generate_image")''',
    '''        if "generate_image" in available:\n            model_id = config.image_generation.model_id\n            image_model = models.get(model_id) if model_id is not None else None\n            if (\n                image_model is None\n                or not image_model.capabilities.image_generation\n                or not image_provider_configs\n                or image_model.provider not in image_provider_configs\n            ):\n                available.discard("generate_image")''',
)

# Model Management no longer owns runtime construction and no longer needs parallel-branch fallback glue.
replace(
    "nanobot/agent/model_management.py",
    '''        *,\n        runtime_resolver: Any | None = None,\n        invalidate: Callable[[], None] | None = None,\n    ) -> None:\n        # Compatibility glue while B/E callers migrate. Model Management does\n        # not execute runtimes and deliberately does not use this resolver.\n        del runtime_resolver''',
    '''        *,\n        invalidate: Callable[[], None] | None = None,\n    ) -> None:''',
)
replace(
    "nanobot/agent/model_management.py",
    '''    @staticmethod\n    def _canonical_offering(config: Config, model_id: str):\n        """Delegate canonical ModelConfig -> Fleet offering construction to B."""\n        try:\n            return offering_from_config(config, model_id=model_id)  # type: ignore[call-arg]\n        except TypeError as exc:\n            raise ModelSettingsError(\n                "canonical Model Fleet adapter is unavailable; integrate refactor/model-runtime-fleet-v2",\n                status=503,\n            ) from exc''',
    '''    @staticmethod\n    def _canonical_offering(config: Config, model_id: str):\n        """Delegate canonical ModelConfig -> Fleet offering construction."""\n        return offering_from_config(config, model_id=model_id)''',
)

# model_id is immutable identity; there is deliberately no rename API.
p = Path("nanobot/session/manager.py")
text = p.read_text()
text = re.sub(
    r"\n    def rename_model_preset\(.*?(?=\n    def |\n    @|\Z)",
    "\n",
    text,
    flags=re.S,
)
p.write_text(text)

# Fleet migration regression coverage, including telemetry preservation and repeat initialization.
p = Path("tests/test_model_fleet.py")
text = p.read_text()
if "test_fleet_store_migrates_legacy_preset_name_column" not in text:
    text = text.replace("import asyncio\n", "import asyncio\nimport sqlite3\n")
    text += r'''


def _offering_columns(path: Path) -> list[str]:
    with sqlite3.connect(path) as db:
        return [str(row[1]) for row in db.execute("PRAGMA table_info(offerings)")]


def test_fleet_store_fresh_db_uses_model_id_column(tmp_path: Path) -> None:
    path = tmp_path / "fleet.db"
    store = ModelFleetStore(path)
    assert "model_id" in _offering_columns(path)
    assert "preset_name" not in _offering_columns(path)
    store._db.close()  # pyright: ignore[reportPrivateUsage]


def test_fleet_store_migrates_legacy_preset_name_column_and_preserves_telemetry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fleet.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE offerings (
                offering_id TEXT PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL,
                preset_name TEXT, pools_json TEXT NOT NULL DEFAULT '[]',
                input_cost_per_million REAL, output_cost_per_million REAL,
                cached_input_cost_per_million REAL, supports_vision INTEGER NOT NULL DEFAULT 0,
                context_window_tokens INTEGER, updated_at_ms INTEGER NOT NULL
            );
            CREATE TABLE calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT, offering_id TEXT NOT NULL,
                started_at_ms INTEGER NOT NULL, duration_ms INTEGER NOT NULL,
                input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
                generation_ms INTEGER, ttft_ms INTEGER, finish_reason TEXT NOT NULL,
                error_status_code INTEGER, error_kind TEXT, estimated_cost REAL
            );
            CREATE TABLE quality_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, offering_id TEXT NOT NULL,
                dimension TEXT NOT NULL, outcome REAL NOT NULL, weight REAL NOT NULL,
                evidence TEXT, created_at_ms INTEGER NOT NULL
            );
            CREATE TABLE score_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT, offering_id TEXT NOT NULL,
                calculated_at_ms INTEGER NOT NULL, quality REAL, speed REAL,
                reliability REAL, cost REAL, confidence REAL NOT NULL, trend REAL NOT NULL,
                samples INTEGER NOT NULL, metrics_json TEXT NOT NULL
            );
            CREATE TABLE aggregates (
                offering_id TEXT NOT NULL, bucket_kind TEXT NOT NULL,
                bucket_start_ms INTEGER NOT NULL, calls INTEGER NOT NULL,
                successes INTEGER NOT NULL, rate_limits INTEGER NOT NULL,
                avg_duration_ms REAL, avg_ttft_ms REAL, avg_generation_ms REAL,
                total_cost REAL,
                PRIMARY KEY(offering_id, bucket_kind, bucket_start_ms)
            );
            INSERT INTO offerings VALUES (
                'offer-1', 'cpa', 'openai/gpt-5.6', 'fast-model', '[]',
                NULL, NULL, NULL, 0, NULL, 1
            );
            INSERT INTO calls(
                offering_id, started_at_ms, duration_ms, finish_reason
            ) VALUES ('offer-1', 1, 2, 'stop');
            INSERT INTO quality_events(
                offering_id, dimension, outcome, weight, evidence, created_at_ms
            ) VALUES ('offer-1', 'general', 1.0, 1.0, 'kept', 1);
            INSERT INTO score_snapshots(
                offering_id, calculated_at_ms, confidence, trend, samples, metrics_json
            ) VALUES ('offer-1', 1, 0.5, 0.0, 1, '{}');
            INSERT INTO aggregates(
                offering_id, bucket_kind, bucket_start_ms, calls, successes, rate_limits
            ) VALUES ('offer-1', 'hour', 1, 1, 1, 0);
            """
        )

    assert "preset_name" in _offering_columns(path)
    assert "model_id" not in _offering_columns(path)

    first = ModelFleetStore(path)
    columns = _offering_columns(path)
    assert "model_id" in columns
    assert "preset_name" not in columns
    row = first.offerings()[0]
    assert row["model_id"] == "fast-model"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM quality_events").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM score_snapshots").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM aggregates").fetchone()[0] == 1
    first._db.close()  # pyright: ignore[reportPrivateUsage]

    second = ModelFleetStore(path)
    assert second.offerings()[0]["model_id"] == "fast-model"
    second._db.close()  # pyright: ignore[reportPrivateUsage]
'''
    p.write_text(text)
