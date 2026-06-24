# ruff: noqa: F401

from typing import Any

from openminion.modules.tool.constants import (
    TOOL_BOOTSTRAP_STATUS_ALREADY_REGISTERED,
    TOOL_BOOTSTRAP_STATUS_REGISTERED,
)
from openminion.modules.tool.base import (
    Tool,
    ToolExecutionContext,
    ToolExecutionPolicy,
    ToolExecutionResult as ToolExecutionResultV2,
)
from openminion.modules.tool.runtime.policy import (
    DEFAULT_POLICY,
    Policy,
    RuntimeBindingPolicy,
    ToolBindingPolicyManager,
    canonical_tool_name,
)
from openminion.modules.tool.runtime.dispatch import (
    BindingResolution,
    resolve_binding_for_call,
)
from openminion.modules.tool.runtime import (
    RuntimeContext,
    build_runtime_repositories,
    create_run_root,
    new_run_id,
    preferred_artifact_ref,
)
from openminion.modules.tool.interfaces import (
    CONTRACT_VERSION_PATTERN,
    PLUGIN_CONTRACT_VERSION,
    ContractProtocol,
    ContractValidator,
    PluginContract,
    PluginContractError,
    ToolErrorEnvelope,
    ToolRequestEnvelope,
    ToolResultEnvelope,
    validate_plugin_contract,
)
from openminion.modules.tool.plugin_contract import (
    ArtifactRef,
    CASArtifactSink,
    HealthStatus,
    MemoryArtifactSink,
    MemoryEventSink,
    MethodSchema,
    PolicyDecision,
    PolicyHook,
    ToolCapabilities,
    ToolContext,
    ToolDefinition,
    ToolDescriptor,
    ToolError,
    ToolInvocation,
    ToolMethod,
    ToolPlugin,
    ToolResult,
    ToolSchemaBundle,
)
from openminion.modules.tool.runtime.plugin import (
    AllowAllPolicyHook,
    DenyHighRiskWithoutTagPolicyHook,
    ToolRuntime,
)
from openminion.modules.tool.runtime.policy import reorder_runtime_chain
from openminion.modules.tool.registry import (
    ToolExecutionBatch,
    ToolPolicyProfile,
    ToolRegistry,
    ToolSpec,
)
from openminion.modules.tool.runtime.manager import (
    ToolRegistryManager,
    build_default_tool_registry_manager,
)
from openminion.modules.tool.bootstrap import (
    build_runtime_bootstrap,
    RuntimeBootstrap,
)
from openminion.modules.tool.runtime.registrar import (
    ToolRegisterContext,
    ToolModuleRegistrar,
)

__version__ = "0.0.1"

# Module-first tool source gates. Wave-1 TMFC/WOMC migrations forced
_MODULES_ONLY = True
_TAVILY_SOURCE = "module_first"
_WEATHER_SOURCE = "module_first"


def build_default_tool_registry(
    *,
    config: Any | None = None,
    workspace_root: Any | None = None,
    run_root: Any | None = None,
    strict: bool = False,
) -> ToolRegistry:
    bootstrap = build_runtime_bootstrap(
        config=config,
        workspace_root=workspace_root,
        run_root=run_root,
        strict=strict,
    )
    return bootstrap.registry


def build_default_tool_registry_debug_report() -> dict[str, Any]:
    bootstrap = build_runtime_bootstrap(
        config=None,
        workspace_root=None,
        run_root=None,
        strict=False,
    )
    records = [record.__dict__.copy() for record in (bootstrap.bootstrap_records or [])]
    required_failures = [
        record
        for record in records
        if bool(record.get("required"))
        and str(record.get("status") or "")
        not in {
            TOOL_BOOTSTRAP_STATUS_REGISTERED,
            TOOL_BOOTSTRAP_STATUS_ALREADY_REGISTERED,
        }
    ]
    return {
        "ok": len(required_failures) == 0,
        "required_failures": required_failures,
        "bootstrap_records": records,
        "registry_snapshot": bootstrap.registry.registration_debug_snapshot(),
    }


__all__ = [
    "__version__",
    "AllowAllPolicyHook",
    "BindingResolution",
    "CONTRACT_VERSION_PATTERN",
    "DEFAULT_POLICY",
    "DenyHighRiskWithoutTagPolicyHook",
    "PLUGIN_CONTRACT_VERSION",
    "Policy",
    "RuntimeBindingPolicy",
    "RuntimeBootstrap",
    "RuntimeContext",
    "Tool",
    "ToolBindingPolicyManager",
    "ToolExecutionBatch",
    "ToolExecutionContext",
    "ToolExecutionPolicy",
    "ToolExecutionResultV2",
    "ToolModuleRegistrar",
    "ToolPolicyProfile",
    "ToolRegisterContext",
    "ToolRegistry",
    "ToolRegistryManager",
    "ToolRuntime",
    "ToolSpec",
    "_MODULES_ONLY",
    "_TAVILY_SOURCE",
    "_WEATHER_SOURCE",
    "build_default_tool_registry",
    "build_default_tool_registry_debug_report",
    "build_runtime_bootstrap",
    "build_runtime_repositories",
    "canonical_tool_name",
    "create_run_root",
    "new_run_id",
    "preferred_artifact_ref",
    "reorder_runtime_chain",
    "resolve_binding_for_call",
    "validate_plugin_contract",
]
