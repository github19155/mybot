from __future__ import annotations

import re
from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def remove_span(text: str, start: str, end: str, *, label: str) -> str:
    a = text.find(start)
    if a < 0:
        raise RuntimeError(f"{label}: start not found")
    b = text.find(end, a)
    if b < 0:
        raise RuntimeError(f"{label}: end not found")
    return text[:a] + text[b:]


# Core settings: call-order is retained only as a one-item explicit active-preset selection.
p = "nanobot/model_settings.py"
s = read(p)
s = s.replace(
    "    defaults.fallback_models = [new_name if fallback == old_name else fallback for fallback in defaults.fallback_models]\n",
    "",
)
s = re.sub(
    r"def _model_call_order_state\(config: Config\) -> tuple\[list\[str\], bool\]:\n.*?\n\ndef _legacy_model_configuration_migratable",
    "def _model_call_order_state(config: Config) -> tuple[list[str], bool]:\n"
    "    primary = config.agents.defaults.model_preset\n"
    "    if not primary or primary == \"default\" or primary not in config.model_presets:\n"
    "        return [], False\n"
    "    return [primary], True\n\n\n"
    "def _legacy_model_configuration_migratable",
    s,
    count=1,
    flags=re.S,
)
s = s.replace("    if defaults.fallback_models:\n        return True\n", "")
s = s.replace("        config.agents.defaults.fallback_models = []\n", "")
s = s.replace(
    "    if defaults.model_preset == name or any(fallback == name for fallback in defaults.fallback_models):\n"
    "        raise ModelSettingsError(\"remove the model preset from the call order first\", status=409)\n",
    "    if defaults.model_preset == name:\n"
    "        raise ModelSettingsError(\"select another model preset before deleting it\", status=409)\n",
)
write(p, s)

# WebUI model settings: no fallback candidates; active selection is exactly one preset.
p = "nanobot/webui/settings_models.py"
s = read(p)
s = s.replace("    FallbackCandidate,\n", "")
s = s.replace(
    "    defaults.fallback_models = [\n"
    "        new_name if fallback == old_name else fallback\n"
    "        for fallback in defaults.fallback_models\n"
    "    ]\n",
    "",
)
s = re.sub(
    r"def _model_call_order_state\(config: Config\) -> tuple\[list\[str\], bool\]:\n.*?\n\ndef _legacy_model_configuration_migratable",
    "def _model_call_order_state(config: Config) -> tuple[list[str], bool]:\n"
    "    primary = config.agents.defaults.model_preset\n"
    "    if not primary or primary == \"default\" or primary not in config.model_presets:\n"
    "        return [], False\n"
    "    return [primary], True\n\n\n"
    "def _legacy_model_configuration_migratable",
    s,
    count=1,
    flags=re.S,
)
s = s.replace(
    "    Inline fallbacks, or a default whose matching provider is configured,\n"
    "    are evidence that there is real legacy state to preserve.\n",
    "    A configured implicit default is evidence that there is real legacy state to preserve.\n",
)
s = s.replace("    if defaults.fallback_models:\n        return True\n\n", "")
s = s.replace("        config.agents.defaults.fallback_models = []\n", "")
start = s.index("def update_model_call_order(")
end = s.index("def update_model_prompt_overrides(", start)
replacement = '''def update_model_call_order(\n    config: Config,\n    query: QueryParams,\n    *,\n    oauth_status: OAuthStatusReader,\n) -> bool:\n    \"\"\"Select exactly one active model preset.\n\n    The legacy endpoint name is retained as an HTTP compatibility seam, but it\n    no longer represents or stores an ordered failover chain.\n    \"\"\"\n    raw_order = query_first_alias(query, \"order\", \"presetNames\")\n    if raw_order is None:\n        raise WebUISettingsError(\"model selection is required\")\n    try:\n        order: object = json.loads(raw_order)\n    except json.JSONDecodeError:\n        raise WebUISettingsError(\"model selection must be a JSON array\") from None\n    if (\n        not isinstance(order, list)\n        or len(order) != 1\n        or not isinstance(order[0], str)\n        or not order[0].strip()\n    ):\n        raise WebUISettingsError(\"select exactly one model preset\")\n\n    selected = cast(str, order[0]).strip()\n    if selected not in config.model_presets:\n        raise WebUISettingsError(f\"unknown model preset: {selected}\")\n\n    _, editable = _model_call_order_state(config)\n    if not editable and _legacy_model_configuration_migratable(config, oauth_status):\n        raise WebUISettingsError(\n            \"convert the existing model configuration to a preset first\",\n            status=409,\n        )\n\n    defaults = config.agents.defaults\n    changed = defaults.model_preset != selected\n    if changed:\n        defaults.model_preset = selected\n    return changed\n\n\n'''
s = s[:start] + replacement + s[end:]
start = s.index("def migrate_model_configurations(")
end = s.index("def delete_model_configuration(", start)
replacement = '''def migrate_model_configurations(\n    config: Config,\n    *,\n    oauth_status: OAuthStatusReader,\n) -> bool:\n    \"\"\"Materialize the implicit legacy model settings as one named preset.\"\"\"\n    _, editable = _model_call_order_state(config)\n    if editable:\n        return False\n    if not _legacy_model_configuration_migratable(config, oauth_status):\n        raise WebUISettingsError(\"there is no legacy model configuration to convert\", status=409)\n\n    defaults = config.agents.defaults\n    primary = config.resolve_preset()\n    if defaults.model_preset and defaults.model_preset != \"default\":\n        return False\n    label = _model_configuration_label(primary.model)\n    name = _unique_model_configuration_name(config, label)\n    config.model_presets[name] = ModelPresetConfig(\n        model=primary.model,\n        provider=primary.provider,\n        max_tokens=primary.max_tokens,\n        context_window_tokens=primary.context_window_tokens,\n        temperature=primary.temperature,\n        reasoning_effort=primary.reasoning_effort,\n        supports_vision=primary.supports_vision,\n        supports_image_generation=primary.supports_image_generation,\n    )\n    defaults.model_preset = name\n    return True\n\n\n'''
s = s[:start] + replacement + s[end:]
s = s.replace(
    "    referenced = defaults.model_preset == name or any(\n"
    "        fallback == name for fallback in defaults.fallback_models\n"
    "    )\n"
    "    if referenced:\n"
    "        raise WebUISettingsError(\n"
    "            \"remove the model preset from the call order first\",\n"
    "            status=409,\n"
    "        )\n",
    "    if defaults.model_preset == name:\n"
    "        raise WebUISettingsError(\n"
    "            \"select another model preset before deleting it\",\n"
    "            status=409,\n"
    "        )\n",
)
write(p, s)

# Remove the transparent failover provider entirely.
Path("nanobot/providers/fallback_provider.py").unlink(missing_ok=True)

# Remove WebUI fallback observer; turn model events remain for explicit runtime selection.
p = "nanobot/session/webui_turns.py"
s = read(p)
s = s.replace("from nanobot.providers.fallback_provider import FallbackModelObserver\n", "")
s = remove_span(
    s,
    "def build_webui_fallback_model_observer(",
    "@dataclass\nclass WebuiTurnCoordinator:",
    label="webui fallback observer",
)
write(p, s)

# Turn-model events no longer carry a failover marker.
p = "nanobot/bus/outbound_events.py"
s = read(p).replace("    fallback: bool = False\n", "")
write(p, s)

p = "nanobot/channels/websocket/runtime.py"
s = read(p)
s = s.replace("        fallback: bool = False,\n", "")
s = s.replace("        if fallback:\n            body[\"fallback\"] = True\n", "")
write(p, s)

p = "nanobot/webui/outbound_projection.py"
s = read(p)
s = s.replace("        fallback: bool = False,\n", "")
s = s.replace("                    fallback=event.fallback,\n", "")
write(p, s)

# WebUI model-preset editor can only switch the active preset, never append a backup.
p = "webui/src/components/settings/models/ModelsSettings.tsx"
s = read(p)
s = s.replace(
    '"Turn the existing primary and fallback models into presets so their order can be managed here.",',
    '"Turn the existing model setup into a named preset so it can be managed here.",',
)
s = s.replace("onChangeCallOrder([...callOrder, preset.name]);", "onChangeCallOrder([preset.name]);")
s = s.replace(
    '"Remove this preset from the call order before deleting it.",',
    '"Select another preset before deleting this one.",',
)
write(p, s)

p = "webui/src/components/settings/models/useModelSettingsActions.ts"
s = read(p).replace(
    "const nextOrder = createdPreset ? [...modelCallOrder, createdPreset] : null;",
    "const nextOrder = createdPreset ? [createdPreset] : null;",
)
write(p, s)

# Remove fallback-model presentation from the composer badge.
p = "webui/src/components/thread/ModelPresetBadge.tsx"
s = read(p)
s = s.replace("  fallbackModelName?: string | null;\n", "")
s = s.replace("  fallbackModelName,\n", "")
block = '''  const fallbackPreset = fallbackModelName\n    ? modelPresets.find((preset) => preset.model?.trim() === fallbackModelName.trim())\n    : undefined;\n  const fallbackDisplayLabel = fallbackPreset?.name\n    || fallbackModelName?.trim().split(/[/:]/).pop()\n    || null;\n  const displayLabel = fallbackDisplayLabel || label;\n  const displayModelDetail = fallbackPreset\n    ? fallbackPreset.model\n    : fallbackModelName\n      ? null\n      : modelDetail;\n  const displayProvider = fallbackPreset?.provider\n    || (fallbackModelName ? inferProviderFromModelName(fallbackModelName) : provider);\n'''
s = s.replace(block, "  const displayLabel = label;\n  const displayModelDetail = modelDetail;\n  const displayProvider = provider;\n")
s = s.replace("      providerLabel={fallbackModelName ? null : providerLabel}\n", "      providerLabel={providerLabel}\n")
s = s.replace("      fallbackModelName={fallbackModelName}\n", "")
s = s.replace("      fallbackFromLabel={fallbackModelName ? label : null}\n", "")
s = s.replace('        aria-label={fallbackModelName ? `${displayLabel} (fallback from ${label})` : label}\n', "        aria-label={label}\n")
s = s.replace("  fallbackFromLabel,\n", "")
s = s.replace("  fallbackFromLabel?: string | null;\n", "")
s = s.replace(
    "  const fallbackTitle = fallbackModelName\n    ? `${fallbackFromLabel || label} · using ${fallbackModelName}`\n    : title;\n",
    "",
)
s = s.replace('      data-fallback={fallbackModelName ? "true" : undefined}\n', "")
s = s.replace("      title={fallbackTitle || undefined}\n", "      title={title || undefined}\n")
write(p, s)

# Thread composer/shell no longer track an effective fallback model.
p = "webui/src/components/thread/ThreadComposer.tsx"
s = read(p)
s = s.replace("  fallbackModelName?: string | null;\n", "")
s = s.replace("  fallbackModelName = null,\n", "")
s = s.replace("                fallbackModelName={fallbackModelName}\n", "")
write(p, s)

p = "webui/src/components/thread/ThreadShell.tsx"
s = read(p)
s = s.replace("  const [fallbackModelName, setFallbackModelName] = useState<string | null>(null);\n", "")
s = re.sub(
    r"\n  useEffect\(\(\) => \{\n    const unsubscribe = client\.subscribeChatEvent\(.*?event\.fallback !== true.*?\n  \}, \[.*?\]\);",
    "",
    s,
    count=1,
    flags=re.S,
)
s = s.replace("          fallbackModelName={fallbackModelName}\n", "")
write(p, s)

p = "webui/src/lib/types.ts"
s = read(p).replace("      fallback?: boolean;\n", "")
write(p, s)

# Styling only used by fallback model badges.
p = "webui/src/globals.css"
s = read(p)
s = re.sub(
    r"\n  \.composer-model-badge\[data-fallback=\"true\"\] \{.*?\n  \}\n\n  \.composer-model-badge\[data-fallback=\"true\"\]::before \{.*?\n  \}\n\n  \.dark \.composer-model-badge\[data-fallback=\"true\"\]::before \{.*?\n  \}\n",
    "\n",
    s,
    count=1,
    flags=re.S,
)
write(p, s)

# English product copy: no model failover promise.
p = "webui/src/i18n/locales/en/common.json"
s = read(p)
s = s.replace(
    '"convertHelp": "Turn the existing primary and fallback models into presets so their order can be managed here."',
    '"convertHelp": "Turn the existing model setup into a named preset so it can be managed here."',
)
s = s.replace(
    '"removeBeforeDelete": "Remove this preset from the call order before deleting it."',
    '"removeBeforeDelete": "Select another preset before deleting this one."',
)
write(p, s)

print("remaining fallback product transform applied")
