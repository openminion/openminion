"""Compatibility imports for module-owned composition policy wiring."""

from openminion.modules.policy.adapters.composition import (
    DEFAULT_COMPOSITION_POLICY_ID,
    SEAM_AGENT_EXECUTOR_RUNTIME,
    SEAM_AGENT_REQUIRED_LANE_RETRY,
    SEAM_AGENT_SERVICE,
    SEAM_AGENT_TOOL_FALLBACKS,
    SEAM_API_TOOLS,
    SEAM_BRAIN_RUNTIME_TOOL_API,
    SEAM_CLI_CRON,
    SEAM_CLI_TOOLS,
    SEAM_RUNTIME_ENGINE,
    SEAM_TOOL_EXECUTOR,
    build_default_composition_boundary_adapter,
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
