from __future__ import annotations

import re
from pathlib import Path


def edit(path: str, pairs: list[tuple[str, str]]) -> None:
    p = Path(path)
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        s = s.replace(old, new)
    p.write_text(s, encoding="utf-8")


def remove_section(path: str, heading: str, next_heading: str) -> None:
    p = Path(path)
    s = p.read_text(encoding="utf-8")
    start = s.find(heading)
    if start < 0:
        raise RuntimeError(f"missing heading {heading!r} in {path}")
    end = s.find(next_heading, start)
    if end < 0:
        raise RuntimeError(f"missing next heading {next_heading!r} in {path}")
    p.write_text(s[:start] + s[end:], encoding="utf-8")


# Dedicated current-product fallback guide no longer exists.
Path("docs/guides/configure-model-fallback.md").unlink(missing_ok=True)

edit("README.md", [
    ("Configure providers, fallback models, Langfuse, MCP, web tools, or security", "Configure providers, model presets, Langfuse, MCP, web tools, or security"),
    ("OpenAI-compatible APIs, local LLMs, image generation, search, and fallbacks.", "OpenAI-compatible APIs, local LLMs, image generation, search, and explicit model presets."),
])

edit("docs/README.md", [
    ("| Add model fallbacks | [Configure Model Fallback](./guides/configure-model-fallback.md) |\n", ""),
])
edit("docs/guides/README.md", [
    ("| Add model fallback | [Configure model fallback](./configure-model-fallback.md) |\n", ""),
])
edit("docs/guides/build-a-personal-ai-agent.md", [
    ("  fallback models.\n", "  named model presets.\n"),
])
edit("docs/quick-start.md", [
    ("Do not add chat apps, MCP servers, fallback models, or deployment until this path works.", "Do not add chat apps, MCP servers, extra model presets, or deployment until this path works."),
])

# Complete configuration reference: remove fallback navigation/current schema and state one-runtime semantics.
p = Path("docs/configuration.md")
s = p.read_text(encoding="utf-8")
s = s.replace("| Configure model fallback | [`guides/configure-model-fallback.md`](./guides/configure-model-fallback.md) |\n", "")
s = s.replace("| Add fallback chains | [Model Fallbacks](#model-fallbacks) |\n", "")
s = s.replace("| Add fallback models | `modelPresets.<fallback>`, `agents.defaults.fallbackModels` | `nanobot status`, then a normal agent run | [Model Fallbacks](#model-fallbacks) |\n", "")
s = s.replace(
    "Model presets let you name a complete model configuration and select one per session with `/model <preset>`. They are the recommended way to configure models because the same names can be reused for new-session defaults, chat-command switching, and fallback chains.",
    "Model presets let you name a complete model configuration and select one per session with `/model <preset>`. They are the recommended way to configure models because the same names can be reused for new-session defaults, chat-command switching, Subagent roles, Dream, and Model Fleet selection.",
)
s = s.replace('      "modelPreset": "fast",\n      "fallbackModels": ["deep", "localSmall"]', '      "modelPreset": "fast"')
s = s.replace(
    "`modelPresets` is a top-level object. Each key (`fast`, `deep`, `coding`, etc.) is the preset's one canonical name: it is shown in the interface, passed to `/model <name>`, and referenced by defaults, fallbacks, sessions, and Dream.",
    "`modelPresets` is a top-level object. Each key (`fast`, `deep`, `coding`, etc.) is the preset's one canonical name: it is shown in the interface, passed to `/model <name>`, and referenced by defaults, sessions, Subagent roles, Dream, and Model Fleet.",
)
start = s.find("### Model Fallbacks\n")
end = s.find("## Transcription Settings\n", start)
if start < 0 or end < 0:
    raise RuntimeError("configuration fallback section bounds missing")
s = s[:start] + (
    "### Request-time model selection\n\n"
    "Each admitted request resolves exactly one `LLMRuntime` through `ModelRuntimeResolver` (and Model Fleet when used). That runtime fixes the model, provider, generation settings, context window, vision capability, and system-prompt override for the request. Provider-level retry may retry the same selected route, but nanobot does not transparently switch to another model or provider after failure; exhausted retries return the failure explicitly.\n\n"
) + s[end:]
p.write_text(s, encoding="utf-8")

# Provider reference: presets are choices, not backup targets; remove failover section.
p = Path("docs/providers.md")
s = p.read_text(encoding="utf-8")
s = s.replace("runtime `/model` switching and fallback chains clearer", "runtime `/model` switching clearer")
s = s.replace("runtime `/model` switching, or reusable fallback targets", "runtime `/model` switching, or reusable explicit model choices")
s = s.replace("OAuth providers are not valid automatic fallbacks. ", "")
start = s.find("## Fallback Models\n")
end = s.find("## Quick Checks\n", start)
if start < 0 or end < 0:
    raise RuntimeError("providers fallback section bounds missing")
s = s[:start] + (
    "## Failure behavior\n\n"
    "A request stays on the model/provider route selected before admission. Provider implementations may retry that same route according to their retry policy. If those retries are exhausted, the request fails explicitly instead of switching to another configured preset or provider. Use `/model`, session presets, Subagent role bindings, Dream policy, or Model Fleet when you want an explicit different model choice before a request starts.\n\n"
) + s[end:]
p.write_text(s, encoding="utf-8")

# Cookbook no longer presents a failover recipe.
p = Path("docs/provider-cookbook.md")
s = p.read_text(encoding="utf-8")
s = s.replace("| A primary model plus one or more backups | [Fallback Presets](#recipe-fallback-presets) | Named presets in `modelPresets`, referenced from `agents.defaults.fallbackModels` |\n", "")
s = s.replace("presets are easier to switch and easier to reuse as fallbacks.", "presets are easier to switch and reuse across sessions, Subagent roles, Dream, and Model Fleet.")
start = s.find("## Recipe: Fallback Presets\n")
end = s.find("## Recipe: Langfuse Tracing\n", start)
if start < 0 or end < 0:
    raise RuntimeError("cookbook fallback recipe bounds missing")
s = s[:start] + s[end:]
p.write_text(s, encoding="utf-8")

# Architecture wording should describe deterministic provider resolution, not failover.
edit("docs/architecture.md", [
    ("local provider fallback when `apiBase` is configured;", "local provider resolution when `apiBase` is configured;"),
    ("gateway fallback for providers that can route many model families.", "gateway resolution for providers that can route many model families."),
])

# Current provider guide introduction.
edit("docs/providers.md", [
    ("presets make runtime `/model` switching and fallback chains clearer", "presets make runtime `/model` switching clearer"),
])

# Dream's field remains, but clarify it is pre-request preset selection, not runtime failover.
edit("docs/memory.md", [
    ("or use the configured Dream fallback preset.", "or use the configured Dream `fallback_preset` as a final pre-request preset choice."),
    ("Dream fallback preset", "Dream final preset choice"),
])

# Design/spec text that claimed global provider failover must match the new authority.
edit("docs/superpowers/specs/2026-09-08-subagent-control-design.md", [
    ("The first version does not add per-role fallback model lists. Existing nanobot preset and\nglobal provider fallback behavior remains authoritative.", "The first version does not add per-role fallback model lists. Model selection remains authoritative through named presets and `ModelRuntimeResolver`; a request does not transparently switch models after admission."),
])

print("fallback docs migrated")
