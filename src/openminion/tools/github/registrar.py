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
            model_tools=_github_model_tools(),
            runtime_bindings=_github_runtime_bindings(),
        )


def _github_model_tools() -> tuple[ModelToolDef, ...]:
    return (
        ModelToolDef(MODEL_GITHUB_LIST_PRS, "List open pull requests for a GitHub repo.", {}, ()),
        ModelToolDef(MODEL_GITHUB_FETCH_PR, "Fetch full pull-request metadata for a number.", {}, ()),
        ModelToolDef(MODEL_GITHUB_FETCH_DIFF, "Fetch the diff text for a pull request.", {}, ()),
        ModelToolDef(MODEL_GITHUB_FETCH_COMMENTS, "Fetch issue/review comments for a pull request.", {}, ()),
        ModelToolDef(MODEL_GITHUB_FETCH_CHECKS, "Fetch CI / check-run status for a head SHA.", {}, ()),
        ModelToolDef(MODEL_GITHUB_COMMIT_FILES, "Commit allowlisted smoke files to a GitHub branch.", {}, ()),
        ModelToolDef(MODEL_GITHUB_OPEN_PR, "Open a GitHub pull request from a smoke branch.", {}, ()),
        ModelToolDef(MODEL_GITHUB_POST_PR_REVIEW, "Post a bounded GitHub PR review comment.", {}, ()),
        ModelToolDef(MODEL_GITHUB_POST_PR_COMMENT, "Post a bounded GitHub PR thread comment.", {}, ()),
    )


def _github_runtime_bindings() -> tuple[RuntimeBindingDef, ...]:
    return (
        RuntimeBindingDef(RUNTIME_GITHUB_LIST_PRS, MODEL_GITHUB_LIST_PRS, (TOOL_GITHUB_LIST_PRS,)),
        RuntimeBindingDef(RUNTIME_GITHUB_FETCH_PR, MODEL_GITHUB_FETCH_PR, (TOOL_GITHUB_FETCH_PR,)),
        RuntimeBindingDef(RUNTIME_GITHUB_FETCH_DIFF, MODEL_GITHUB_FETCH_DIFF, (TOOL_GITHUB_FETCH_DIFF,)),
        RuntimeBindingDef(RUNTIME_GITHUB_FETCH_COMMENTS, MODEL_GITHUB_FETCH_COMMENTS, (TOOL_GITHUB_FETCH_COMMENTS,)),
        RuntimeBindingDef(RUNTIME_GITHUB_FETCH_CHECKS, MODEL_GITHUB_FETCH_CHECKS, (TOOL_GITHUB_FETCH_CHECKS,)),
        RuntimeBindingDef(RUNTIME_GITHUB_COMMIT_FILES, MODEL_GITHUB_COMMIT_FILES, (TOOL_GITHUB_COMMIT_FILES,)),
        RuntimeBindingDef(RUNTIME_GITHUB_OPEN_PR, MODEL_GITHUB_OPEN_PR, (TOOL_GITHUB_OPEN_PR,)),
        RuntimeBindingDef(RUNTIME_GITHUB_POST_PR_REVIEW, MODEL_GITHUB_POST_PR_REVIEW, (TOOL_GITHUB_POST_PR_REVIEW,)),
        RuntimeBindingDef(RUNTIME_GITHUB_POST_PR_COMMENT, MODEL_GITHUB_POST_PR_COMMENT, (TOOL_GITHUB_POST_PR_COMMENT,)),
    )


REGISTRAR = GithubRegistrar()
