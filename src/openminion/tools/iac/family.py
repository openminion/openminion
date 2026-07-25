from __future__ import annotations

from openminion.modules.tool.exposure import ToolExposureProfile
from openminion.modules.tool.framework import ToolDecl, ToolFamilySpec

from .args import (
    IacPlanArgs,
    IacPlanShowArgs,
    IacWorkspaceArgs,
)
from .interfaces import (
    ALL_IAC_TOOLS,
    TOOL_IAC_VALIDATE,
    TOOL_IAC_PLAN_CREATE,
    TOOL_IAC_PLAN_SHOW,
    TOOL_IAC_PROVIDER_FACTS,
)
from .plugin import (
    _h_validate,
    _h_plan_create,
    _h_plan_show,
    _h_provider_facts,
)

IAC_FAMILY = ToolFamilySpec(
    module_id="iac",
    min_scope_default="READ_ONLY",
    common_tags=("plugin", "ops", "iac"),
    exposure_profiles=(
        ToolExposureProfile(
            profile_id="iac_plan",
            title="IaC planning",
            summary="Terraform/OpenTofu validate, plan, show-json, and provider facts.",
            tool_names=frozenset(ALL_IAC_TOOLS),
            target_kinds=frozenset({"ops-target"}),
            dependencies=frozenset({"fixture:iac"}),
            evidence_expectations=("return fixture/live source and evidence digest",),
            stop_rules=("stop before mutation or unscoped provider access",),
            guidance_names=("ops.safety.v1",),
            activation_hint="Activate explicitly for a scoped read-only operations task.",
        ),
    ),
    tools=(
        ToolDecl(
            TOOL_IAC_VALIDATE,
            IacWorkspaceArgs,
            _h_validate,
            "Validate a Terraform/OpenTofu workspace fixture.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_IAC_PLAN_CREATE,
            IacPlanArgs,
            _h_plan_create,
            "Create a saved plan fixture and return hashes without applying.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_IAC_PLAN_SHOW,
            IacPlanShowArgs,
            _h_plan_show,
            "Parse saved plan JSON fixture with redaction.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_IAC_PROVIDER_FACTS,
            IacWorkspaceArgs,
            _h_provider_facts,
            "Inspect provider and version facts.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
    ),
)

__all__ = ["IAC_FAMILY"]
