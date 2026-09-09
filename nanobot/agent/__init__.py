"""Agent core module."""

from nanobot.agent.context import ContextBuilder
from nanobot.agent.hook import (
    AgentHook,
    AgentHookContext,
    AgentRunHookContext,
    AgentTurnHookContext,
    AgentTurnHookFactory,
    CompositeHook,
)

# Install the durable extension before AgentLoop imports SubagentManager. The
# extension preserves the existing manager API/runtime and only adds lifecycle
# persistence plus restart-visible history.
from nanobot.agent import subagent as _subagent_module
from nanobot.agent.durable_subagent import DurableSubagentManager

_subagent_module.SubagentManager = DurableSubagentManager

from nanobot.agent.loop import AgentLoop
from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader
from nanobot.agent.subagent import SubagentManager

__all__ = [
    "AgentHook",
    "AgentHookContext",
    "AgentRunHookContext",
    "AgentTurnHookContext",
    "AgentTurnHookFactory",
    "AgentLoop",
    "CompositeHook",
    "ContextBuilder",
    "MemoryStore",
    "SkillsLoader",
    "SubagentManager",
]
