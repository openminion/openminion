import logging
from typing import Callable, Iterable, List

from openminion.base.config import OpenMinionConfig
from openminion.base.config.runtime.capability import resolve_plugin_runtime_policy
from openminion.base.types import AgentResponse, Message
from openminion.services.config import resolve_services_plugin_paths
from openminion.services.runtime.plugins.metadata import plugin_label
from openminion.services.runtime.plugins.hooks import Plugin, PluginContext
from openminion.services.runtime.plugins.validate import ValidatePlugin
from openminion.services.runtime.plugins.discovery import (
    DiscoveredPlugin,
    discover_plugin_manifests,
    load_plugin_instance,
)
from openminion.services.runtime.plugins.hook_runner import PluginHookRunner
from openminion.services.runtime.plugins.manifests import (
    PluginManifest,
    PluginManifestError,
    validate_plugin_manifest,
)
from openminion.modules.tool.registry import ToolRegistry


class PluginRegistry:
    def __init__(
        self,
        plugins: Iterable[Plugin] = (),
        hook_runner: PluginHookRunner | None = None,
    ) -> None:
        self._plugins: List[Plugin] = list(plugins)
        self._manifests: dict[str, PluginManifest] = {}
        self._hook_runner = hook_runner or PluginHookRunner()

    def register(self, plugin: Plugin, manifest: PluginManifest | None = None) -> None:
        if manifest is not None:
            if manifest.id in self._manifests:
                raise RuntimeError(f"Duplicate plugin manifest id: {manifest.id}")
            self._manifests[manifest.id] = manifest
        self._plugins.append(plugin)

    def names(self) -> List[str]:
        return [plugin.name for plugin in self._plugins]

    def manifest_ids(self) -> List[str]:
        return sorted(self._manifests.keys())

    def manifests(self) -> List[PluginManifest]:
        return [self._manifests[key] for key in sorted(self._manifests.keys())]

    def register_tool_extensions(
        self, registry: ToolRegistry, context: PluginContext
    ) -> None:
        for plugin in self._plugins:
            try:
                plugin.register_tools(registry, context)
            except Exception:
                context.logger.exception(
                    "plugin tool registration failed plugin=%s",
                    plugin_label(plugin),
                )

    def apply_inbound(self, message: Message, context: PluginContext) -> Message:
        return self._hook_runner.run_inbound(self._plugins, message, context)

    def apply_outbound(
        self,
        response: AgentResponse,
        message: Message,
        context: PluginContext,
    ) -> AgentResponse:
        return self._hook_runner.run_outbound(self._plugins, response, message, context)


def build_default_plugin_registry(
    config: OpenMinionConfig, logger: logging.Logger
) -> PluginRegistry:
    return _build_default_plugin_registry(
        config=config, logger=logger, on_before_activate=None
    )


def _build_default_plugin_registry(
    *,
    config: OpenMinionConfig,
    logger: logging.Logger,
    on_before_activate: Callable[[PluginManifest], None] | None,
) -> PluginRegistry:
    registry = PluginRegistry()
    enabled = _normalize_enabled_plugins(
        list(
            resolve_plugin_runtime_policy(
                compatibility_enabled_plugins=list(config.enabled_plugins),
                system_policy=getattr(config.runtime, "plugins", None),
            ).effective_enabled
        )
    )
    if not enabled:
        logger.debug("enabled plugins: none")
        return registry

    builtin_specs = {
        "validate": (ValidatePlugin, _built_in_validate_manifest()),
    }
    builtin_lookup: dict[str, tuple[type[Plugin], PluginManifest]] = {}
    for plugin_key, (plugin_class, manifest) in builtin_specs.items():
        builtin_lookup[plugin_key] = (plugin_class, manifest)
        builtin_lookup[manifest.id] = (plugin_class, manifest)

    discovered = discover_plugin_manifests(resolve_services_plugin_paths(None))
    custom_lookup = _build_custom_lookup(
        discovered_plugins=discovered,
        reserved_lookup_keys=set(builtin_lookup.keys()),
    )

    loaded_manifest_ids: set[str] = set()
    for enabled_item in enabled:
        builtin_entry = builtin_lookup.get(enabled_item)
        if builtin_entry is not None:
            plugin_class, manifest = builtin_entry
            if manifest.id in loaded_manifest_ids:
                continue
            if on_before_activate is not None:
                on_before_activate(manifest)
            plugin_instance = plugin_class()
            _enforce_provider_extension_policy(
                plugin=plugin_instance, manifest=manifest
            )
            registry.register(plugin_instance, manifest=manifest)
            loaded_manifest_ids.add(manifest.id)
            continue

        discovered_plugin = custom_lookup.get(enabled_item)
        if discovered_plugin is None:
            raise RuntimeError(
                "Enabled plugin was not found: "
                + enabled_item
                + ". Checked built-ins and discovery roots."
            )
        if discovered_plugin.manifest.id in loaded_manifest_ids:
            continue

        if on_before_activate is not None:
            on_before_activate(discovered_plugin.manifest)
        plugin_instance = load_plugin_instance(discovered_plugin)
        _enforce_provider_extension_policy(
            plugin=plugin_instance, manifest=discovered_plugin.manifest
        )
        registry.register(plugin_instance, manifest=discovered_plugin.manifest)
        loaded_manifest_ids.add(discovered_plugin.manifest.id)

    logger.debug("enabled plugins: %s", ", ".join(registry.names()) or "none")
    return registry


def build_default_plugin_registry_with_activation_guard(
    *,
    config: OpenMinionConfig,
    logger: logging.Logger,
    on_before_activate: Callable[[PluginManifest], None] | None = None,
) -> PluginRegistry:
    return _build_default_plugin_registry(
        config=config,
        logger=logger,
        on_before_activate=on_before_activate,
    )


def _built_in_validate_manifest() -> PluginManifest:
    try:
        return validate_plugin_manifest(
            {
                "id": "builtin.validate",
                "name": "Validate Plugin",
                "version": "1.0.0",
                "description": "Built-in plugin for lightweight inbound/outbound sanity logging.",
                "config_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "trust_tier": "verified",
                "requested_capabilities": [
                    "message.inbound.read",
                    "message.outbound.read",
                ],
                "provenance": {
                    "source": "builtin",
                    "publisher": "openminion",
                    "checksum": "builtin.validate",
                    "verified": True,
                },
            }
        )
    except PluginManifestError as exc:
        raise RuntimeError(
            "Built-in validate plugin manifest is invalid: " + "; ".join(exc.errors)
        ) from exc


def _normalize_enabled_plugins(raw_values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        normalized_value = str(value).strip()
        if not normalized_value:
            continue
        if normalized_value in seen:
            continue
        seen.add(normalized_value)
        normalized.append(normalized_value)
    return normalized


def _build_custom_lookup(
    *,
    discovered_plugins: list[DiscoveredPlugin],
    reserved_lookup_keys: set[str],
) -> dict[str, DiscoveredPlugin]:
    lookup: dict[str, DiscoveredPlugin] = {}
    manifest_index: dict[str, DiscoveredPlugin] = {}
    for discovered in discovered_plugins:
        manifest_id = discovered.manifest.id
        if manifest_id in reserved_lookup_keys:
            raise RuntimeError(
                "Custom plugin manifest id conflicts with reserved plugin id/key: "
                + manifest_id
            )

        existing_manifest = manifest_index.get(manifest_id)
        if existing_manifest is not None:
            if existing_manifest.module_path != discovered.module_path:
                raise RuntimeError(
                    "Plugin discovery conflict for manifest id "
                    + manifest_id
                    + ": "
                    + str(existing_manifest.module_path)
                    + " vs "
                    + str(discovered.module_path)
                )
        else:
            manifest_index[manifest_id] = discovered

        lookup_keys = {manifest_id, discovered.module_alias}
        for lookup_key in lookup_keys:
            if lookup_key in reserved_lookup_keys:
                # Built-ins win for built-in keys; custom plugins keep manifest ids.
                continue

            existing = lookup.get(lookup_key)
            if existing is not None and existing.manifest.id != discovered.manifest.id:
                raise RuntimeError(
                    "Plugin discovery conflict for key "
                    + lookup_key
                    + ": "
                    + existing.manifest.id
                    + " vs "
                    + discovered.manifest.id
                )
            lookup[lookup_key] = discovered
    return lookup


def _enforce_provider_extension_policy(
    *, plugin: Plugin, manifest: PluginManifest
) -> None:
    if plugin.__class__.register_providers is Plugin.register_providers:
        return
    raise RuntimeError(
        "Legacy provider extensions are no longer supported in OpenMinion plugins "
        f"(plugin={manifest.id}). Move provider logic to openminion.modules.llm via llmctl.providers."
    )
