from openminion.modules.tool.exposure import ToolExposureProfile
from openminion.modules.tool.framework import ToolDecl, ToolFamilySpec

from .args import (
    SsmInventoryArgs,
    SsmCommandStatusArgs,
)
from .interfaces import (
    ALL_CLOUD_OPS_TOOLS,
    TOOL_CLOUD_SSM_INVENTORY,
    TOOL_CLOUD_SSM_COMMAND_STATUS,
)
from .plugin import (
    _h_ssm_inventory,
    _h_ssm_command_status,
)

CLOUD_OPS_FAMILY = ToolFamilySpec(
    module_id="cloud_ops",
    min_scope_default="READ_ONLY",
    common_tags=("plugin", "ops", "cloud_ops"),
    exposure_profiles=(
        ToolExposureProfile(
            profile_id="cloud_ops_readonly",
            title="Cloud operations",
            summary="Read-only AWS SSM inventory and command-status inspection.",
            tool_names=frozenset(ALL_CLOUD_OPS_TOOLS),
            target_kinds=frozenset({"ops-target"}),
            dependencies=frozenset(),
            evidence_expectations=("return fixture/live source and evidence digest",),
            stop_rules=("stop before mutation or unscoped provider access",),
            guidance_names=("ops.safety.v1",),
            activation_hint="Activate explicitly for a scoped read-only operations task.",
        ),
    ),
    tools=(
        ToolDecl(
            TOOL_CLOUD_SSM_INVENTORY,
            SsmInventoryArgs,
            _h_ssm_inventory,
            "Inspect AWS SSM managed-node inventory within an approved account/region/tag scope.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_CLOUD_SSM_COMMAND_STATUS,
            SsmCommandStatusArgs,
            _h_ssm_command_status,
            "Inspect an existing AWS SSM command status without sending commands.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
    ),
)

__all__ = ["CLOUD_OPS_FAMILY"]
