from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from openminion.base.config import OpenMinionConfig
from openminion.base.config.parse import _as_obj
from openminion.base.config.paths import ensure_under_data_root
from openminion.modules.config import (
    is_module_standalone_mode,
    normalize_data_root_relative_path,
    resolve_module_config_path,
    resolve_module_data_root,
    resolve_module_home_root,
)
from openminion.modules.storage.runtime.provider_selection import (
    resolve_storage_provider,
)
from openminion.modules.skill.scalars import parse_scalar
from .constants import (
    DEFAULT_STATUS_FILTER,
    DEFAULT_CONFIG_FILENAME,
    DEFAULT_INTEGRATED_ROOT_SUBPATH,
    DEFAULT_INTEGRATED_SQLITE_SUBPATH,
    DEFAULT_STANDALONE_ROOT_SUBPATH,
    DEFAULT_STANDALONE_SQLITE_SUBPATH,
    HIGH_RISK_STATUS_FILTER,
    OPENMINION_DATA_ROOT_ENV,
    OPENMINION_MODULE_STANDALONE_ENV,
    SKILL_TOOL_REGISTRY_AVAILABLE,
    SKILL_TOOL_REGISTRY_AVAILABLE_EMPTY,
    SKILL_TOOL_REGISTRY_UNAVAILABLE,
)

try:
    import yaml  # type: ignore[import-untyped]
except ModuleNotFoundError:  # pragma: no cover
    yaml = None


@dataclass
class SkillConfig:
    provider: str = "sqlite"
    sqlite_path: str = f"~/{DEFAULT_STANDALONE_SQLITE_SUBPATH}"
    blob_root: str = "~/.skill"
    fallback_root: str = "~/.skill"
    wal: bool = True
    default_status_filter: list[str] = field(
        default_factory=lambda: list(DEFAULT_STATUS_FILTER)
    )
    high_risk_status_filter: list[str] = field(
        default_factory=lambda: list(HIGH_RISK_STATUS_FILTER)
    )
    known_tools: list[str] = field(default_factory=list)
    known_tools_state: str = SKILL_TOOL_REGISTRY_UNAVAILABLE
    allowed_roots: list[str] = field(default_factory=list)
    trust_tier: str = "disabled"
    ingest_enabled: bool = True
    selection_rag_threshold: int = 10
    selection_rag_topk: int = 5
    promotion_cadence_enabled: bool = False
    promotion_cadence_success_threshold: int = 3
    promotion_cadence_utility_threshold: float = 0.7
    suggestion_batch_cap: int = 5
    suggestion_cooldown_seconds: int = 7 * 24 * 60 * 60
    suggestion_min_age_seconds: int = 0
    skill_blob_retention: str = "retain"
    path_mode: str = "module_standalone"
    path_source: str = "standalone_default"
    home_root: str | None = None


def load_config(
    path: str | Path | dict[str, Any] | SkillConfig | None = DEFAULT_CONFIG_FILENAME,
    *,
    home_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> SkillConfig:
    if isinstance(path, SkillConfig):
        return path

    env_map = dict(env or os.environ)
    standalone_mode = is_module_standalone_mode(env_map)
    resolved_home_root = (
        None
        if standalone_mode
        else resolve_module_home_root(home_root, env_map, fallback_to_cwd=True)
    )
    resolved_data_root = (
        resolve_module_data_root(home_root=resolved_home_root, env=env_map)
        if resolved_home_root is not None
        else None
    )

    default_sqlite, default_blob, default_fallback = _default_storage_paths(
        home_root=resolved_home_root,
        data_root=resolved_data_root,
        standalone_mode=standalone_mode,
    )
    default_mode = (
        "module_standalone"
        if standalone_mode
        else "integrated_runtime"
        if resolved_home_root
        else "module_standalone"
    )
    default_source = (
        "standalone_default"
        if standalone_mode or not resolved_home_root
        else "default_integrated"
    )

    if isinstance(path, dict):
        raw = dict(path)
    else:
        candidate = DEFAULT_CONFIG_FILENAME if path is None else str(path)
        cfg_path = resolve_module_config_path(candidate, home_root=resolved_home_root)
        if not cfg_path.exists():
            return SkillConfig(
                provider="sqlite",
                sqlite_path=str(default_sqlite),
                blob_root=str(default_blob),
                fallback_root=str(default_fallback),
                known_tools_state=SKILL_TOOL_REGISTRY_UNAVAILABLE,
                path_mode=default_mode,
                path_source=default_source,
                home_root=str(resolved_home_root) if resolved_home_root else None,
            )

        text = cfg_path.read_text(encoding="utf-8")
        if cfg_path.suffix.lower() == ".json":
            parsed = json.loads(text or "{}")
        else:
            if yaml is None:
                parsed = _parse_yaml_like_mapping(text) or {}
            else:
                parsed = yaml.safe_load(text) or {}

        if not isinstance(parsed, dict):
            raise ValueError("skill config must be a mapping")
        raw = parsed

    root = _as_obj(raw.get("skill"), _as_obj(raw.get("skillctl"), raw))
    provider = resolve_storage_provider(
        module="skill",
        raw_provider=root.get("provider"),
        source_label="skill.provider",
        path_mode=default_mode,
        unsupported_message_builder=(
            lambda provider, _supported, _source: (
                "Unsupported skill storage provider "
                f"{provider!r}. Supported provider: sqlite."
            )
        ),
    )
    sqlite_path_raw = root.get("sqlite_path")
    blob_root_raw = root.get("blob_root")
    fallback_root_raw = root.get("fallback_root")

    sqlite_path = _resolve_storage_path(
        sqlite_path_raw if sqlite_path_raw is not None else default_sqlite,
        home_root=resolved_home_root,
        data_root=resolved_data_root,
        label="skill_sqlite_path",
    )
    default_root = sqlite_path.parent
    blob_root = _resolve_storage_path(
        blob_root_raw if blob_root_raw is not None else default_root,
        home_root=resolved_home_root,
        data_root=resolved_data_root,
        label="skill_blob_root",
    )
    fallback_root = _resolve_storage_path(
        fallback_root_raw if fallback_root_raw is not None else default_root,
        home_root=resolved_home_root,
        data_root=resolved_data_root,
        label="skill_fallback_root",
    )

    explicit_override = any(
        value is not None
        for value in (sqlite_path_raw, blob_root_raw, fallback_root_raw)
    )
    path_source = "explicit_override" if explicit_override else default_source
    path_mode = default_mode
    known_tools = _as_str_list(root.get("known_tools"), [])
    if "known_tools" not in root:
        known_tools_state = SKILL_TOOL_REGISTRY_UNAVAILABLE
    elif known_tools:
        known_tools_state = SKILL_TOOL_REGISTRY_AVAILABLE
    else:
        known_tools_state = SKILL_TOOL_REGISTRY_AVAILABLE_EMPTY

    raw_retention = str(root.get("skill_blob_retention", "retain")).strip()
    skill_blob_retention = (
        raw_retention if raw_retention in {"retain", "gc"} else "retain"
    )

    return SkillConfig(
        provider=provider,
        sqlite_path=str(sqlite_path),
        blob_root=str(blob_root),
        fallback_root=str(fallback_root),
        wal=bool(root.get("wal", True)),
        default_status_filter=_as_str_list(
            root.get("default_status_filter"),
            list(DEFAULT_STATUS_FILTER),
        ),
        high_risk_status_filter=_as_str_list(
            root.get("high_risk_status_filter"),
            list(HIGH_RISK_STATUS_FILTER),
        ),
        known_tools=known_tools,
        known_tools_state=known_tools_state,
        allowed_roots=_as_str_list(root.get("allowed_roots"), []),
        trust_tier=str(root.get("trust_tier", "disabled")),
        ingest_enabled=bool(root.get("ingest_enabled", True)),
        skill_blob_retention=skill_blob_retention,
        path_mode=path_mode,
        path_source=path_source,
        home_root=str(resolved_home_root) if resolved_home_root else None,
    )


def from_base_config(
    *,
    base_config: OpenMinionConfig,
    home_root: Path,
    data_root: Path,
) -> SkillConfig:
    env = dict(getattr(base_config.runtime, "env", {}) or {})
    env.setdefault(OPENMINION_DATA_ROOT_ENV, str(data_root))
    env.pop(OPENMINION_MODULE_STANDALONE_ENV, None)
    module_configs = dict(getattr(base_config, "module_configs", {}) or {})
    raw_config = module_configs.get("skill") or module_configs.get("skillctl") or None
    return load_config(raw_config, home_root=home_root, env=env)


def _resolve_storage_path(
    value: Any,
    *,
    home_root: Path | None,
    data_root: Path | None,
    label: str,
) -> Path:
    path_value = normalize_data_root_relative_path(Path(str(value)).expanduser())
    if path_value.is_absolute():
        if data_root is not None:
            return ensure_under_data_root(path_value, data_root, label=label)
        return path_value.resolve(strict=False)
    if data_root is not None:
        candidate = (data_root / path_value).resolve(strict=False)
        return ensure_under_data_root(candidate, data_root, label=label)
    if home_root is not None:
        return (home_root / path_value).resolve(strict=False)
    return path_value.resolve(strict=False)


def _default_storage_paths(
    *,
    home_root: Path | None,
    data_root: Path | None,
    standalone_mode: bool,
) -> tuple[Path, Path, Path]:
    if data_root is not None and not standalone_mode:
        skill_root = (data_root / DEFAULT_INTEGRATED_ROOT_SUBPATH).resolve(strict=False)
        return (
            (data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH).resolve(strict=False),
            skill_root,
            skill_root,
        )

    skill_root = (Path.home() / DEFAULT_STANDALONE_ROOT_SUBPATH).resolve(strict=False)
    return (
        (Path.home() / DEFAULT_STANDALONE_SQLITE_SUBPATH).resolve(strict=False),
        skill_root,
        skill_root,
    )


def _as_str_list(value: Any, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out or list(default)


def _parse_yaml_like_mapping(text: str) -> dict[str, Any] | None:
    """
    Tiny YAML-like parser for environments without PyYAML.
    Supports nested maps, inline lists, and scalar booleans/numbers/strings.
    """

    lines = text.splitlines()
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any] | list[Any]]] = [(0, root)]

    for idx, raw_line in enumerate(lines):
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()

        while len(stack) > 1 and indent < stack[-1][0]:
            stack.pop()

        container = stack[-1][1]
        if not isinstance(container, dict):
            continue

        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if value == "":
            is_list = _next_block_is_list(lines, idx + 1, indent)
            child: dict[str, Any] | list[Any]
            child = [] if is_list else {}
            container[key] = child
            stack.append((indent + 2, child))
            continue

        container[key] = parse_scalar(value)

    return root


def _next_block_is_list(lines: list[str], start_idx: int, parent_indent: int) -> bool:
    for raw_line in lines[start_idx:]:
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent <= parent_indent:
            break
        return raw_line.strip().startswith("- ")
    return False
