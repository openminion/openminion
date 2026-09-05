from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from openminion.base.config import OpenMinionConfig, save_config
from openminion.cli.config import load_cli_config_with_path
from openminion.cli.presentation.json_output import print_json_payload
from openminion.services.config import resolve_services_plugin_paths
from openminion.services.runtime.plugins.discovery import (
    DiscoveredPlugin,
    discover_plugin_manifests,
    load_plugin_instance,
    module_checksum_status,
)


_STATE_FILE = ".openminion-plugin-installs.json"
_BACKUP_DIR = ".openminion-plugin-rollback"


def list_plugins(_args: Any, app: Any) -> int:
    names = app.plugins.names()
    if not names:
        print("No plugins enabled")
        return 0

    for name in names:
        print(name)
    return 0


def _plugin_root(args: Any) -> Path:
    explicit = str(getattr(args, "root", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return resolve_services_plugin_paths(None)[0]


def _source_plugin(path: str, *, verify_checksums: bool = True) -> DiscoveredPlugin:
    source = Path(path).expanduser().resolve()
    root = source if source.is_dir() else source.parent
    discovered = discover_plugin_manifests([root], verify_checksums=verify_checksums)
    if not source.is_dir():
        discovered = [item for item in discovered if item.manifest_path == source]
    if len(discovered) != 1:
        raise RuntimeError(f"Expected one plugin manifest under {source}.")
    return discovered[0]


def _installed_plugin(root: Path, plugin_id: str) -> DiscoveredPlugin:
    matches = [
        item
        for item in discover_plugin_manifests([root])
        if item.manifest.id == plugin_id or item.module_alias == plugin_id
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Installed plugin not found: {plugin_id}")
    return matches[0]


def _manifest_payload(plugin: DiscoveredPlugin) -> dict[str, Any]:
    manifest = plugin.manifest
    return {
        "id": manifest.id,
        "name": manifest.name,
        "version": manifest.version,
        "dependencies": list(manifest.dependencies),
        "dependencies_enforced": False,
        "config_schema_enforced": False,
        "permissions": list(manifest.requested_capabilities),
        "trust_tier": manifest.trust_tier,
        "provenance": {
            "source": manifest.provenance_source,
            "uri": manifest.provenance_uri,
            "publisher": manifest.provenance_publisher,
            "checksum": manifest.provenance_checksum,
            "verified": manifest.provenance_verified,
            "verification": module_checksum_status(manifest, plugin.module_path),
        },
    }


def _load_state(root: Path) -> dict[str, Any]:
    path = root / _STATE_FILE
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _save_state(root: Path, state: dict[str, Any]) -> None:
    path = root / _STATE_FILE
    if state:
        path.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    elif path.exists():
        path.unlink()


def _config(args: Any) -> tuple[OpenMinionConfig, Path]:
    return load_cli_config_with_path(
        getattr(args, "config", None),
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )


def _set_enabled(args: Any, plugin_id: str, enabled: bool) -> bool:
    config, config_path = _config(args)
    enabled_before = plugin_id in config.enabled_plugins
    current = [item for item in config.enabled_plugins if item != plugin_id]
    if enabled:
        current.append(plugin_id)
    config.enabled_plugins = current
    save_config(config, str(config_path))
    return enabled_before


def preview_plugin(args: Any) -> int:
    plugin = _source_plugin(args.source, verify_checksums=False)
    print_json_payload({"ok": True, "plugin": _manifest_payload(plugin)})
    return 0


def install_plugin(args: Any) -> int:
    plugin = _source_plugin(args.source)
    root = _plugin_root(args)
    root.mkdir(parents=True, exist_ok=True)
    state = _load_state(root)
    plugin_id = plugin.manifest.id

    manifest_target = root / plugin.manifest_path.name
    module_target = root / plugin.module_path.name
    backup_root = root / _BACKUP_DIR / plugin.module_alias
    had_previous = manifest_target.exists() and module_target.exists()
    if manifest_target.exists() != module_target.exists():
        raise RuntimeError(f"Incomplete existing plugin files for {plugin_id}.")
    if had_previous:
        shutil.rmtree(backup_root, ignore_errors=True)
        backup_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest_target, backup_root / manifest_target.name)
        shutil.copy2(module_target, backup_root / module_target.name)

    shutil.copy2(plugin.manifest_path, manifest_target)
    shutil.copy2(plugin.module_path, module_target)
    enabled_before = _set_enabled(args, plugin_id, True)
    state[plugin_id] = {
        "module_alias": plugin.module_alias,
        "manifest_name": manifest_target.name,
        "module_name": module_target.name,
        "had_previous": had_previous,
        "enabled_before": enabled_before,
    }
    _save_state(root, state)
    print_json_payload(
        {
            "ok": True,
            "action": "installed",
            "root": str(root),
            "plugin": _manifest_payload(plugin),
        }
    )
    return 0


def health_plugin(args: Any) -> int:
    root = _plugin_root(args)
    plugin = _installed_plugin(root, args.plugin_id)
    instance = load_plugin_instance(plugin)
    config, _ = _config(args)
    print_json_payload(
        {
            "ok": True,
            "healthy": True,
            "enabled": plugin.manifest.id in config.enabled_plugins,
            "runtime_name": instance.name,
            "plugin": _manifest_payload(plugin),
        }
    )
    return 0


def rollback_plugin(args: Any) -> int:
    root = _plugin_root(args)
    state = _load_state(root)
    record = state.get(args.plugin_id)
    if not isinstance(record, dict):
        raise RuntimeError(f"No install rollback is available for {args.plugin_id}.")

    manifest_target = root / str(record["manifest_name"])
    module_target = root / str(record["module_name"])
    if bool(record["had_previous"]):
        backup_root = root / _BACKUP_DIR / str(record["module_alias"])
        shutil.copy2(backup_root / manifest_target.name, manifest_target)
        shutil.copy2(backup_root / module_target.name, module_target)
        action = "restored"
    else:
        manifest_target.unlink(missing_ok=True)
        module_target.unlink(missing_ok=True)
        action = "removed"
    _set_enabled(args, args.plugin_id, bool(record["enabled_before"]))
    shutil.rmtree(root / _BACKUP_DIR / str(record["module_alias"]), ignore_errors=True)
    state.pop(args.plugin_id)
    _save_state(root, state)
    print_json_payload({"ok": True, "action": action, "plugin_id": args.plugin_id})
    return 0


def uninstall_plugin(args: Any) -> int:
    root = _plugin_root(args)
    plugin = _installed_plugin(root, args.plugin_id)
    plugin.manifest_path.unlink()
    plugin.module_path.unlink()
    _set_enabled(args, plugin.manifest.id, False)
    state = _load_state(root)
    record = state.pop(plugin.manifest.id, None)
    if isinstance(record, dict):
        shutil.rmtree(
            root / _BACKUP_DIR / str(record["module_alias"]), ignore_errors=True
        )
    _save_state(root, state)
    print_json_payload(
        {"ok": True, "action": "uninstalled", "plugin_id": plugin.manifest.id}
    )
    return 0


def _add_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        help="Plugin install root. Defaults to the first configured plugin path.",
    )


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    plugins = subparsers.add_parser("plugins", help="Plugin operations")
    plugins_subcommands = plugins.add_subparsers(dest="plugins_command")
    plugins_list = plugins_subcommands.add_parser("list", help="List enabled plugins")
    plugins_list.set_defaults(handler=list_plugins, needs_app=True)

    preview = plugins_subcommands.add_parser(
        "preview",
        help="Inspect plugin metadata and checksum without loading code",
    )
    preview.add_argument("source")
    preview.set_defaults(handler=preview_plugin, needs_app=False)

    install = plugins_subcommands.add_parser("install", help="Install a local plugin")
    install.add_argument("source")
    _add_root(install)
    install.set_defaults(handler=install_plugin, needs_app=False)

    health = plugins_subcommands.add_parser("health", help="Check an installed plugin")
    health.add_argument("plugin_id")
    _add_root(health)
    health.set_defaults(handler=health_plugin, needs_app=False)

    rollback = plugins_subcommands.add_parser(
        "rollback", help="Undo the last plugin install"
    )
    rollback.add_argument("plugin_id")
    _add_root(rollback)
    rollback.set_defaults(handler=rollback_plugin, needs_app=False)

    uninstall = plugins_subcommands.add_parser(
        "uninstall", help="Uninstall a local plugin"
    )
    uninstall.add_argument("plugin_id")
    _add_root(uninstall)
    uninstall.set_defaults(handler=uninstall_plugin, needs_app=False)
