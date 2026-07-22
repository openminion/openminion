from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any
from collections.abc import Sequence

from openminion.base.config import OpenMinionConfig
from openminion.base.config.runtime.capability import resolve_plugin_runtime_policy
from openminion.base.config.core import resolve_default_agent_id
from openminion.services.runtime.plugins.discovery import (
    PluginDiscoveryError,
    discover_plugin_manifests,
)
from openminion.services.config import resolve_services_plugin_paths


@dataclass(frozen=True)
class ExtensionRecord:
    name: str
    kind: str
    source: str
    module: str | None
    enabled: bool | None
    installed: bool
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "source": self.source,
            "module": self.module,
            "enabled": self.enabled,
            "installed": self.installed,
            "error": self.error,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ExtensionCatalog:
    plugins: list[ExtensionRecord]
    tool_plugins: list[ExtensionRecord]
    providers: list[ExtensionRecord]
    channels: list[ExtensionRecord]
    sidecars: list[ExtensionRecord] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugins": [record.to_dict() for record in self.plugins],
            "tool_plugins": [record.to_dict() for record in self.tool_plugins],
            "providers": [record.to_dict() for record in self.providers],
            "channels": [record.to_dict() for record in self.channels],
            "sidecars": [record.to_dict() for record in self.sidecars],
            "errors": [dict(item) for item in self.errors],
        }

    @classmethod
    def from_config(
        cls,
        config: OpenMinionConfig,
        *,
        plugin_roots: Sequence[Path] | None = None,
    ) -> "ExtensionCatalog":
        enabled_plugins = _resolve_enabled_plugins(config)

        errors: list[dict[str, Any]] = []
        plugins: list[ExtensionRecord] = [_builtin_validate_plugin(enabled_plugins)]

        discovered = []
        try:
            discovered = discover_plugin_manifests(
                resolve_services_plugin_paths(plugin_roots)
            )
        except PluginDiscoveryError as exc:
            errors.append(
                {
                    "kind": "plugin_manifest",
                    "error": str(exc),
                }
            )

        plugins.extend(_manifest_plugin_records(discovered, enabled_plugins))
        enabled_provider_name = _resolve_enabled_provider_name(config)
        provider_records = _entry_point_records(
            group="llmctl.providers",
            kind="provider",
            enabled_name=enabled_provider_name,
        )
        tool_records = _entry_point_records(
            group="openminion.modules.tool.runtime.plugins",
            kind="tool_plugin",
            enabled_name=None,
        )
        channel_records = _channel_records(config)
        sidecar_records = _sidecar_records()

        return cls(
            plugins=sorted(plugins, key=lambda record: record.name),
            tool_plugins=sorted(tool_records, key=lambda record: record.name),
            providers=sorted(provider_records, key=lambda record: record.name),
            channels=sorted(channel_records, key=lambda record: record.name),
            sidecars=sorted(sidecar_records, key=lambda record: record.name),
            errors=errors,
        )


def _resolve_enabled_plugins(config: OpenMinionConfig) -> set[str]:
    plugin_resolution = resolve_plugin_runtime_policy(
        compatibility_enabled_plugins=list(config.enabled_plugins or []),
        system_policy=getattr(config.runtime, "plugins", None),
    )
    return {
        str(item).strip()
        for item in plugin_resolution.effective_enabled
        if str(item).strip()
    }


def _builtin_validate_plugin(enabled_plugins: set[str]) -> ExtensionRecord:
    builtin_enabled = any(
        token in enabled_plugins for token in {"validate", "builtin.validate"}
    )
    return ExtensionRecord(
        name="builtin.validate",
        kind="plugin",
        source="builtin",
        module="openminion.services.runtime.validate.ValidatePlugin",
        enabled=builtin_enabled,
        installed=True,
        metadata={
            "id": "builtin.validate",
            "display_name": "Validate Plugin",
        },
    )


def _manifest_plugin_records(
    discovered: Sequence[Any],
    enabled_plugins: set[str],
) -> list[ExtensionRecord]:
    records: list[ExtensionRecord] = []
    for item in sorted(discovered, key=lambda entry: entry.manifest.id):
        manifest = item.manifest
        enabled = manifest.id in enabled_plugins or item.module_alias in enabled_plugins
        records.append(
            ExtensionRecord(
                name=manifest.id,
                kind="plugin",
                source="manifest",
                module=str(item.module_path),
                enabled=enabled,
                installed=True,
                metadata={
                    "display_name": manifest.name,
                    "version": manifest.version,
                    "manifest_path": str(item.manifest_path),
                    "module_alias": item.module_alias,
                    "trust_tier": manifest.trust_tier,
                },
            )
        )
    return records


def _resolve_enabled_provider_name(config: OpenMinionConfig) -> str:
    try:
        default_agent_id = resolve_default_agent_id(config)
        return str(config.agents[default_agent_id].provider or "").strip().lower()
    except Exception:  # noqa: BLE001
        return ""


def _entry_point_records(
    *,
    group: str,
    kind: str,
    enabled_name: str | None,
) -> list[ExtensionRecord]:
    records: list[ExtensionRecord] = []
    for ep in _entry_points(group):
        name = ep.name
        enabled = bool(enabled_name and name.lower() == enabled_name.lower())
        records.append(
            ExtensionRecord(
                name=name,
                kind=kind,
                source="entry_point",
                module=ep.module,
                enabled=enabled if enabled_name else True,
                installed=True,
                metadata={"group": group},
            )
        )
    return records


def _entry_points(group: str) -> list[EntryPoint]:
    eps = entry_points(group=group)
    return sorted(eps, key=lambda ep: ep.name)


def _channel_records(config: OpenMinionConfig) -> list[ExtensionRecord]:
    records: list[ExtensionRecord] = []
    enabled_channels = {
        str(item).strip().lower()
        for item in getattr(config, "enabled_channels", []) or []
        if str(item).strip()
    }
    if "console" not in enabled_channels:
        enabled_channels.add("console")
    raw_channels = getattr(config, "channels", {}) or {}
    for name in sorted(enabled_channels):
        source = (
            "builtin"
            if name == "console"
            else "config"
            if name in raw_channels
            else "legacy"
        )
        module = {
            "console": "openminion.base.channel.console",
            "telegram": "openminion.modules.controlplane.channels.telegram",
            "slack": "openminion.modules.controlplane.channels.slack",
        }.get(name)
        records.append(
            ExtensionRecord(
                name=name,
                kind="channel",
                source=source,
                module=module,
                enabled=True,
                installed=True,
            )
        )
    return records


def _sidecar_records() -> list[ExtensionRecord]:
    return [
        ExtensionRecord(
            name="pinchtab",
            kind="sidecar",
            source="builtin",
            module="openminion.tools.browser.providers.pinchtab.daemon",
            enabled=True,
            installed=True,
            metadata={
                "description": "PinchTab browser bridge daemon",
                "autostart_env_key": "PINCHTAB_AUTOSTART",
            },
        )
    ]
