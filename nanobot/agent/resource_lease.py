"""Process-wide exclusive resource leases for agent tool workflows.

A lease is owned by one logical agent execution, not one individual tool call.
This lets multi-step workflows (for example browser snapshot -> click -> type)
keep a shared stateful resource stable until the owning asyncio task finishes.
"""

from __future__ import annotations

import asyncio
import weakref
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

_DELEGATED_RESOURCE_PARENT: ContextVar[str | None] = ContextVar(
    "nanobot_delegated_resource_parent",
    default=None,
)


@dataclass
class _LoopLeaseState:
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    owners: dict[str, str] = field(default_factory=dict)
    held_by_owner: dict[str, set[str]] = field(default_factory=dict)
    waiters: dict[str, deque[str]] = field(default_factory=dict)
    bound_tasks: dict[str, asyncio.Task[Any]] = field(default_factory=dict)


class ResourceLeaseManager:
    """Serialize exclusive resources across Main and Subagent executions.

    State is isolated per asyncio event loop so unit tests and embedded runtimes
    can safely create independent loops in one process. A lease is re-entrant for
    its current owner. Inline children may atomically inherit a parent's lease
    when ``delegated_resource_parent`` is active.
    """

    def __init__(self) -> None:
        self._states: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop,
            _LoopLeaseState,
        ] = weakref.WeakKeyDictionary()

    def _state(self) -> _LoopLeaseState:
        loop = asyncio.get_running_loop()
        state = self._states.get(loop)
        if state is None:
            state = _LoopLeaseState()
            self._states[loop] = state
        return state

    @staticmethod
    def _normalize(value: str, label: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{label} must be a non-empty string")
        return normalized

    @staticmethod
    def _remove_waiter(state: _LoopLeaseState, resource: str, owner: str) -> None:
        queue = state.waiters.get(resource)
        if queue is None:
            return
        try:
            queue.remove(owner)
        except ValueError:
            pass
        if not queue:
            state.waiters.pop(resource, None)

    @staticmethod
    def _assign_owner(
        state: _LoopLeaseState,
        resource: str,
        owner: str,
        previous: str | None,
    ) -> None:
        if previous is not None and previous != owner:
            previous_resources = state.held_by_owner.get(previous)
            if previous_resources is not None:
                previous_resources.discard(resource)
                if not previous_resources:
                    state.held_by_owner.pop(previous, None)
        state.owners[resource] = owner
        state.held_by_owner.setdefault(owner, set()).add(resource)

    def _bind_owner_task(
        self,
        state: _LoopLeaseState,
        owner: str,
        task: asyncio.Task[Any] | None,
    ) -> None:
        if task is None or state.bound_tasks.get(owner) is task:
            return
        state.bound_tasks[owner] = task
        loop = asyncio.get_running_loop()

        def _release_when_done(_: asyncio.Task[Any]) -> None:
            if loop.is_closed():
                return
            loop.create_task(self.release_owner(owner, expected_task=task))

        task.add_done_callback(_release_when_done)

    async def acquire(
        self,
        resource_key: str,
        owner_key: str,
        *,
        parent_owner_key: str | None = None,
    ) -> None:
        """Acquire one resource and hold it until the owner task finishes.

        ``parent_owner_key`` enables one atomic inline-child transfer. If the
        resource is currently held by that exact parent, the child takes the
        lease without waiting for the parent task to finish. Unrelated waiters
        cannot use this path.
        """
        resource = self._normalize(resource_key, "resource_key")
        owner = self._normalize(owner_key, "owner_key")
        parent = str(parent_owner_key).strip() if parent_owner_key else None
        state = self._state()
        task = asyncio.current_task()

        async with state.condition:
            if state.owners.get(resource) == owner:
                self._bind_owner_task(state, owner, task)
                return

            queue = state.waiters.setdefault(resource, deque())
            if owner not in queue:
                queue.append(owner)

            while True:
                current = state.owners.get(resource)
                if current is not None and parent == current and current != owner:
                    self._remove_waiter(state, resource, owner)
                    self._assign_owner(state, resource, owner, current)
                    self._bind_owner_task(state, owner, task)
                    state.condition.notify_all()
                    return

                queue = state.waiters.get(resource)
                if current is None and queue and queue[0] == owner:
                    queue.popleft()
                    if not queue:
                        state.waiters.pop(resource, None)
                    self._assign_owner(state, resource, owner, None)
                    self._bind_owner_task(state, owner, task)
                    state.condition.notify_all()
                    return

                try:
                    await state.condition.wait()
                except asyncio.CancelledError:
                    self._remove_waiter(state, resource, owner)
                    state.condition.notify_all()
                    raise

    async def release_owner(
        self,
        owner_key: str,
        *,
        expected_task: asyncio.Task[Any] | None = None,
    ) -> None:
        """Release every resource held by one logical owner."""
        owner = self._normalize(owner_key, "owner_key")
        state = self._state()
        async with state.condition:
            if expected_task is not None and state.bound_tasks.get(owner) is not expected_task:
                return
            for resource in list(state.held_by_owner.pop(owner, set())):
                if state.owners.get(resource) == owner:
                    state.owners.pop(resource, None)
            for resource in list(state.waiters):
                self._remove_waiter(state, resource, owner)
            state.bound_tasks.pop(owner, None)
            state.condition.notify_all()

    async def owner_of(self, resource_key: str) -> str | None:
        """Return the current owner for diagnostics/tests."""
        resource = self._normalize(resource_key, "resource_key")
        state = self._state()
        async with state.condition:
            return state.owners.get(resource)

    async def clear(self) -> None:
        """Clear current-loop lease state. Intended for focused tests."""
        state = self._state()
        async with state.condition:
            state.owners.clear()
            state.held_by_owner.clear()
            state.waiters.clear()
            state.bound_tasks.clear()
            state.condition.notify_all()


@contextmanager
def delegated_resource_parent(owner_key: str | None) -> Iterator[None]:
    """Let an inline child inherit resources held by the current parent owner."""
    if not owner_key:
        yield
        return
    token = _DELEGATED_RESOURCE_PARENT.set(owner_key)
    try:
        yield
    finally:
        _DELEGATED_RESOURCE_PARENT.reset(token)


def current_delegated_resource_parent() -> str | None:
    """Return the parent owner copied into an inline child task context."""
    return _DELEGATED_RESOURCE_PARENT.get()


RESOURCE_LEASES = ResourceLeaseManager()
