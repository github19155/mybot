"""Permission capability vocabulary shared by config and runtime."""

from __future__ import annotations

MAIN_SUBJECT = "main"
DREAM_SUBJECT = "dream"
WORK_SUBJECT = "work"
SPECIALIST_PREFIX = "specialist:"

WORKSPACE_READ = "workspace.read"
WORKSPACE_WRITE = "workspace.write"
EXEC = "exec"
WEB = "web"
BROWSER_CONTROL = "browser.control"
VISION_DELEGATE = "vision.delegate"
IMAGE_GENERATE = "image.generate"
EXTERNAL_CALL = "external.call"
CONFIG_READ = "config.read"
CONFIG_WRITE = "config.write"
SPECIALIST_MANAGE = "specialist.manage"
SUBAGENT_MANAGE = "subagent.manage"
AUTOMATION_MANAGE = "automation.manage"
MODEL_MANAGE = "model.manage"
CONTEXT_MANAGE = "context.manage"
PROPOSAL_CREATE = "proposal.create"
GOAL_MUTATE = "goal.mutate"
TOOL_USE = "tool.use"

KNOWN_CAPABILITIES = frozenset({
    WORKSPACE_READ,
    WORKSPACE_WRITE,
    EXEC,
    WEB,
    BROWSER_CONTROL,
    VISION_DELEGATE,
    IMAGE_GENERATE,
    EXTERNAL_CALL,
    CONFIG_READ,
    CONFIG_WRITE,
    SPECIALIST_MANAGE,
    SUBAGENT_MANAGE,
    AUTOMATION_MANAGE,
    MODEL_MANAGE,
    CONTEXT_MANAGE,
    PROPOSAL_CREATE,
    GOAL_MUTATE,
    TOOL_USE,
})

HIGH_RISK_CAPABILITIES = frozenset({
    WORKSPACE_WRITE,
    EXEC,
    BROWSER_CONTROL,
    IMAGE_GENERATE,
    EXTERNAL_CALL,
    CONFIG_WRITE,
    SPECIALIST_MANAGE,
    SUBAGENT_MANAGE,
    AUTOMATION_MANAGE,
    MODEL_MANAGE,
})
