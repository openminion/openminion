from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]
except ModuleNotFoundError:  # pragma: no cover
    yaml = None

from openminion.base.config import OpenMinionConfig, resolve_data_root
from openminion.base.config.env import resolve_environment_config
from openminion.base.config.parse import _as_obj
from openminion.base.config.paths import resolve_config_storage_path
from openminion.modules.config import resolve_module_home_root
from .constants import (
    DEFAULT_INDEX_FILENAME,
    DEFAULT_INTEGRATED_ROOT_SUBPATH,
    DEFAULT_STANDALONE_ROOT_SUBPATH,
)


@dataclass
class BlobStoreConfig:
    backend: str = "filesystem_cas"
    root_dir: str = "~/.artifactctl"
    max_ingest_bytes: int = 104_857_600


@dataclass
class IndexConfig:
    backend: str = "sqlite"
    sqlite_path: str = "~/.artifactctl/index.db"
    wal: bool = True


@dataclass
class ViewsConfig:
    auto_generate: list[str] = field(default_factory=lambda: ["digest", "text"])
    table_max_rows: int = 200
    digest_max_chars: int = 2000
    digest_max_lines: int = 80
    json_max_chars: int = 20_000


@dataclass
class AliasesConfig:
    expire_default_days: int = 30


@dataclass
class RetentionConfig:
    keep_days: int = 14
    delete_unreferenced_after_days: int = 7
    purge_grace_days: int = 7
    delete_views_first: bool = True


@dataclass
class SecurityConfig:
    store_original_path: bool = False
    redaction_enabled: bool = True


@dataclass
class ArtifactCtlConfig:
    blob_store: BlobStoreConfig = field(default_factory=BlobStoreConfig)
    index: IndexConfig = field(default_factory=IndexConfig)
    views: ViewsConfig = field(default_factory=ViewsConfig)
    aliases: AliasesConfig = field(default_factory=AliasesConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)


def from_base_config(
    *,
    base_config: OpenMinionConfig,
    home_root: Path,
    data_root: Path,
) -> ArtifactCtlConfig:
    del base_config
    default_blob_root, default_index_path = _default_blob_and_index_paths(
        home_root, data_root
    )
    return _default_config(default_blob_root, default_index_path)


def load_config(
    path: str | Path | dict[str, Any] | ArtifactCtlConfig,
) -> ArtifactCtlConfig:
    if isinstance(path, ArtifactCtlConfig):
        return path

    env = resolve_environment_config()
    home_root = resolve_module_home_root(None, env, fallback_to_cwd=True)
    data_root = (
        resolve_data_root(home_root, data_root=env.openminion_data_root or None)
        if home_root is not None
        else None
    )
    default_blob_root, default_index_path = _default_blob_and_index_paths(
        home_root, data_root
    )

    if isinstance(path, dict):
        raw = dict(path)
    else:
        cfg_path = Path(path).expanduser().resolve(strict=False)
        if not cfg_path.exists():
            return _default_config(default_blob_root, default_index_path)

        text = cfg_path.read_text(encoding="utf-8")
        if cfg_path.suffix.lower() == ".json":
            parsed = json.loads(text or "{}")
        else:
            if yaml is None:
                raise RuntimeError("PyYAML is required for YAML config files")
            parsed = yaml.safe_load(text)
            if parsed is None:
                parsed = {}

        if not isinstance(parsed, dict):
            raise ValueError("artifactctl config must be a mapping")
        raw = parsed

    root = _as_obj(raw.get("artifactctl"), raw)
    blob_store = _as_obj(root.get("blob_store"), {})
    index = _as_obj(root.get("index"), {})
    views = _as_obj(root.get("views"), {})
    aliases = _as_obj(root.get("aliases"), {})
    retention = _as_obj(root.get("retention"), {})
    security = _as_obj(root.get("security"), {})

    blob_root = resolve_config_storage_path(
        str(blob_store.get("root_dir", default_blob_root)),
        data_root=data_root,
        label="artifact_blob_root",
    )
    sqlite_path = resolve_config_storage_path(
        str(index.get("sqlite_path", default_index_path)),
        data_root=data_root,
        label="artifact_index_path",
    )

    return ArtifactCtlConfig(
        blob_store=BlobStoreConfig(
            backend=str(blob_store.get("backend", "filesystem_cas")),
            root_dir=blob_root,
            max_ingest_bytes=int(blob_store.get("max_ingest_bytes", 104_857_600)),
        ),
        index=IndexConfig(
            backend=str(index.get("backend", "sqlite")),
            sqlite_path=sqlite_path,
            wal=bool(index.get("wal", True)),
        ),
        views=ViewsConfig(
            auto_generate=_as_str_list(views.get("auto_generate"), ["digest", "text"]),
            table_max_rows=int(views.get("table_max_rows", 200)),
            digest_max_chars=int(views.get("digest_max_chars", 2000)),
            digest_max_lines=int(views.get("digest_max_lines", 80)),
            json_max_chars=int(views.get("json_max_chars", 20_000)),
        ),
        aliases=AliasesConfig(
            expire_default_days=int(aliases.get("expire_default_days", 30)),
        ),
        retention=RetentionConfig(
            keep_days=int(retention.get("keep_days", 14)),
            delete_unreferenced_after_days=int(
                retention.get("delete_unreferenced_after_days", 7)
            ),
            purge_grace_days=int(retention.get("purge_grace_days", 7)),
            delete_views_first=bool(retention.get("delete_views_first", True)),
        ),
        security=SecurityConfig(
            store_original_path=bool(security.get("store_original_path", False)),
            redaction_enabled=bool(security.get("redaction_enabled", True)),
        ),
    )


def _as_str_list(value: Any, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    out: list[str] = []
    for item in value:
        text = str(item).strip().lower()
        if text:
            out.append(text)
    return out or list(default)


def _default_blob_root(home_root: Path | None, data_root: Path | None) -> str:
    if home_root is None or data_root is None:
        return str(DEFAULT_STANDALONE_ROOT_SUBPATH)
    return str((data_root / DEFAULT_INTEGRATED_ROOT_SUBPATH).resolve(strict=False))


def _default_blob_and_index_paths(
    home_root: Path | None, data_root: Path | None
) -> tuple[str, str]:
    blob_root = _default_blob_root(home_root, data_root)
    index_path = (
        str(DEFAULT_STANDALONE_ROOT_SUBPATH / DEFAULT_INDEX_FILENAME)
        if home_root is None
        else str((Path(blob_root) / DEFAULT_INDEX_FILENAME).resolve(strict=False))
    )
    return blob_root, index_path


def _default_config(blob_root: str, index_path: str) -> ArtifactCtlConfig:
    return ArtifactCtlConfig(
        blob_store=BlobStoreConfig(root_dir=blob_root),
        index=IndexConfig(sqlite_path=index_path),
    )
