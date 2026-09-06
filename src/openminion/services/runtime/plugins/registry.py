import logging
from collections.abc import Callable, Iterable

from openminion.base.config import OpenMinionConfig
from openminion.base.config.runtime.capability import resolve_plugin_runtime_policy
from openminion.base.types import AgentResponse, Message
from openminion.base.version import OPENMINION_VERSION
from openminion.services.config import resolve_services_plugin_paths
from openminion.services.runtime.plugins.metadata import plugin_label
from openminion.services.runtime.plugins.hooks import Plugin, PluginContext
from openminion.services.runtime.plugins.validate import ValidatePlugin
from openminion.services.runtime.plugins.discovery import (
    DiscoveredPlugin,
    discover_plugin_manifests,
    load_plugin_instance,
    load_plugin_module,
)
from openminion.services.runtime.errors import PluginActivationError
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
        self._plugins: list[Plugin] = list(plugins)
        self._manifests: dict[str, PluginManifest] = {}
        self._manifest_plugins: dict[str, Plugin] = {}
        self._registrars: list[tuple[str, object]] = []
        self._hook_runner = hook_runner or PluginHookRunner()

    def register(
        self,
        plugin: Plugin,
        manifest: PluginManifest | None = None,
        registrar: object | None = None,
    ) -> None:
        if manifest is not None:
            setattr(plugin, "_openminion_plugin_id", manifest.id)
        if manifest is not None:
            if manifest.id in self._manifests:
                raise RuntimeError(f"Duplicate plugin manifest id: {manifest.id}")
            self._manifests[manifest.id] = manifest
            self._manifest_plugins[manifest.id] = plugin
        if registrar is not None:
            self._registrars.append(
                (manifest.id if manifest else plugin_label(plugin), registrar)
            )
        self._plugins.append(plugin)

    def names(self) -> list[str]:
        return [plugin.name for plugin in self._plugins]

    def manifest_ids(self) -> list[str]:
        return sorted(self._manifests)

    def manifests(self) -> list[PluginManifest]:
        return [self._manifests[key] for key in sorted(self._manifests)]

    def status(self, plugin: Plugin) -> dict[str, object]:
        failure = self._hook_runner.failure_status(plugin)
        return {
            "state": "degraded" if failure is not None else "healthy",
            "last_failure": failure,
        }

    def plugin_statuses(self) -> dict[str, dict[str, object]]:
        statuses = {
            plugin_label(plugin): self.status(plugin) for plugin in self._plugins
        }
        statuses.update(
            {
                manifest_id: self.status(plugin)
                for manifest_id, plugin in self._manifest_plugins.items()
            }
        )
        return statuses

    def registrars(self) -> tuple[tuple[str, object], ...]:
        return tuple(self._registrars)

    def register_tool_extensions(
        self, registry: ToolRegistry, context: PluginContext
    ) -> None:
        for plugin in self._plugins:
            if plugin.__class__.register_tools is not Plugin.register_tools:
                raise PluginActivationError(
                    plugin_id=plugin_label(plugin),
                    stage="tool_registration",
                    reason_code="unbound_tool_contribution",
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
    config: OpenMinionConfig,
    logger: logging.Logger,
    event_sink: Callable[[str, dict[str, object]], None] | None = None,
) -> PluginRegistry:
    return _build_default_plugin_registry(
        config=config,
        logger=logger,
        on_before_activate=None,
        event_sink=event_sink,
    )


def _build_default_plugin_registry(
    *,
    config: OpenMinionConfig,
    logger: logging.Logger,
    on_before_activate: Callable[[PluginManifest], None] | None,
    event_sink: Callable[[str, dict[str, object]], None] | None,
) -> PluginRegistry:
    registry = PluginRegistry(hook_runner=PluginHookRunner(event_sink=event_sink))
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
                f"Enabled plugin was not found: {enabled_item}. "
                "Checked built-ins and discovery roots."
            )
        if discovered_plugin.manifest.id in loaded_manifest_ids:
            continue

        if on_before_activate is not None:
            on_before_activate(discovered_plugin.manifest)
        module = load_plugin_module(discovered_plugin)
        plugin_instance = load_plugin_instance(discovered_plugin, module=module)
        registrar = getattr(module, "REGISTRAR", None)
        if (
            registrar is None
            and plugin_instance.__class__.register_tools is not Plugin.register_tools
        ):
            raise PluginActivationError(
                plugin_id=discovered_plugin.manifest.id,
                stage="tool_registration",
                reason_code="unbound_tool_contribution",
            )
        _enforce_provider_extension_policy(
            plugin=plugin_instance, manifest=discovered_plugin.manifest
        )
        registry.register(
            plugin_instance,
            manifest=discovered_plugin.manifest,
            registrar=registrar,
        )
        loaded_manifest_ids.add(discovered_plugin.manifest.id)

    logger.debug("enabled plugins: %s", ", ".join(registry.names()) or "none")
    return registry


def build_default_plugin_registry_with_activation_guard(
    *,
    config: OpenMinionConfig,
    logger: logging.Logger,
    on_before_activate: Callable[[PluginManifest], None] | None = None,
    event_sink: Callable[[str, dict[str, object]], None] | None = None,
) -> PluginRegistry:
    return _build_default_plugin_registry(
        config=config,
        logger=logger,
        on_before_activate=on_before_activate,
        event_sink=event_sink,
    )


def _built_in_validate_manifest() -> PluginManifest:
    try:
        return validate_plugin_manifest(
            {
                "id": "builtin.validate",
                "name": "Validate Plugin",
                "version": OPENMINION_VERSION,
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
            f"Built-in validate plugin manifest is invalid: {'; '.join(exc.errors)}"
        ) from exc


def _normalize_enabled_plugins(raw_values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        normalized_value = value.strip()
        if not normalized_value or normalized_value in seen:
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
                f"Custom plugin manifest id conflicts with reserved plugin id/key: {manifest_id}"
            )

        existing_manifest = manifest_index.get(manifest_id)
        if existing_manifest is not None:
            if existing_manifest.module_path != discovered.module_path:
                raise RuntimeError(
                    f"Plugin discovery conflict for manifest id {manifest_id}: "
                    f"{existing_manifest.module_path} vs {discovered.module_path}"
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
                    f"Plugin discovery conflict for key {lookup_key}: "
                    f"{existing.manifest.id} vs {discovered.manifest.id}"
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
