from openminion.modules.tool.exposure import ToolExposureProfile
from openminion.modules.tool.framework import ToolDecl, ToolFamilySpec

from .args import (
    AnsibleCheckArgs,
    ConfigTargetArgs,
    SaltTestArgs,
    SaltJobArgs,
)
from .interfaces import (
    ALL_CONFIG_MGMT_TOOLS,
    TOOL_CONFIG_ANSIBLE_CHECK,
    TOOL_CONFIG_ANSIBLE_FACTS,
    TOOL_CONFIG_SALT_TEST,
    TOOL_CONFIG_SALT_JOB_STATUS,
)
from .plugin import (
    _h_ansible_check,
    _h_ansible_facts,
    _h_salt_test,
    _h_salt_job_status,
)

CONFIG_MGMT_FAMILY = ToolFamilySpec(
    module_id="config_mgmt",
    min_scope_default="READ_ONLY",
    common_tags=("plugin", "ops", "config_mgmt"),
    exposure_profiles=(
        ToolExposureProfile(
            profile_id="config_check",
            title="Configuration checks",
            summary="Read-only/check-mode Ansible Runner and Salt inspection.",
            tool_names=frozenset(ALL_CONFIG_MGMT_TOOLS),
            target_kinds=frozenset({"ops-target"}),
            dependencies=frozenset({"fixture:config_mgmt"}),
            evidence_expectations=("return fixture/live source and evidence digest",),
            stop_rules=("stop before mutation or unscoped provider access",),
            guidance_names=("ops.safety.v1",),
            activation_hint="Activate explicitly for a scoped read-only operations task.",
        ),
    ),
    tools=(
        ToolDecl(
            TOOL_CONFIG_ANSIBLE_CHECK,
            AnsibleCheckArgs,
            _h_ansible_check,
            "Run/check Ansible local-inventory fixture without remote mutation.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_CONFIG_ANSIBLE_FACTS,
            ConfigTargetArgs,
            _h_ansible_facts,
            "Inspect Ansible facts fixture.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_CONFIG_SALT_TEST,
            SaltTestArgs,
            _h_salt_test,
            "Inspect Salt test-mode fixture.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_CONFIG_SALT_JOB_STATUS,
            SaltJobArgs,
            _h_salt_job_status,
            "Inspect Salt job status fixture.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
    ),
)

__all__ = ["CONFIG_MGMT_FAMILY"]
