"""OpenMinion's stable package-level public API."""

from __future__ import annotations

# Bind __version__ before public re-exports to avoid circular import reads.
__version__ = "0.0.1"

from openminion.api import (  # noqa: E402 — ordered after __version__
    APIRuntime,
    Agent,
    AgentOutputValidationError,
    AgentRunResult,
    Handoff,
    subagent,
)
from openminion.base.config import OpenMinionConfig  # noqa: E402
from openminion.modules.memory.portability import MemoryBundle  # noqa: E402
from openminion.tools import tool  # noqa: E402

__all__ = [
    "APIRuntime",
    "Agent",
    "AgentOutputValidationError",
    "AgentRunResult",
    "Handoff",
    "MemoryBundle",
    "OpenMinionConfig",
    "__version__",
    "subagent",
    "tool",
]

# Per-symbol public-surface version metadata.
__since__: dict[str, str] = {
    "APIRuntime": "0.0.1",
    "Agent": "0.0.1",
    "AgentOutputValidationError": "0.0.1",
    "AgentRunResult": "0.0.1",
    "Handoff": "0.0.1",
    "MemoryBundle": "0.0.1",
    "OpenMinionConfig": "0.0.1",
    "__version__": "0.0.1",
    "subagent": "0.0.1",
    "tool": "0.0.1",
}
