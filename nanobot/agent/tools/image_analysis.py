"""Image analysis tool for text-only models."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ConfigDict, Field

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.schema import ArraySchema, StringSchema, tool_parameters_schema
from nanobot.config.paths import get_media_dir
from nanobot.config_base import Base
from nanobot.model_domain import ModelConfig, require_model_capability
from nanobot.security.workspace_access import current_tool_workspace
from nanobot.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path
from nanobot.utils.helpers import detect_image_mime

if TYPE_CHECKING:
    from nanobot.agent.tools.context import ToolContext
    from nanobot.providers.factory import ProviderSnapshot


class ImageAnalysisToolConfig(Base):
    """Configuration for the fallback image analysis tool."""

    model_config = ConfigDict(**Base.model_config, extra="forbid")

    enabled: bool = True
    model_id: str | None = None
    max_image_mb: float = Field(default=10.0, gt=0, le=50)
    max_images: int = Field(default=4, ge=1, le=8)


@tool_parameters(
    tool_parameters_schema(
        image_paths=ArraySchema(
            StringSchema(
                "Local image path from the current message or nanobot media directory.",
                min_length=1,
            ),
            description="One or more images to inspect.",
            min_items=1,
            max_items=8,
        ),
        prompt=StringSchema(
            "What to inspect or extract from the image(s).",
            min_length=1,
        ),
        model_id=StringSchema(
            "Optional vision-capable canonical model_id. Overrides tools.imageAnalysis.modelId.",
            min_length=1,
        ),
        required=["image_paths", "prompt"],
    )
)
class ImageAnalysisTool(Tool):
    """Inspect local images through a separately configured vision-capable model."""

    config_key = "image_analysis"
    _scopes = {"core", "subagent"}

    @classmethod
    def config_cls(cls):
        return ImageAnalysisToolConfig

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.image_analysis.enabled

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        root_config = (
            ctx.model_management.config_snapshot()
            if ctx.model_management is not None
            else None
        )
        models = ctx.models or (root_config.models if root_config is not None else {})
        return cls(
            workspace=ctx.workspace,
            config=ctx.config.image_analysis,
            models=models,
            provider_snapshot_loader=ctx.provider_snapshot_loader,
        )

    def __init__(
        self,
        *,
        workspace: str | Path,
        config: ImageAnalysisToolConfig,
        models: Mapping[str, ModelConfig] | None = None,
        provider_snapshot_loader: Any = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser()
        self.config = config
        self.models = models or {}
        self.provider_snapshot_loader = provider_snapshot_loader

    @property
    def name(self) -> str:
        return "image_analyze"

    @property
    def description(self) -> str:
        return (
            "Analyze one or more local images with a configured vision-capable model. "
            "Use this when the current model cannot see images. Do not use it when the "
            "current model already has native vision support."
        )

    def _resolve_image(self, value: str) -> tuple[str, str, bytes]:
        access = current_tool_workspace(self.workspace, restrict_to_workspace=True)
        workspace = access.project_path or self.workspace
        try:
            resolved = resolve_allowed_path(
                value,
                workspace=workspace,
                allowed_root=access.allowed_root,
                extra_allowed_roots=(
                    [get_media_dir()] if access.allowed_root is not None else None
                ),
                strict=True,
            )
        except WorkspaceBoundaryError as exc:
            raise ValueError(
                "image_paths must be inside the workspace or nanobot media directory"
            ) from exc
        except OSError as exc:
            raise ValueError(f"image not found: {value}") from exc
        if not resolved.is_file():
            raise ValueError(f"image is not a file: {value}")
        raw = resolved.read_bytes()
        limit = int(self.config.max_image_mb * 1024 * 1024)
        if len(raw) > limit:
            raise ValueError(
                f"image exceeds tools.imageAnalysis.maxImageMb ({self.config.max_image_mb:g} MB): {value}"
            )
        mime = detect_image_mime(raw)
        if mime is None:
            raise ValueError(f"unsupported image format: {value}")
        return str(resolved), mime, raw

    async def execute(
        self,
        image_paths: list[str],
        prompt: str,
        model_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        if not isinstance(image_paths, list):
            return ToolResult.error("Error: image_paths must be an array.")
        if not isinstance(prompt, str) or not prompt.strip():
            return ToolResult.error("Error: prompt must not be blank.")
        paths = [
            value.strip()
            for value in image_paths
            if isinstance(value, str) and value.strip()
        ]
        if len(paths) > self.config.max_images:
            return ToolResult.error(
                "Error: image_paths exceeds tools.imageAnalysis.maxImages "
                f"({self.config.max_images})"
            )
        if self.provider_snapshot_loader is None:
            return ToolResult.error(
                "Error: no image analysis runtime loader is configured."
            )

        requested_model_id = model_id if isinstance(model_id, str) else None
        selected_model_id = (requested_model_id or self.config.model_id or "").strip()
        if not selected_model_id:
            return ToolResult.error(
                "Error: no image analysis model_id is configured. "
                "Pass model_id or set tools.imageAnalysis.modelId."
            )

        try:
            require_model_capability(self.models, selected_model_id, "vision")
            images = [self._resolve_image(value) for value in paths]
            if not images:
                return ToolResult.error("Error: at least one image path is required.")
            snapshot: ProviderSnapshot = self.provider_snapshot_loader(
                model_id=selected_model_id,
            )
        except (KeyError, ValueError, OSError) as exc:
            return ToolResult.error(f"Error: {exc}")

        content: list[dict[str, Any]] = []
        for path, mime, raw in images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64,{base64.b64encode(raw).decode()}",
                    },
                    "_meta": {"path": path},
                }
            )
        content.append({"type": "text", "text": prompt})

        generation = snapshot.generation
        try:
            response = await snapshot.provider.chat_with_retry(
                messages=[
                    {
                        "role": "user",
                        "content": content,
                    }
                ],
                tools=None,
                model=snapshot.model,
                max_tokens=generation.max_tokens if generation is not None else None,
                temperature=generation.temperature if generation is not None else None,
                reasoning_effort=(
                    generation.reasoning_effort if generation is not None else None
                ),
                retry_mode="standard",
            )
        except Exception as exc:  # noqa: BLE001 - tool errors stay in-band
            return ToolResult.error(f"Error analyzing image: {exc}")

        if response.finish_reason == "error":
            return ToolResult.error(
                response.content or "Error: image analysis model request failed."
            )
        if response.content is None or not response.content.strip():
            return ToolResult.error("Error: image analysis model returned no text.")
        return response.content
