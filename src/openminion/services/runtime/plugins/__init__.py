from openminion.services.runtime.plugins.discovery import (
    PluginDiscoveryError,
    discover_plugin_manifests,
)
from openminion.services.runtime.plugins.hooks import Plugin, PluginContext
from openminion.services.runtime.plugins.manifests import (
    PluginManifest,
    PluginManifestError,
    load_plugin_manifest,
    validate_plugin_manifest,
)
from openminion.services.runtime.plugins.registry import (
    PluginRegistry,
    _build_custom_lookup,
    _normalize_enabled_plugins,
    build_default_plugin_registry,
    build_default_plugin_registry_with_activation_guard,
)

Hook, HookContext = Plugin, PluginContext
HookManifest, HookManifestError = PluginManifest, PluginManifestError
HookRegistry = PluginRegistry
load_hook_manifest, validate_hook_manifest = (
    load_plugin_manifest,
    validate_plugin_manifest,
)
HookDiscoveryError, discover_hook_manifests = (
    PluginDiscoveryError,
    discover_plugin_manifests,
)
build_default_hook_registry_with_activation_guard = (
    build_default_plugin_registry_with_activation_guard
)

__all__ = [
    "Plugin",
    "PluginContext",
    "PluginManifest",
    "PluginManifestError",
    "load_plugin_manifest",
    "validate_plugin_manifest",
    "PluginDiscoveryError",
    "discover_plugin_manifests",
    "PluginRegistry",
    "build_default_plugin_registry",
    "build_default_plugin_registry_with_activation_guard",
    "Hook",
    "HookContext",
    "HookManifest",
    "HookManifestError",
    "load_hook_manifest",
    "validate_hook_manifest",
    "HookDiscoveryError",
    "discover_hook_manifests",
    "HookRegistry",
    "build_default_hook_registry_with_activation_guard",
    "_build_custom_lookup",
    "_normalize_enabled_plugins",
]
