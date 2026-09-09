"""Self context inspection and safe session compaction control.

This module is intentionally a thin adapter over the existing session,
Consolidator, and ContextGovernor budgeting rules. It does not own a second
context store or compaction pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from nanobot.agent.context_governance import SNIP_SAFETY_BUFFER
from nanobot.providers.base import LLMUsage
from nanobot.session.manager import MIN_COMPACTED_REPLAY_MESSAGES
from nanobot.session.summary import session_summary_from_metadata

if TYPE_CHECKING:
    from nanobot.agent.memory import Consolidator
    from nanobot.session.manager import Session, SessionManager
    from nanobot.utils.llm_runtime import LLMRuntime


CONTEXT_CONSIDER_RATIO = 0.60
CONTEXT_RECOMMEND_RATIO = 0.75
CONTEXT_STRONG_RATIO = 0.85


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """Machine-readable view of one session's model-facing context pressure."""

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
    pending_compaction: bool
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
            "pending_compaction": self.pending_compaction,
            "recommendation": self.recommendation,
            "should_compact": self.should_compact,
        }


class _ContextControlTarget(Protocol):
    sessions: SessionManager
    consolidator: Consolidator
    context_block_limit: int | None

    def runtime_for_session(self, session: Session, *, recover_removed: bool = True) -> LLMRuntime: ...


class AgentContextControl:
    """Allow Main and slash commands to reuse the normal session compactor safely."""

    def __init__(self, target: _ContextControlTarget) -> None:
        self.__target = target
        # Main calls context.compact during an active turn. Do not rewrite that
        # turn's session underneath AgentRunner; consume this request at the
        # next turn's existing COMPACT stage instead.
        self.__pending: set[str] = set()

    def _runtime(self, session: Session, runtime: LLMRuntime | None) -> LLMRuntime:
        return runtime or self.__target.runtime_for_session(session)

    def _input_budget(self, runtime: LLMRuntime) -> int:
        budget = self.__target.context_block_limit or (
            runtime.context_window_tokens
            - runtime.generation.max_tokens
            - SNIP_SAFETY_BUFFER
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
            usage = LLMUsage.from_dict(session.metadata.get("_last_usage"))
            if usage is not None:
                context_tokens = getattr(usage, "context_tokens", None)
                estimated = (
                    context_tokens
                    if isinstance(context_tokens, int)
                    else usage.input_tokens
                )
                source = "last provider usage"

        ratio = estimated / input_budget if input_budget > 0 else None
        replayable = len(session.get_history(max_messages=0))
        raw_messages = len(session.messages)
        archived = session.last_archived
        unarchived = max(0, raw_messages - archived)
        summary = session_summary_from_metadata(
            session.metadata,
            fallback_last_active=session.updated_at,
        )
        # Session compaction always retains the project-wide recent replay
        # window. Compacting a shorter transcript cannot reduce model input.
        can_compact = (
            not session_key.startswith("dream:")
            and unarchived > 0
            and replayable > MIN_COMPACTED_REPLAY_MESSAGES
        )
        recommendation = self._recommendation(ratio, can_compact)
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
            has_summary=summary is not None,
            can_compact=can_compact,
            pending_compaction=session_key in self.__pending,
            recommendation=recommendation,
        )

    def request_compaction(
        self,
        session_key: str,
        *,
        runtime: LLMRuntime | None = None,
    ) -> dict[str, object]:
        snapshot = self.status(session_key, runtime=runtime)
        if snapshot.pending_compaction:
            return {
                "status": "scheduled",
                "reason": "already_pending",
                "context": snapshot.as_dict(),
            }
        if not snapshot.can_compact:
            return {
                "status": "noop",
                "reason": "not_enough_replayable_history",
                "context": snapshot.as_dict(),
            }
        self.__pending.add(session_key)
        return {
            "status": "scheduled",
            "reason": "next_turn_compact_stage",
            "context": self.status(session_key, runtime=runtime).as_dict(),
        }

    def has_pending(self, session_key: str) -> bool:
        return session_key in self.__pending

    async def compact_now(
        self,
        session_key: str,
        *,
        runtime: LLMRuntime | None = None,
    ) -> dict[str, object]:
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
        try:
            summary = await self.__target.consolidator.compact_idle_session(
                session_key,
                runtime=effective_runtime,
            )
        except Exception:
            return {
                "status": "error",
                "reason": "compaction_failed",
                "before": before.as_dict(),
            }
        after = self.status(session_key, runtime=effective_runtime)
        if summary is None:
            return {
                "status": "error",
                "reason": "compaction_failed",
                "before": before.as_dict(),
                "after": after.as_dict(),
            }
        return {
            "status": "compacted",
            "reason": "manual_compaction",
            "before": before.as_dict(),
            "after": after.as_dict(),
        }

    async def consume_pending(
        self,
        session_key: str,
        *,
        runtime: LLMRuntime | None = None,
    ) -> dict[str, object] | None:
        if session_key not in self.__pending:
            return None
        self.__pending.discard(session_key)
        return await self.compact_now(session_key, runtime=runtime)
