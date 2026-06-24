import hashlib
import importlib.util
import inspect
import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Sequence

from openminion.services.bootstrap.paths import (
    SERVICES_PLUGIN_MANIFEST_GLOB,
    SERVICES_PLUGIN_MANIFEST_SUFFIX,
)
from openminion.services.agent.hooks import Hook
from .contracts.manifest import (
    HookManifest,
    HookManifestError,
    load_plugin_manifest,
)


class HookDiscoveryError(RuntimeError):
    """Raised when plugin discovery or loading fails."""


@dataclass(frozen=True)
class DiscoveredHook:
    manifest: HookManifest
    manifest_path: Path
    module_path: Path
    source_root: Path
    module_alias: str


def discover_hooks(search_roots: Sequence[Path]) -> list[DiscoveredHook]:
    discovered: list[DiscoveredHook] = []
    normalized_roots = [Path(root).resolve() for root in search_roots]

    for source_root in normalized_roots:
        if not source_root.exists() or not source_root.is_dir():
            continue
        manifest_paths = sorted(source_root.rglob(SERVICES_PLUGIN_MANIFEST_GLOB))
        for manifest_path in manifest_paths:
            try:
                manifest = load_plugin_manifest(manifest_path)
            except HookManifestError as exc:
                raise HookDiscoveryError(
                    "Invalid plugin manifest at "
                    + str(manifest_path)
                    + ": "
                    + "; ".join(exc.errors)
                ) from exc

            module_alias = _module_alias_from_manifest_path(manifest_path)
            module_path = manifest_path.with_name(module_alias + ".py")
            if not module_path.exists() or not module_path.is_file():
                raise HookDiscoveryError(
                    "Hook module is missing for manifest "
                    + str(manifest_path)
                    + " (expected "
                    + str(module_path)
                    + ")"
                )
            discovered.append(
                DiscoveredHook(
                    manifest=manifest,
                    manifest_path=manifest_path.resolve(),
                    module_path=module_path.resolve(),
                    source_root=source_root,
                    module_alias=module_alias,
                )
            )

    return discovered


def load_plugin_instance(discovered: DiscoveredHook) -> Hook:
    module = _import_plugin_module(discovered)
    plugin_classes = _plugin_classes_in_module(module)
    if not plugin_classes:
        raise HookDiscoveryError(
            "No Hook subclass found in module " + str(discovered.module_path)
        )
    if len(plugin_classes) > 1:
        class_names = ", ".join(sorted(item.__name__ for item in plugin_classes))
        raise HookDiscoveryError(
            "Multiple Hook subclasses found in module "
            + str(discovered.module_path)
            + " ("
            + class_names
            + "); keep exactly one."
        )

    plugin_class = plugin_classes[0]
    try:
        instance = plugin_class()
    except Exception as exc:  # pragma: no cover - defensive
        raise HookDiscoveryError(
            "Failed to instantiate plugin class "
            + plugin_class.__name__
            + " from "
            + str(discovered.module_path)
            + ": "
            + str(exc)
        ) from exc

    plugin_name = str(getattr(instance, "name", "")).strip()
    if not plugin_name:
        raise HookDiscoveryError(
            "Hook class "
            + plugin_class.__name__
            + " in "
            + str(discovered.module_path)
            + " has empty `name`."
        )
    return instance


def _import_plugin_module(discovered: DiscoveredHook) -> ModuleType:
    digest = hashlib.md5(str(discovered.module_path).encode("utf-8")).hexdigest()[:12]
    normalized_id = re.sub(r"[^a-zA-Z0-9_]", "_", discovered.manifest.id)
    module_name = f"openminion.services.runtime.dynamic.{normalized_id}_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, discovered.module_path)
    if spec is None or spec.loader is None:
        raise HookDiscoveryError(
            "Unable to create module spec for " + str(discovered.module_path)
        )

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - defensive
        raise HookDiscoveryError(
            "Failed to import plugin module "
            + str(discovered.module_path)
            + ": "
            + str(exc)
        ) from exc
    return module


def _plugin_classes_in_module(module: ModuleType) -> list[type[Hook]]:
    classes: list[type[Hook]] = []
    for value in vars(module).values():
        if not inspect.isclass(value):
            continue
        if value is Hook:
            continue
        if not issubclass(value, Hook):
            continue
        if value.__module__ != module.__name__:
            continue
        classes.append(value)
    return sorted(classes, key=lambda item: item.__name__)


def _module_alias_from_manifest_path(manifest_path: Path) -> str:
    file_name = manifest_path.name
    suffix = SERVICES_PLUGIN_MANIFEST_SUFFIX
    if not file_name.endswith(suffix):
        raise HookDiscoveryError("Unexpected manifest file name: " + file_name)
    alias = file_name[: -len(suffix)].strip()
    if not alias:
        raise HookDiscoveryError(
            "Manifest file name must include module alias: " + file_name
        )
    return alias
