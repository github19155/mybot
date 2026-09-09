"""Leaf-provider wrapper that applies model-fleet admission per physical attempt."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from nanobot.model_fleet import ModelFleetManager, ModelOffering, current_fleet_priority
from nanobot.providers.base import (
    GenerationSettings,
    LLMProvider,
    LLMResponse,
    ProviderCallContext,
    ProviderConversationState,
)


class FleetControlledProvider(LLMProvider):
    """Gate a concrete provider/model route without holding slots across retries.

    The inherited retry loop invokes this wrapper's ``_safe_chat`` once per
    attempt.  That method calls ``chat``/``chat_stream`` here, so the admission
    slot surrounds only the underlying physical API call and is released before
    the base retry policy sleeps.  FallbackProvider can therefore wrap these
    leaves and each fallback route keeps its own fleet identity.
    """

    def __init__(
        self,
        inner: LLMProvider,
        *,
        fleet: ModelFleetManager,
        offering: ModelOffering,
    ) -> None:
        generation = inner.generation
        self._inner = inner
        self.fleet = fleet
        self.offering = fleet.bind_offering(offering)
        super().__init__(provider_name=inner.provider_name)
        self._inner.generation = generation

    @property
    def generation(self) -> GenerationSettings:
        return self._inner.generation

    @generation.setter
    def generation(self, value: GenerationSettings) -> None:
        self._inner.generation = value

    def get_default_model(self) -> str:
        return self._inner.get_default_model()

    def can_resume_conversation_state(
        self,
        state: ProviderConversationState,
        model: str | None = None,
    ) -> bool:
        return self._inner.can_resume_conversation_state(state, model)

    def supports_native_compaction(self, model: str | None = None) -> bool:
        return self._inner.supports_native_compaction(model)

    async def _run_physical(
        self,
        call: Callable[[], Awaitable[LLMResponse]],
    ) -> LLMResponse:
        async with self.fleet.slot(self.offering, priority=current_fleet_priority()):
            response = await call()
            await self.fleet.note_response(self.offering, response)
            return response

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        return await self._run_physical(lambda: self._inner.chat(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            tool_choice=tool_choice,
        ))

    async def chat_with_context(
        self,
        *,
        provider_context: ProviderCallContext,
        **kwargs: Any,
    ) -> LLMResponse:
        return await self._run_physical(lambda: self._inner.chat_with_context(
            provider_context=provider_context,
            **kwargs,
        ))

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        return await self._run_physical(lambda: self._inner.chat_stream(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            tool_choice=tool_choice,
            on_content_delta=on_content_delta,
            on_thinking_delta=on_thinking_delta,
            on_tool_call_delta=on_tool_call_delta,
        ))

    async def chat_stream_with_context(
        self,
        *,
        provider_context: ProviderCallContext,
        **kwargs: Any,
    ) -> LLMResponse:
        return await self._run_physical(lambda: self._inner.chat_stream_with_context(
            provider_context=provider_context,
            **kwargs,
        ))

    def _observe_llm_call(
        self,
        response: LLMResponse,
        kwargs: dict[str, Any],
        *,
        started_at_ms: int,
        started_at_ns: int,
        stream: bool,
    ) -> LLMResponse:
        """Reuse base usage estimation, then persist the same physical attempt."""
        observed = super()._observe_llm_call(
            response,
            kwargs,
            started_at_ms=started_at_ms,
            started_at_ns=started_at_ns,
            stream=stream,
        )
        duration_ms = max(0, (time.monotonic_ns() - started_at_ns) // 1_000_000)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.fleet.record_call(
                self.offering,
                started_at_ms=started_at_ms,
                duration_ms=duration_ms,
                response=observed,
                usage=observed.usage,
            ))
        except RuntimeError:
            pass
        return observed
