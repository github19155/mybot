"""Dream-owned model selection for read-only background cognition."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from nanobot.agent.model_runtime import ModelRuntimeResolver
    from nanobot.config.schema import DreamConfig
    from nanobot.utils.llm_runtime import LLMRuntime

_DREAM_MIN_CONTEXT_TOKENS = 16_000

FleetRecommend = Callable[..., Awaitable[dict[str, object]]]


async def resolve_dream_runtime(
    workload: str,
    *,
    config: "DreamConfig",
    runtime_resolver: "ModelRuntimeResolver",
    fleet_recommend: FleetRecommend,
) -> "LLMRuntime":
    """Resolve Dream's model without giving up workload-aware Fleet routing.

    Selection order is deliberately strict:
    1. explicit ``DreamConfig.model_id``;
    2. the configured workload pool's Fleet recommendation;
    3. ``fallback_model_id``;
    4. fail explicitly.

    Model identity is always a canonical model ID. Provider/model strings and
    preset names are not accepted at this boundary.
    """
    selected_model_id = config.model_id
    if selected_model_id is None:
        recommendation = await fleet_recommend(
            pool=config.pool_for(workload),
            task_type="background",
            min_context_tokens=_DREAM_MIN_CONTEXT_TOKENS,
        )
        if recommendation.get("status") == "ok":
            recommended = recommendation.get("recommended")
            if isinstance(recommended, dict):
                value = cast(dict[str, Any], recommended).get("model_id")
                if isinstance(value, str) and value.strip():
                    selected_model_id = value.strip()

    if selected_model_id is None:
        selected_model_id = config.fallback_model_id
    if selected_model_id is None:
        raise RuntimeError(
            "Dream has no eligible model route; configure dream.model_id, a workload pool, "
            "or dream.fallback_model_id"
        )

    return runtime_resolver.resolve_selection(
        runtime_resolver.runtime,
        model_id=selected_model_id,
    )
