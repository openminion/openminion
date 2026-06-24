from openminion.modules.tool.runtime.blast_radius import (
    InMemoryCompositionAuditLog,
    build_composition_policy,
)
from openminion.services.security.blast_radius.adapter import (
    CompositionBoundaryAdapter,
    build_composition_boundary_adapter,
)

# Canonical default policy id used for production wiring. Operators
DEFAULT_COMPOSITION_POLICY_ID = "openminion.tsbr.default"

# Caller-declared seam ids for the eight named production paths.
# Static labels, never synthesized.
SEAM_BRAIN_RUNTIME_TOOL_API = "modules.brain.adapters.tool.runtime"
SEAM_AGENT_EXECUTOR_RUNTIME = "services.agent.execution.runtime"
SEAM_TOOL_EXECUTOR = "modules.tool.executor"
SEAM_AGENT_SERVICE = "services.agent.service"
SEAM_AGENT_TOOL_FALLBACKS = "services.agent.fallbacks"
SEAM_AGENT_REQUIRED_LANE_RETRY = "services.agent.execution.required_lane.post_execution"
SEAM_API_TOOLS = "api.operations.tools"
SEAM_CLI_TOOLS = "cli.commands.tools"
SEAM_CLI_CRON = "cli.commands.cron"
SEAM_RUNTIME_ENGINE = "services.runtime.engine"


def build_default_composition_boundary_adapter(
    *, seam_id: str
) -> CompositionBoundaryAdapter:
    """Build a turn-scoped composition-boundary adapter for one seam."""
    return build_composition_boundary_adapter(
        policy=build_composition_policy(policy_id=DEFAULT_COMPOSITION_POLICY_ID),
        audit_log=InMemoryCompositionAuditLog(),
        seam_id=seam_id,
    )


__all__ = [
    "DEFAULT_COMPOSITION_POLICY_ID",
    "SEAM_AGENT_EXECUTOR_RUNTIME",
    "SEAM_AGENT_REQUIRED_LANE_RETRY",
    "SEAM_AGENT_SERVICE",
    "SEAM_AGENT_TOOL_FALLBACKS",
    "SEAM_API_TOOLS",
    "SEAM_BRAIN_RUNTIME_TOOL_API",
    "SEAM_CLI_CRON",
    "SEAM_CLI_TOOLS",
    "SEAM_RUNTIME_ENGINE",
    "SEAM_TOOL_EXECUTOR",
    "build_default_composition_boundary_adapter",
]
