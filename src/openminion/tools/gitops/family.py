from openminion.modules.tool.exposure import ToolExposureProfile
from openminion.modules.tool.framework import ToolDecl, ToolFamilySpec

from .args import (
    GitOpsAppArgs,
)
from .interfaces import (
    ALL_GITOPS_TOOLS,
    TOOL_GITOPS_APP_STATUS,
    TOOL_GITOPS_APP_DIFF,
    TOOL_GITOPS_SOURCE_REVISION,
    TOOL_GITOPS_DRIFT_INSPECT,
)
from .plugin import (
    _h_app_status,
    _h_app_diff,
    _h_source_revision,
    _h_drift_inspect,
)

GITOPS_FAMILY = ToolFamilySpec(
    module_id="gitops",
    min_scope_default="READ_ONLY",
    common_tags=("plugin", "ops", "gitops"),
    exposure_profiles=(
        ToolExposureProfile(
            profile_id="gitops_readonly",
            title="GitOps",
            summary="Read-only Argo CD and Flux status, diff, revision, and drift inspection.",
            tool_names=frozenset(ALL_GITOPS_TOOLS),
            target_kinds=frozenset({"ops-target"}),
            dependencies=frozenset({"fixture:gitops"}),
            evidence_expectations=("return fixture/live source and evidence digest",),
            stop_rules=("stop before mutation or unscoped provider access",),
            guidance_names=("ops.safety.v1",),
            activation_hint="Activate explicitly for a scoped read-only operations task.",
        ),
    ),
    tools=(
        ToolDecl(
            TOOL_GITOPS_APP_STATUS,
            GitOpsAppArgs,
            _h_app_status,
            "Inspect Argo CD or Flux application health and sync status.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_GITOPS_APP_DIFF,
            GitOpsAppArgs,
            _h_app_diff,
            "Inspect application drift/diff without sync.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_GITOPS_SOURCE_REVISION,
            GitOpsAppArgs,
            _h_source_revision,
            "Inspect source revision facts.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_GITOPS_DRIFT_INSPECT,
            GitOpsAppArgs,
            _h_drift_inspect,
            "Inspect drift state without reconcile.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
    ),
)

__all__ = ["GITOPS_FAMILY"]
