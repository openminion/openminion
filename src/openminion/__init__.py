"""OpenMinion's stable package-level public API."""

from typing import Any

# Bind __version__ before public re-exports to avoid circular import reads.
__version__ = "0.0.1"

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

_LAZY_EXPORTS = {
    "APIRuntime": ("openminion.api", "APIRuntime"),
    "Agent": ("openminion.api", "Agent"),
    "AgentOutputValidationError": ("openminion.api", "AgentOutputValidationError"),
    "AgentRunResult": ("openminion.api", "AgentRunResult"),
    "Handoff": ("openminion.api", "Handoff"),
    "MemoryBundle": ("openminion.modules.memory.portability", "MemoryBundle"),
    "OpenMinionConfig": ("openminion.base.config", "OpenMinionConfig"),
    "subagent": ("openminion.api", "subagent"),
    "tool": ("openminion.tools", "tool"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module 'openminion' has no attribute {name!r}") from exc

    from importlib import import_module

    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value

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
