"""Memory storage, transcript archiving, and session checkpoint consolidation."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import weakref
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator, cast

from loguru import logger

from nanobot.llm_usage.context import llm_usage_source
from nanobot.providers.base import ProviderCallContext, ProviderConversationState
from nanobot.runtime_context import public_history_messages
from nanobot.session.manager import MIN_COMPACTED_REPLAY_MESSAGES, Session, SessionManager
from nanobot.session.summary import session_summary_from_metadata
from nanobot.utils.helpers import (
    content_with_media_breadcrumbs,
    ensure_dir,
    estimate_prompt_tokens_chain,
    strip_think,
    truncate_text,
    truncate_text_to_tokens,
)
from nanobot.utils.prompt_templates import render_template
from nanobot.utils.workspace_prompts import (
    WORKSPACE_PROMPT_MAX_CHARS,
    has_workspace_prompt_override,
    load_workspace_prompt_override,
    workspace_prompt_file,
)

if TYPE_CHECKING:
    from nanobot.utils.llm_runtime import LLMRuntime


_RAW_ARCHIVE_MAX_CHARS = 16_000
_HISTORY_ENTRY_HARD_CAP = 64_000


class MemoryStore:
    """Durable Memory and append-only history storage.

    Dream only consumes the history/cursor/prompt surface. Mutation authority for
    canonical Memory belongs to normal runtime code, not to Dream.
    """

    _DEFAULT_MAX_HISTORY = 1000

    def __init__(self, workspace: Path, max_history_entries: int = _DEFAULT_MAX_HISTORY):
        self.workspace = workspace
        self.max_history_entries = max_history_entries
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "history.jsonl"
        self.soul_file = workspace / "SOUL.md"
        self.user_file = workspace / "USER.md"
        self._cursor_file = self.memory_dir / ".cursor"
        self._dream_cursor_file = self.memory_dir / ".dream_cursor"
        self._corruption_logged = False
        self._malformed_entry_logged = False
        self._oversize_logged = False
        self._dream_prompt_oversize_logged = False
        self._append_lock = threading.Lock()

    @staticmethod
    def read_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def read_memory(self) -> str:
        return self.read_file(self.memory_file)

    def write_memory(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    def read_soul(self) -> str:
        return self.read_file(self.soul_file)

    def write_soul(self, content: str) -> None:
        self.soul_file.write_text(content, encoding="utf-8")

    def read_user(self) -> str:
        return self.read_file(self.user_file)

    def write_user(self, content: str) -> None:
        self.user_file.write_text(content, encoding="utf-8")

    def get_memory_context(self) -> str:
        long_term = self.read_memory()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    def _normalize_history_entry(
        self,
        entry: str,
        *,
        max_chars: int | None = None,
    ) -> str:
        limit = max_chars if max_chars is not None else _HISTORY_ENTRY_HARD_CAP
        content = strip_think(entry.rstrip())
        if len(content) > limit:
            if not self._oversize_logged:
                self._oversize_logged = True
                logger.warning(
                    "history entry exceeds {} chars ({}); truncating; further occurrences suppressed",
                    limit,
                    len(content),
                )
            content = truncate_text(content, limit)
        return content

    def append_history(
        self,
        entry: str,
        *,
        max_chars: int | None = None,
        session_key: str | None = None,
    ) -> int:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        raw = entry.rstrip()
        content = self._normalize_history_entry(entry, max_chars=max_chars)
        with self._append_lock:
            cursor = self._next_cursor()
            if raw and not content:
                logger.debug(
                    "history entry {} stripped to empty; persisting empty content",
                    cursor,
                )
            record: dict[str, Any] = {
                "cursor": cursor,
                "timestamp": ts,
                "content": content,
            }
            if session_key:
                record["session_key"] = session_key
            with self.history_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._cursor_file.write_text(str(cursor), encoding="utf-8")
        return cursor

    @staticmethod
    def _valid_cursor(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    @staticmethod
    def _valid_history_payload(entry: dict[str, Any]) -> bool:
        if not isinstance(entry.get("timestamp"), str):
            return False
        if not isinstance(entry.get("content"), str):
            return False
        session_key = entry.get("session_key")
        return session_key is None or isinstance(session_key, str)

    def _iter_valid_entries(self) -> Iterator[tuple[dict[str, Any], int]]:
        poisoned: Any = None
        malformed_cursor: int | None = None
        for entry in self._read_entries():
            raw = entry.get("cursor")
            if raw is None:
                continue
            cursor = self._valid_cursor(raw)
            if cursor is None:
                poisoned = raw
                continue
            if not self._valid_history_payload(entry):
                malformed_cursor = cursor
                continue
            yield entry, cursor
        if poisoned is not None and not self._corruption_logged:
            self._corruption_logged = True
            logger.warning(
                "history.jsonl contains invalid cursor {!r}; dropping it; further occurrences suppressed",
                poisoned,
            )
        if malformed_cursor is not None and not self._malformed_entry_logged:
            self._malformed_entry_logged = True
            logger.warning(
                "history.jsonl contains malformed entry at cursor {}; dropping it; further occurrences suppressed",
                malformed_cursor,
            )

    def _read_cursor_counter(self) -> int | None:
        if not self._cursor_file.exists():
            return None
        with suppress(ValueError, OSError):
            cursor = int(self._cursor_file.read_text(encoding="utf-8").strip())
            if cursor >= 0:
                return cursor
        return None

    def _next_cursor(self) -> int:
        cursor_counter = self._read_cursor_counter()
        last = self._read_last_entry() or {}
        last_cursor = self._valid_cursor(last.get("cursor"))
        if cursor_counter is not None:
            if last_cursor is not None:
                return max(cursor_counter, last_cursor) + 1
            max_history_cursor = max((c for _, c in self._iter_valid_entries()), default=0)
            return max(cursor_counter, max_history_cursor) + 1
        if last_cursor is not None:
            return last_cursor + 1
        return max((c for _, c in self._iter_valid_entries()), default=0) + 1

    def read_unprocessed_history(self, since_cursor: int) -> list[dict[str, Any]]:
        return [entry for entry, cursor in self._iter_valid_entries() if cursor > since_cursor]

    def compact_history(self) -> None:
        if self.max_history_entries <= 0:
            return
        entries = self._read_entries()
        if len(entries) <= self.max_history_entries:
            return
        last_dream_cursor = self.get_last_dream_cursor()
        first_unprocessed = next(
            (
                index
                for index, entry in enumerate(entries)
                if (
                    (cursor := self._valid_cursor(entry.get("cursor"))) is not None
                    and cursor > last_dream_cursor
                )
            ),
            len(entries),
        )
        keep_from = min(len(entries) - self.max_history_entries, first_unprocessed)
        kept = entries[keep_from:]
        if len(kept) > self.max_history_entries:
            logger.warning(
                "History compaction retained {} pending entries beyond configured limit {}",
                len(kept),
                self.max_history_entries,
            )
        self._write_entries(kept)

    def _read_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        with suppress(FileNotFoundError):
            with self.history_file.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed: object = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, dict):
                        entries.append(cast(dict[str, Any], parsed))
        return entries

    def _read_last_entry(self) -> dict[str, Any] | None:
        try:
            with self.history_file.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                if size == 0:
                    return None
                read_size = min(size, 4096)
                handle.seek(size - read_size)
                data = handle.read().decode("utf-8")
                lines = [line for line in data.split("\n") if line.strip()]
                if not lines:
                    return None
                parsed: object = json.loads(lines[-1])
                return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else None
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _write_entries(self, entries: list[dict[str, Any]]) -> None:
        tmp_path = self.history_file.with_suffix(self.history_file.suffix + ".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as handle:
                for entry in entries:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.history_file)
            with suppress(PermissionError):
                fd = os.open(str(self.history_file.parent), os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    def get_last_dream_cursor(self) -> int:
        if self._dream_cursor_file.exists():
            with suppress(ValueError, OSError):
                return int(self._dream_cursor_file.read_text(encoding="utf-8").strip())
        return 0

    def set_last_dream_cursor(self, cursor: int) -> None:
        self._dream_cursor_file.write_text(str(cursor), encoding="utf-8")

    @property
    def dream_prompt_file(self) -> Path:
        return workspace_prompt_file(self.workspace, "dream")

    def has_dream_prompt_override(self) -> bool:
        return has_workspace_prompt_override(self.dream_prompt_file)

    @staticmethod
    def default_dream_prompt() -> str:
        return render_template("agent/dream.md", strip=True)

    def _dream_template(self) -> str:
        text, original_chars = load_workspace_prompt_override(self.dream_prompt_file)
        if text is not None:
            if original_chars > WORKSPACE_PROMPT_MAX_CHARS and not self._dream_prompt_oversize_logged:
                self._dream_prompt_oversize_logged = True
                logger.warning(
                    "workspace Dream prompt exceeds {} chars ({}); truncating; further occurrences suppressed",
                    WORKSPACE_PROMPT_MAX_CHARS,
                    original_chars,
                )
            return text
        return self.default_dream_prompt()

    def build_dream_prompt(self, *, max_entries: int = 20) -> tuple[str, int] | None:
        last_cursor = self.get_last_dream_cursor()
        entries = self.read_unprocessed_history(since_cursor=last_cursor)
        if not entries:
            return None
        batch = entries[:max_entries]
        history_text = "\n".join(
            f"[{entry['timestamp']}] {truncate_text(entry['content'], 1000)}"
            for entry in batch
        )
        return (
            f"{self._dream_template()}\n\n## Conversation History\n{history_text}",
            batch[-1]["cursor"],
        )

    @staticmethod
    def _format_messages(messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for message in messages:
            content = content_with_media_breadcrumbs(
                message.get("role"),
                message.get("content", ""),
                message.get("media"),
            )
            if not content:
                continue
            tools_used = message.get("tools_used")
            tools = (
                f" [tools: {', '.join(cast(list[str], tools_used))}]"
                if tools_used else ""
            )
            raw_timestamp = message.get("timestamp")
            timestamp = str(raw_timestamp) if raw_timestamp is not None else "?"
            role = str(message.get("role") or "unknown")
            lines.append(f"[{timestamp[:16]}] {role.upper()}{tools}: {content}")
        return "\n".join(lines)

    def raw_archive(
        self,
        messages: list[dict[str, Any]],
        *,
        max_chars: int | None = None,
        session_key: str | None = None,
    ) -> str:
        checkpoint = self._build_raw_checkpoint(messages, max_chars=max_chars)
        self.append_history(checkpoint, session_key=session_key)
        logger.warning("Memory consolidation degraded: raw-archived {} messages", len(messages))
        return checkpoint

    def _build_raw_checkpoint(
        self,
        messages: list[dict[str, Any]],
        *,
        max_chars: int | None = None,
    ) -> str:
        limit = max_chars if max_chars is not None else _RAW_ARCHIVE_MAX_CHARS
        checkpoint = (
            f"[RAW] {len(messages)} messages\n"
            f"{self._format_messages(public_history_messages(messages))}"
        )
        return self._normalize_history_entry(checkpoint, max_chars=limit)


class MemoryArchiver:
    """Write transcript checkpoints to the Memory ingestion journal."""

    def __init__(
        self,
        store: MemoryStore,
        build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        resolve_prompt_context: Callable[[Session], tuple[str | None, Path | None]] | None = None,
    ) -> None:
        self.store = store
        self._build_messages = build_messages
        self._get_tool_definitions = get_tool_definitions
        self._resolve_prompt_context = resolve_prompt_context

    def _raw_checkpoint(
        self,
        messages: list[dict[str, Any]],
        *,
        session_key: str,
        previous_summary: str | None,
        max_tokens: int,
    ) -> str:
        raw = self.store.raw_archive(messages, session_key=session_key)
        return self._combine_raw_checkpoint(
            raw,
            previous_summary=previous_summary,
            max_tokens=max_tokens,
        )

    @staticmethod
    def _combine_raw_checkpoint(
        raw: str,
        *,
        previous_summary: str | None,
        max_tokens: int,
    ) -> str:
        token_limit = max(1, max_tokens)
        if not previous_summary:
            return truncate_text_to_tokens(raw, token_limit)
        combined = (
            "[Previous archived context]\n"
            f"{previous_summary}\n\n"
            "[Newly archived raw context]\n"
            f"{raw}"
        )
        bounded = truncate_text_to_tokens(combined, token_limit)
        if bounded == combined:
            return combined
        section_limit = max(1, (token_limit - 32) // 2)
        return truncate_text_to_tokens(
            "[Previous archived context]\n"
            f"{truncate_text_to_tokens(previous_summary, section_limit)}\n\n"
            "[Newly archived raw context]\n"
            f"{truncate_text_to_tokens(raw, section_limit)}",
            token_limit,
        )

    async def archive(
        self,
        source_messages: list[dict[str, Any]],
        *,
        runtime: LLMRuntime,
        session_key: str,
        history: list[dict[str, Any]],
        request_tools: list[dict[str, Any]],
        previous_summary: str | None = None,
        input_token_budget: int | None = None,
        fallback_max_tokens: int | None = None,
        provider_state: ProviderConversationState | None = None,
    ) -> str | None:
        if not source_messages:
            return None

        def raw_fallback() -> str:
            return self._raw_checkpoint(
                source_messages,
                session_key=session_key,
                previous_summary=previous_summary,
                max_tokens=(
                    fallback_max_tokens
                    if fallback_max_tokens is not None
                    else runtime.generation.max_tokens
                ),
            )

        prompt = render_template(
            "agent/consolidator_archive.md",
            strip=True,
            archive_count=len(source_messages),
        )
        prompt_message = {"role": "user", "content": prompt}
        provider_context = None
        call_tools = request_tools
        if provider_state is not None:
            if not runtime.provider.can_resume_conversation_state(provider_state, runtime.model):
                return raw_fallback()
            instruction_messages: list[dict[str, Any]] = []
            for message in history:
                if message.get("role") not in {"system", "developer"}:
                    break
                instruction_messages.append(dict(message))
            request_messages = [*instruction_messages, prompt_message]
            provider_context = ProviderCallContext(
                conversation_state=provider_state.with_pending_messages([
                    *provider_state.pending_messages,
                    prompt_message,
                ]),
                context_window_tokens=runtime.context_window_tokens,
                session_id=session_key,
            )
            call_tools = []
        else:
            request_messages = [*map(dict, history), prompt_message]

        if input_token_budget is not None and provider_context is None:
            estimated, source = estimate_prompt_tokens_chain(
                runtime.provider,
                runtime.model,
                request_messages,
                call_tools,
            )
            if input_token_budget <= 0 or estimated > input_token_budget:
                logger.debug(
                    "Memory archive input does not fit for {}: {}/{} via {}; raw-dumping",
                    session_key,
                    estimated,
                    input_token_budget,
                    source,
                )
                return raw_fallback()

        try:
            with llm_usage_source("system"):
                response = await runtime.provider.chat_with_retry(
                    model=runtime.model,
                    messages=request_messages,
                    tools=call_tools,
                    temperature=runtime.generation.temperature,
                    max_tokens=runtime.generation.max_tokens,
                    reasoning_effort=runtime.generation.reasoning_effort,
                    provider_context=provider_context,
                )
        except Exception:
            logger.warning("Memory archive provider call failed, raw-dumping to history")
            return raw_fallback()
        if response.finish_reason in {"error", "length"}:
            logger.warning(
                "Memory archive provider did not complete ({}), raw-dumping to history",
                response.finish_reason,
            )
            return raw_fallback()
        if response.has_tool_calls is True:
            logger.warning("Memory archive provider returned tool calls, raw-dumping to history")
            return raw_fallback()
        summary = response.content
        if not summary or not summary.strip():
            logger.warning("Memory archive provider returned no summary, raw-dumping to history")
            return raw_fallback()
        summary = self.store._normalize_history_entry(summary)
        if not summary:
            logger.warning("Memory archive provider summary was not safe to replay, raw-dumping")
            return raw_fallback()
        if summary == "(nothing)":
            return "(nothing)"
        self.store.append_history(summary, session_key=session_key)
        return summary

    async def archive_session(
        self,
        session: Session,
        *,
        archive_end: int,
        runtime: LLMRuntime,
        input_token_budget: int,
    ) -> str | None:
        messages = list(session.messages[session.last_archived:archive_end])
        if not messages:
            return None
        session_summary = session_summary_from_metadata(
            session.metadata,
            fallback_last_active=session.updated_at,
        )
        previous_summary = session_summary["text"] if session_summary else None

        if input_token_budget <= 0:
            logger.debug("Memory archive has no safe input budget for {}; raw-dumping", session.key)
            return self._raw_checkpoint(
                messages,
                session_key=session.key,
                previous_summary=previous_summary,
                max_tokens=runtime.generation.max_tokens,
            )
        prefix = Session(
            key=session.key,
            messages=list(session.messages[:archive_end]),
            last_consolidated=session.last_archived,
        )
        history = prefix.get_history(max_tokens=input_token_budget)
        archive_history = Session(key=session.key, messages=messages).get_history()
        if not archive_history or history[-len(archive_history):] != archive_history:
            logger.debug("Memory archive cannot replay full chunk for {}; raw-dumping", session.key)
            return self._raw_checkpoint(
                messages,
                session_key=session.key,
                previous_summary=previous_summary,
                max_tokens=runtime.generation.max_tokens,
            )
        channel = session.key.split(":", 1)[0] if ":" in session.key else None
        workspace: Path | None = None
        if self._resolve_prompt_context is not None:
            channel, workspace = self._resolve_prompt_context(session)
        history_messages = self._build_messages(
            history=history,
            current_message=None,
            channel=channel,
            session_summary=session_summary,
            workspace=workspace,
        )
        return await self.archive(
            messages,
            runtime=runtime,
            session_key=session.key,
            history=history_messages,
            request_tools=self._get_tool_definitions(),
            previous_summary=previous_summary,
            input_token_budget=input_token_budget,
        )


class Consolidator:
    """Coordinate session Memory checkpoints through MemoryArchiver."""

    _SAFETY_BUFFER = 1024

    def __init__(
        self,
        store: MemoryStore,
        sessions: SessionManager,
        build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        resolve_prompt_context: Callable[[Session], tuple[str | None, Path | None]] | None = None,
    ):
        self.store = store
        self.sessions = sessions
        self._build_messages = build_messages
        self._get_tool_definitions = get_tool_definitions
        self.archiver = MemoryArchiver(
            store=store,
            build_messages=build_messages,
            get_tool_definitions=get_tool_definitions,
            resolve_prompt_context=resolve_prompt_context,
        )
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def get_lock(self, session_key: str) -> asyncio.Lock:
        return self._locks.setdefault(session_key, asyncio.Lock())

    async def summarize_transcript(
        self,
        accepted_messages: list[dict[str, Any]],
        previous_summary: str | None,
        *,
        runtime: LLMRuntime,
        session_key: str,
        tools: list[dict[str, Any]],
        provider_state: ProviderConversationState | None = None,
    ) -> str | None:
        source_messages = [
            dict(message)
            for message in accepted_messages
            if message.get("role") != "system"
        ]
        if not source_messages:
            return None

        max_output_tokens = max(0, runtime.generation.max_tokens)
        input_token_budget = runtime.context_window_tokens - max_output_tokens
        checkpoint_tokens = min(
            max_output_tokens,
            max(1, (input_token_budget - self._SAFETY_BUFFER) // 2),
        )
        summary = await self.archiver.archive(
            source_messages,
            runtime=runtime,
            session_key=session_key,
            history=accepted_messages,
            request_tools=tools,
            previous_summary=previous_summary,
            input_token_budget=input_token_budget,
            fallback_max_tokens=max(1, checkpoint_tokens),
            provider_state=provider_state,
        )
        if summary == "(nothing)":
            summary = self.archiver._raw_checkpoint(
                source_messages,
                session_key=session_key,
                previous_summary=previous_summary,
                max_tokens=max_output_tokens,
            )
        if summary is None:
            return None
        return truncate_text_to_tokens(summary, max(1, max_output_tokens))

    async def summarize_provider_compaction(
        self,
        state: ProviderConversationState,
        fallback_messages: list[dict[str, Any]],
        previous_summary: str | None,
        *,
        runtime: LLMRuntime,
        session_key: str,
        tools: list[dict[str, Any]],
    ) -> str | None:
        return await self.summarize_transcript(
            fallback_messages,
            previous_summary,
            runtime=runtime,
            session_key=session_key,
            tools=tools,
            provider_state=state,
        )

    @staticmethod
    def _full_replay_history(session: Session) -> list[dict[str, Any]]:
        return [] if not session.messages else session.get_history()

    @staticmethod
    def _set_last_summary(
        session: Session,
        summary: str,
        *,
        last_active: datetime | None = None,
    ) -> None:
        if summary != "(nothing)":
            session.metadata["_last_summary"] = {
                "text": summary,
                "last_active": (last_active or session.updated_at).isoformat(),
            }

    def estimate_session_prompt_tokens(
        self,
        session: Session,
        *,
        runtime: LLMRuntime,
    ) -> tuple[int, str]:
        history = self._full_replay_history(session)
        channel = session.key.split(":", 1)[0] if ":" in session.key else None
        summary = session_summary_from_metadata(
            session.metadata,
            fallback_last_active=session.updated_at,
        )
        probe_messages = self._build_messages(
            history=history,
            current_message="[token-probe]",
            channel=channel,
            session_summary=summary,
        )
        return estimate_prompt_tokens_chain(
            runtime.provider,
            runtime.model,
            probe_messages,
            self._get_tool_definitions(),
        )

    def _input_token_budget(self, runtime: LLMRuntime) -> int:
        return runtime.context_window_tokens - runtime.generation.max_tokens - self._SAFETY_BUFFER

    async def archive_session(
        self,
        session: Session,
        *,
        archive_end: int,
        runtime: LLMRuntime,
    ) -> str | None:
        return await self.archiver.archive_session(
            session,
            archive_end=archive_end,
            runtime=runtime,
            input_token_budget=self._input_token_budget(runtime),
        )

    async def compact_idle_session(
        self,
        session_key: str,
        *,
        runtime: LLMRuntime,
    ) -> str | None:
        lock = self.get_lock(session_key)
        async with lock:
            self.sessions.invalidate(session_key)
            session = self.sessions.get_or_create(session_key)
            archive_start = session.last_archived
            messages_to_archive = list(session.messages[archive_start:])
            if not messages_to_archive:
                return ""

            last_active = session.updated_at
            archive_end = archive_start + len(messages_to_archive)
            summary = await self.archive_session(
                session,
                archive_end=archive_end,
                runtime=runtime,
            )
            if summary is None:
                return None

            self._set_last_summary(session, summary, last_active=last_active)
            session.last_archived = archive_end
            self.sessions.save(session)

            visible = session.get_history(
                max_messages=MIN_COMPACTED_REPLAY_MESSAGES,
                extend_to_user=True,
            )
            logger.info(
                "Idle-session compact for {}: archived={}, visible={}, retained={}, summary={}",
                session_key,
                len(messages_to_archive),
                len(visible),
                len(session.messages),
                bool(summary),
            )
            return summary
