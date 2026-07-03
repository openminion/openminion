"""OpenMinion's stable package-level public API."""

from typing import Any

from openminion.base.version import (
    OPENMINION_INITIAL_PUBLIC_VERSION,
    OPENMINION_VERSION,
)

# Bind __version__ before public re-exports to avoid circular import reads.
__version__ = OPENMINION_VERSION

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
    "APIRuntime": OPENMINION_INITIAL_PUBLIC_VERSION,
    "Agent": OPENMINION_INITIAL_PUBLIC_VERSION,
    "AgentOutputValidationError": OPENMINION_INITIAL_PUBLIC_VERSION,
    "AgentRunResult": OPENMINION_INITIAL_PUBLIC_VERSION,
    "Handoff": OPENMINION_INITIAL_PUBLIC_VERSION,
    "MemoryBundle": OPENMINION_INITIAL_PUBLIC_VERSION,
    "OpenMinionConfig": OPENMINION_INITIAL_PUBLIC_VERSION,
    "__version__": OPENMINION_INITIAL_PUBLIC_VERSION,
    "subagent": OPENMINION_INITIAL_PUBLIC_VERSION,
    "tool": OPENMINION_INITIAL_PUBLIC_VERSION,
}
