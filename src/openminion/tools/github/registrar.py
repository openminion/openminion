from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openminion.modules.tool.contracts import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)
from openminion.modules.tool.contracts.model_ids import (
    MODEL_GITHUB_COMMIT_FILES,
    MODEL_GITHUB_FETCH_CHECKS,
    MODEL_GITHUB_FETCH_COMMENTS,
    MODEL_GITHUB_FETCH_DIFF,
    MODEL_GITHUB_FETCH_PR,
    MODEL_GITHUB_LIST_PRS,
    MODEL_GITHUB_OPEN_PR,
    MODEL_GITHUB_POST_PR_COMMENT,
    MODEL_GITHUB_POST_PR_REVIEW,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_GITHUB_COMMIT_FILES,
    RUNTIME_GITHUB_FETCH_CHECKS,
    RUNTIME_GITHUB_FETCH_COMMENTS,
    RUNTIME_GITHUB_FETCH_DIFF,
    RUNTIME_GITHUB_FETCH_PR,
    RUNTIME_GITHUB_LIST_PRS,
    RUNTIME_GITHUB_OPEN_PR,
    RUNTIME_GITHUB_POST_PR_COMMENT,
    RUNTIME_GITHUB_POST_PR_REVIEW,
)

from .interfaces import (
    TOOL_GITHUB_COMMIT_FILES,
    TOOL_GITHUB_FETCH_CHECKS,
    TOOL_GITHUB_FETCH_COMMENTS,
    TOOL_GITHUB_FETCH_DIFF,
    TOOL_GITHUB_FETCH_PR,
    TOOL_GITHUB_LIST_PRS,
    TOOL_GITHUB_OPEN_PR,
    TOOL_GITHUB_POST_PR_COMMENT,
    TOOL_GITHUB_POST_PR_REVIEW,
)

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext
    from openminion.modules.tool.registry import ToolRegistry


class GithubRegistrar:
    module_id = "github"
    is_provider_only = False

    def register(
        self, registry: "ToolRegistry", ctx: "ToolRegisterContext | None" = None
    ) -> None:
        del ctx
        from .plugin import register

        register(registry)

    def get_manifest(self, ctx: "ToolRegisterContext") -> Any:
        del ctx
        return ToolBindingManifest(
            module_id=self.module_id,
            model_tools=(
                ModelToolDef(
                    model_tool_id=MODEL_GITHUB_LIST_PRS,
                    description="List open pull requests for a GitHub repo.",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_GITHUB_FETCH_PR,
                    description="Fetch full pull-request metadata for a number.",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_GITHUB_FETCH_DIFF,
                    description="Fetch the diff text for a pull request.",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_GITHUB_FETCH_COMMENTS,
                    description="Fetch issue/review comments for a pull request.",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_GITHUB_FETCH_CHECKS,
                    description="Fetch CI / check-run status for a head SHA.",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_GITHUB_COMMIT_FILES,
                    description="Commit allowlisted smoke files to a GitHub branch.",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_GITHUB_OPEN_PR,
                    description="Open a GitHub pull request from a smoke branch.",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_GITHUB_POST_PR_REVIEW,
                    description="Post a bounded GitHub PR review comment.",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_GITHUB_POST_PR_COMMENT,
                    description="Post a bounded GitHub PR thread comment.",
                    parameters={},
                    aliases=(),
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_GITHUB_LIST_PRS,
                    model_tool_id=MODEL_GITHUB_LIST_PRS,
                    runtime_candidates=(TOOL_GITHUB_LIST_PRS,),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_GITHUB_FETCH_PR,
                    model_tool_id=MODEL_GITHUB_FETCH_PR,
                    runtime_candidates=(TOOL_GITHUB_FETCH_PR,),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_GITHUB_FETCH_DIFF,
                    model_tool_id=MODEL_GITHUB_FETCH_DIFF,
                    runtime_candidates=(TOOL_GITHUB_FETCH_DIFF,),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_GITHUB_FETCH_COMMENTS,
                    model_tool_id=MODEL_GITHUB_FETCH_COMMENTS,
                    runtime_candidates=(TOOL_GITHUB_FETCH_COMMENTS,),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_GITHUB_FETCH_CHECKS,
                    model_tool_id=MODEL_GITHUB_FETCH_CHECKS,
                    runtime_candidates=(TOOL_GITHUB_FETCH_CHECKS,),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_GITHUB_COMMIT_FILES,
                    model_tool_id=MODEL_GITHUB_COMMIT_FILES,
                    runtime_candidates=(TOOL_GITHUB_COMMIT_FILES,),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_GITHUB_OPEN_PR,
                    model_tool_id=MODEL_GITHUB_OPEN_PR,
                    runtime_candidates=(TOOL_GITHUB_OPEN_PR,),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_GITHUB_POST_PR_REVIEW,
                    model_tool_id=MODEL_GITHUB_POST_PR_REVIEW,
                    runtime_candidates=(TOOL_GITHUB_POST_PR_REVIEW,),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_GITHUB_POST_PR_COMMENT,
                    model_tool_id=MODEL_GITHUB_POST_PR_COMMENT,
                    runtime_candidates=(TOOL_GITHUB_POST_PR_COMMENT,),
                ),
            ),
        )


REGISTRAR = GithubRegistrar()
