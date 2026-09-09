from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.agent.resource_lease import (
    RESOURCE_LEASES,
    ResourceLeaseManager,
    current_delegated_resource_parent,
    delegated_resource_parent,
    request_resource_owner_key,
    tool_resource_key,
)
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from nanobot.agent.tools.execution import execute_tool_calls
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.providers.base import ToolCallRequest


@tool_parameters({"type": "object", "properties": {}})
class _BrowserLikeTool(Tool):
    config_key = "browser"

    def __init__(self, endpoint: str, events: list[str], label: str) -> None:
        self.runtime = SimpleNamespace(config=SimpleNamespace(cdp_endpoint=endpoint))
        self._events = events
        self._label = label

    @property
    def name(self) -> str:
        return "browser_probe"

    @property
    def description(self) -> str:
        return "test browser lease"

    @property
    def exclusive(self) -> bool:
        return True

    async def execute(self, **kwargs):
        self._events.append(self._label)
        return self._label


async def _call_once(registry: ToolRegistry) -> None:
    await execute_tool_calls(
        registry,
        [ToolCallRequest(id="call", name="browser_probe", arguments={})],
        concurrent=False,
        external_lookup_counts={},
        workspace_violation_counts={},
        hook=AgentHook(),
        context=AgentHookContext(iteration=0, messages=[]),
    )


@pytest.mark.asyncio
async def test_browser_lease_spans_multiple_tool_calls_until_owner_task_finishes() -> None:
    await RESOURCE_LEASES.clear()
    events: list[str] = []
    first_done = asyncio.Event()
    allow_finish = asyncio.Event()
    b_started = asyncio.Event()

    async def owner_a() -> None:
        registry = ToolRegistry()
        registry.register(_BrowserLikeTool("http://browser:9222", events, "a"))
        token = bind_request_context(RequestContext(channel="test", chat_id="a", turn_id="turn-a"))
        try:
            await _call_once(registry)
            first_done.set()
            await allow_finish.wait()
            await _call_once(registry)
        finally:
            reset_request_context(token)

    async def owner_b() -> None:
        registry = ToolRegistry()
        registry.register(_BrowserLikeTool("http://browser:9222", events, "b"))
        token = bind_request_context(RequestContext(channel="test", chat_id="b", turn_id="turn-b"))
        try:
            b_started.set()
            await _call_once(registry)
        finally:
            reset_request_context(token)

    task_a = asyncio.create_task(owner_a())
    await first_done.wait()
    task_b = asyncio.create_task(owner_b())
    await b_started.wait()
    await asyncio.sleep(0)

    assert events == ["a"]
    assert await RESOURCE_LEASES.owner_of("browser:http://browser:9222") == "turn:turn-a"

    allow_finish.set()
    await task_a
    await task_b
    assert events == ["a", "a", "b"]


@pytest.mark.asyncio
async def test_inline_child_can_atomically_take_parent_resource() -> None:
    leases = ResourceLeaseManager()
    resource = "browser:http://browser:9222"
    parent = "turn:parent"
    child = "subagent:session:subagent:child"

    await leases.acquire(resource, parent)
    assert await leases.owner_of(resource) == parent

    async def child_task() -> None:
        with delegated_resource_parent(parent):
            inherited = current_delegated_resource_parent()
            await leases.acquire(resource, child, parent_owner_key=inherited)
            assert await leases.owner_of(resource) == child

    await asyncio.create_task(child_task())
    await asyncio.sleep(0)
    assert await leases.owner_of(resource) is None


@pytest.mark.asyncio
async def test_unrelated_waiter_cannot_steal_resource() -> None:
    leases = ResourceLeaseManager()
    resource = "browser:http://browser:9222"
    await leases.acquire(resource, "owner-a")
    entered = asyncio.Event()

    async def waiter() -> None:
        entered.set()
        await leases.acquire(resource, "owner-b")

    task = asyncio.create_task(waiter())
    await entered.wait()
    await asyncio.sleep(0)
    assert not task.done()
    assert await leases.owner_of(resource) == "owner-a"

    await leases.release_owner("owner-a")
    await task
    assert await leases.owner_of(resource) == "owner-b"
    await leases.release_owner("owner-b")


@pytest.mark.asyncio
async def test_different_browser_endpoints_do_not_block_each_other() -> None:
    leases = ResourceLeaseManager()
    await leases.acquire("browser:http://one:9222", "a")
    await leases.acquire("browser:http://two:9222", "b")
    assert await leases.owner_of("browser:http://one:9222") == "a"
    assert await leases.owner_of("browser:http://two:9222") == "b"
    await leases.release_owner("a")
    await leases.release_owner("b")


def test_resource_key_uses_browser_cdp_endpoint() -> None:
    tool = _BrowserLikeTool("http://browser:9222", [], "x")
    assert tool_resource_key(tool) == "browser:http://browser:9222"


def test_request_owner_prefers_subagent_then_turn_then_session() -> None:
    assert request_resource_owner_key(SimpleNamespace(
        exec_owner_session_key="s:subagent:abc",
        turn_id="turn-1",
        session_key="s",
    )) == "subagent:s:subagent:abc"
    assert request_resource_owner_key(SimpleNamespace(
        exec_owner_session_key=None,
        turn_id="turn-1",
        session_key="s",
    )) == "turn:turn-1"
    assert request_resource_owner_key(SimpleNamespace(
        exec_owner_session_key=None,
        turn_id=None,
        session_key="s",
    )) == "session:s"
