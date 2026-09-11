"""Canonical permission policy configuration."""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator

from nanobot.config_base import Base
from nanobot.permission_types import GOAL_MUTATE, KNOWN_CAPABILITIES


class PermissionPolicyConfig(Base):
    """Granted capabilities and the hard ceiling for one subject class."""

    capabilities: list[str] = Field(default_factory=list)
    ceiling: list[str] = Field(default_factory=list)

    @field_validator("capabilities", "ceiling")
    @classmethod
    def _normalize_capabilities(cls, value: list[str]) -> list[str]:
        normalized = [str(item).strip().lower() for item in value if str(item).strip()]
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def _validate_policy(self) -> "PermissionPolicyConfig":
        capabilities = set(self.capabilities)
        ceiling = set(self.ceiling)
        unknown = (capabilities | ceiling) - KNOWN_CAPABILITIES
        if unknown:
            raise ValueError("unknown capabilities: " + ", ".join(sorted(unknown)))
        excess = capabilities - ceiling
        if excess:
            raise ValueError(
                "granted capabilities exceed privilege ceiling: "
                + ", ".join(sorted(excess))
            )
        return self


def _policy(*capabilities: str, ceiling: tuple[str, ...] | None = None) -> PermissionPolicyConfig:
    caps = list(capabilities)
    return PermissionPolicyConfig(
        capabilities=caps,
        ceiling=list(ceiling or tuple(caps)),
    )


_MAIN_CAPABILITIES = (
    "workspace.read",
    "workspace.write",
    "exec",
    "web",
    "browser.control",
    "vision.delegate",
    "image.generate",
    "external.call",
    "config.read",
    "config.write",
    "specialist.manage",
    "subagent.manage",
    "automation.manage",
    "model.manage",
    "context.manage",
    "proposal.create",
    "tool.use",
)


class PermissionConfig(Base):
    """Single persistent permission-policy authority for nanobot."""

    main: PermissionPolicyConfig = Field(
        default_factory=lambda: _policy(
            *_MAIN_CAPABILITIES,
            ceiling=(*_MAIN_CAPABILITIES, GOAL_MUTATE),
        )
    )
    dream: PermissionPolicyConfig = Field(
        default_factory=lambda: _policy(
            "workspace.read",
            "config.read",
            "proposal.create",
        )
    )
    work: PermissionPolicyConfig = Field(
        default_factory=lambda: _policy(
            "workspace.read",
            "workspace.write",
            "exec",
            "web",
            "browser.control",
            "vision.delegate",
            "tool.use",
        )
    )
    specialist_default: PermissionPolicyConfig = Field(
        default_factory=lambda: _policy(
            "workspace.read",
            "web",
            ceiling=(
                "workspace.read",
                "workspace.write",
                "exec",
                "web",
                "browser.control",
                "vision.delegate",
                "tool.use",
            ),
        )
    )
    specialists: dict[str, PermissionPolicyConfig] = Field(
        default_factory=lambda: {
            "general": _policy(
                "workspace.read", "workspace.write", "exec", "web",
                "browser.control", "vision.delegate", "tool.use",
            ),
            "researcher": _policy("workspace.read", "web", "vision.delegate", "tool.use"),
            "planner": _policy("workspace.read", "web", "tool.use"),
            "coder": _policy("workspace.read", "workspace.write", "exec", "web", "tool.use"),
            "debugger": _policy("workspace.read", "workspace.write", "exec", "web", "tool.use"),
            "tester": _policy("workspace.read", "workspace.write", "exec", "web", "tool.use"),
            "writer": _policy("workspace.read", "workspace.write", "web", "tool.use"),
            "analyst": _policy("workspace.read", "workspace.write", "exec", "web", "tool.use"),
        }
    )
    require_user_approval: list[str] = Field(
        default_factory=lambda: [
            "config.write",
            "specialist.manage",
            "browser.control",
            "external.call",
        ]
    )

    @field_validator("require_user_approval")
    @classmethod
    def _normalize_approval_capabilities(cls, value: list[str]) -> list[str]:
        normalized = [str(item).strip().lower() for item in value if str(item).strip()]
        unknown = set(normalized) - KNOWN_CAPABILITIES
        if unknown:
            raise ValueError("unknown approval capabilities: " + ", ".join(sorted(unknown)))
        return list(dict.fromkeys(normalized))
