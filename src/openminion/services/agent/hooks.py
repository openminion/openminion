"""Compatibility imports for agent execution lifecycle hooks."""

from openminion.services.runtime.plugins.hooks import PluginContext

HookContext = PluginContext

__all__ = ["HookContext"]
