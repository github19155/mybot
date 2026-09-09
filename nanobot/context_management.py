"""Self context inspection and safe session compaction control.

This module deliberately has no runtime imports from ``nanobot.agent`` or
``nanobot.config``. It is loaded while configuration/tool classes are being
resolved, so keeping the adapter dependency-light prevents import cycles.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from nanobot.agent.memory import Consolidator
    from nanobot.session.manager import Session, SessionManager
    from nanobot.utils.llm_runtime import LLMRuntime


_CONTEXT_INPUT_SAFETY_BUFFER = 1024
_MIN_COMPACTED_REPLAY_MESSAGES = 8
CONTEXT_CONSIDER_RATIO = 0.60
CONTEXT_RECOMMEND_RATIO = 0.75
CONTEXT_STRONG_RATIO = 0.85


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    session_key: str
    estimated_tokens: int
    estimate_source: str
    input_budget_tokens: int
    context_window_tokens: int
    max_output_tokens: int
    usage_ratio: float | None
    raw_messages: int
    replayable_messages: int
    archived_messages: int
    unarchived_messages: int
    has_summary: bool
    can_compact: bool
    recommendation: str

    @property
    def should_compact(self) -> bool:
        return self.can_compact and self.usage_ratio is not None and (
            self.usage_ratio >= CONTEXT_RECOMMEND_RATIO
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "session_key": self.session_key,
            "estimated_tokens": self.estimated_tokens,
            "estimate_source": self.estimate_source,
            "input_budget_tokens": self.input_budget_tokens,
            "context_window_tokens": self.context_window_tokens,
            "max_output_tokens": self.max_output_tokens,
            "usage_ratio": self.usage_ratio,
            "usage_percent": (
                round(self.usage_ratio * 100, 1)
                if self.usage_ratio is not None
                else None
            ),
            "raw_messages": self.raw_messages,
            "replayable_messages": self.replayable_messages,
            "archived_messages": self.archived_messages,
            "unarchived_messages": self.unarchived_messages,
            "has_summary": self.has_summary,
            "can_compact": self.can_compact,
            "recommendation": self.recommendation,
            "should_compact": self.should_compact,
        }


class _ContextControlTarget(Protocol):
    sessions: SessionManager
    consolidator: Consolidator
    context_block_limit: int | None

    def runtime_for_session(
        self,
        session: Session,
        *,
        recover_removed: bool = True,
    ) -> LLMRuntime: ...


class AgentContextControl:
    """Inspect and compact the current Main session through existing primitives."""

    def __init__(self, target: _ContextControlTarget) -> None:
        self.__target = target

    def _runtime(self, session: Session, runtime: LLMRuntime | None) -> LLMRuntime:
        return runtime or self.__target.runtime_for_session(session)

    def _input_budget(self, runtime: LLMRuntime) -> int:
        budget = self.__target.context_block_limit or (
            runtime.context_window_tokens
            - runtime.generation.max_tokens
            - _CONTEXT_INPUT_SAFETY_BUFFER
        )
        return max(0, budget)

    @staticmethod
    def _recommendation(ratio: float | None, can_compact: bool) -> str:
        if not can_compact or ratio is None:
            return "none"
        if ratio >= CONTEXT_STRONG_RATIO:
            return "strongly_recommended"
        if ratio >= CONTEXT_RECOMMEND_RATIO:
            return "recommended"
        if ratio >= CONTEXT_CONSIDER_RATIO:
            return "consider"
        return "none"

    @staticmethod
    def _last_usage_tokens(metadata: dict[str, Any]) -> int:
        raw = metadata.get("_last_usage")
        if not isinstance(raw, dict):
            return 0
        context_tokens = raw.get("context_tokens")
        if isinstance(context_tokens, int) and not isinstance(context_tokens, bool):
            return max(0, context_tokens)
        input_tokens = raw.get("input_tokens")
        if isinstance(input_tokens, int) and not isinstance(input_tokens, bool):
            return max(0, input_tokens)
        return 0

    @staticmethod
    def _has_summary(metadata: dict[str, Any]) -> bool:
        raw = metadata.get("_last_summary")
        return isinstance(raw, dict) and isinstance(raw.get("text"), str) and bool(raw["text"])

    def status(
        self,
        session_key: str,
        *,
        runtime: LLMRuntime | None = None,
    ) -> ContextSnapshot:
        session = self.__target.sessions.get_or_create(session_key)
        effective_runtime = self._runtime(session, runtime)
        input_budget = self._input_budget(effective_runtime)
        estimated = 0
        source = "unavailable"
        try:
            estimated, source = self.__target.consolidator.estimate_session_prompt_tokens(
                session,
                runtime=effective_runtime,
            )
        except Exception:
            estimated = self._last_usage_tokens(session.metadata)
            if estimated > 0:
                source = "last provider usage"

        ratio = estimated / input_budget if input_budget > 0 else None
        replayable = len(session.get_history(max_messages=0))
        raw_messages = len(session.messages)
        archived = session.last_archived
        unarchived = max(0, raw_messages - archived)
        can_compact = (
            not session_key.startswith("dream:")
            and unarchived > 0
            and replayable > _MIN_COMPACTED_REPLAY_MESSAGES
        )
        return ContextSnapshot(
            session_key=session_key,
            estimated_tokens=max(0, estimated),
            estimate_source=source,
            input_budget_tokens=input_budget,
            context_window_tokens=effective_runtime.context_window_tokens,
            max_output_tokens=effective_runtime.generation.max_tokens,
            usage_ratio=round(ratio, 6) if ratio is not None else None,
            raw_messages=raw_messages,
            replayable_messages=replayable,
            archived_messages=archived,
            unarchived_messages=unarchived,
            has_summary=self._has_summary(session.metadata),
            can_compact=can_compact,
            recommendation=self._recommendation(ratio, can_compact),
        )

    async def compact(
        self,
        session_key: str,
        *,
        runtime: LLMRuntime | None = None,
    ) -> dict[str, object]:
        """Advance the persisted checkpoint without mutating a runner transcript."""
        before = self.status(session_key, runtime=runtime)
        if not before.can_compact:
            return {
                "status": "noop",
                "reason": "not_enough_replayable_history",
                "before": before.as_dict(),
                "after": before.as_dict(),
            }

        session = self.__target.sessions.get_or_create(session_key)
        effective_runtime = self._runtime(session, runtime)
        lock = self.__target.consolidator.get_lock(session_key)
        async with lock:
            archive_start = session.last_archived
            archive_end = len(session.messages)
            if archive_end <= archive_start:
                after = self.status(session_key, runtime=effective_runtime)
                return {
                    "status": "noop",
                    "reason": "no_unarchived_messages",
                    "before": before.as_dict(),
                    "after": after.as_dict(),
                }
            last_active = session.updated_at
            summary = await self.__target.consolidator.archive_session(
                session,
                archive_end=archive_end,
                runtime=effective_runtime,
            )
            if summary is None:
                return {
                    "status": "error",
                    "reason": "compaction_failed",
                    "before": before.as_dict(),
                }
            if summary != "(nothing)":
                session.metadata["_last_summary"] = {
                    "text": summary,
                    "last_active": last_active.isoformat(),
                }
            session.last_archived = archive_end
            session.provider_state = None
            self.__target.sessions.save(session)

        after = self.status(session_key, runtime=effective_runtime)
        return {
            "status": "compacted",
            "reason": "manual_compaction",
            "applies_to": "later_model_requests",
            "before": before.as_dict(),
            "after": after.as_dict(),
        }
