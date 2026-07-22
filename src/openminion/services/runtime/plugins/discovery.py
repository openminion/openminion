import hashlib
import importlib.util
import inspect
import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from collections.abc import Sequence

from openminion.services.bootstrap.paths import (
    SERVICES_PLUGIN_MANIFEST_GLOB,
    SERVICES_PLUGIN_MANIFEST_SUFFIX,
)
from openminion.services.runtime.plugins.hooks import Plugin
from openminion.services.runtime.plugins.manifests import (
    PluginManifest,
    PluginManifestError,
    load_plugin_manifest,
)


class PluginDiscoveryError(RuntimeError):
    """Raised when plugin discovery or loading fails."""


@dataclass(frozen=True)
class DiscoveredPlugin:
    manifest: PluginManifest
    manifest_path: Path
    module_path: Path
    source_root: Path
    module_alias: str


def discover_plugin_manifests(search_roots: Sequence[Path]) -> list[DiscoveredPlugin]:
    discovered: list[DiscoveredPlugin] = []
    normalized_roots = [Path(root).resolve() for root in search_roots]

    for source_root in normalized_roots:
        if not source_root.exists() or not source_root.is_dir():
            continue
        manifest_paths = sorted(source_root.rglob(SERVICES_PLUGIN_MANIFEST_GLOB))
        for manifest_path in manifest_paths:
            try:
                manifest = load_plugin_manifest(manifest_path)
            except PluginManifestError as exc:
                raise PluginDiscoveryError(
                    "Invalid plugin manifest at "
                    + str(manifest_path)
                    + ": "
                    + "; ".join(exc.errors)
                ) from exc

            module_alias = _module_alias_from_manifest_path(manifest_path)
            module_path = manifest_path.with_name(module_alias + ".py")
            if not module_path.exists() or not module_path.is_file():
                raise PluginDiscoveryError(
                    "Plugin module is missing for manifest "
                    + str(manifest_path)
                    + " (expected "
                    + str(module_path)
                    + ")"
                )
            discovered.append(
                DiscoveredPlugin(
                    manifest=manifest,
                    manifest_path=manifest_path.resolve(),
                    module_path=module_path.resolve(),
                    source_root=source_root,
                    module_alias=module_alias,
                )
            )

    return discovered


def load_plugin_instance(discovered: DiscoveredPlugin) -> Plugin:
    module = _import_plugin_module(discovered)
    plugin_classes = _plugin_classes_in_module(module)
    if not plugin_classes:
        raise PluginDiscoveryError(
            "No Plugin subclass found in module " + str(discovered.module_path)
        )
    if len(plugin_classes) > 1:
        class_names = ", ".join(sorted(item.__name__ for item in plugin_classes))
        raise PluginDiscoveryError(
            "Multiple Plugin subclasses found in module "
            + str(discovered.module_path)
            + " ("
            + class_names
            + "); keep exactly one."
        )

    plugin_class = plugin_classes[0]
    try:
        instance = plugin_class()
    except Exception as exc:  # pragma: no cover - defensive
        raise PluginDiscoveryError(
            "Failed to instantiate plugin class "
            + plugin_class.__name__
            + " from "
            + str(discovered.module_path)
            + ": "
            + str(exc)
        ) from exc

    plugin_name = str(getattr(instance, "name", "")).strip()
    if not plugin_name:
        raise PluginDiscoveryError(
            "Plugin class "
            + plugin_class.__name__
            + " in "
            + str(discovered.module_path)
            + " has empty `name`."
        )
    return instance


def _import_plugin_module(discovered: DiscoveredPlugin) -> ModuleType:
    digest = hashlib.md5(str(discovered.module_path).encode("utf-8")).hexdigest()[:12]
    normalized_id = re.sub(r"[^a-zA-Z0-9_]", "_", discovered.manifest.id)
    module_name = f"openminion.extensions.dynamic.{normalized_id}_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, discovered.module_path)
    if spec is None or spec.loader is None:
        raise PluginDiscoveryError(
            "Unable to create module spec for " + str(discovered.module_path)
        )

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - defensive
        raise PluginDiscoveryError(
            "Failed to import plugin module "
            + str(discovered.module_path)
            + ": "
            + str(exc)
        ) from exc
    return module


def _plugin_classes_in_module(module: ModuleType) -> list[type[Plugin]]:
    classes: list[type[Plugin]] = []
    for value in vars(module).values():
        if not inspect.isclass(value):
            continue
        if value is Plugin:
            continue
        if not issubclass(value, Plugin):
            continue
        if value.__module__ != module.__name__:
            continue
        classes.append(value)
    return sorted(classes, key=lambda item: item.__name__)


def _module_alias_from_manifest_path(manifest_path: Path) -> str:
    file_name = manifest_path.name
    suffix = SERVICES_PLUGIN_MANIFEST_SUFFIX
    if not file_name.endswith(suffix):
        raise PluginDiscoveryError("Unexpected manifest file name: " + file_name)
    alias = file_name[: -len(suffix)].strip()
    if not alias:
        raise PluginDiscoveryError(
            "Manifest file name must include module alias: " + file_name
        )
    return alias
