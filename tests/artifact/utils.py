from __future__ import annotations

import copy
import io
import json
from collections.abc import Mapping, MutableMapping
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import Any, Iterator

from openminion.modules.artifact import cli as cli_module
from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.artifact.models import ArtifactMeta, iso_now


def make_config(
    tmp_path: Path, overrides: Mapping[str, Any] | None = None
) -> dict[str, Any]:

    root = tmp_path / "artifact-store"
    base: dict[str, Any] = {
        "artifactctl": {
            "blob_store": {
                "backend": "filesystem_cas",
                "root_dir": str(root),
                "max_ingest_bytes": 104_857_600,
            },
            "index": {
                "backend": "sqlite",
                "sqlite_path": str(root / "index.db"),
                "wal": False,
            },
            "views": {
                "auto_generate": ["digest", "text"],
                "table_max_rows": 200,
                "digest_max_chars": 2000,
                "digest_max_lines": 80,
                "json_max_chars": 20000,
            },
            "aliases": {
                "expire_default_days": 0,
            },
            "retention": {
                "keep_days": 14,
                "delete_unreferenced_after_days": 7,
                "purge_grace_days": 7,
                "delete_views_first": True,
            },
            "security": {
                "store_original_path": False,
                "redaction_enabled": True,
            },
        }
    }

    data = copy.deepcopy(base)
    if overrides:
        data = _deep_merge(data, overrides)
    return data


def write_config_file(
    tmp_path: Path, overrides: Mapping[str, Any] | None = None
) -> Path:
    cfg = make_config(tmp_path, overrides)
    config_path = tmp_path / "artifactctl.json"
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    return config_path


def run_cli_command(config_path: Path, args: list[str]) -> dict[str, Any]:
    buf = io.StringIO()
    argv = ["--config", str(config_path), *args]
    with redirect_stdout(buf):
        cli_module.main(argv)
    output = buf.getvalue().strip() or "{}"
    return json.loads(output)


def fixture_path(name: str) -> Path:
    return Path(__file__).parent / "fixtures" / name


def read_fixture_bytes(name: str) -> bytes:
    return fixture_path(name).read_bytes()


def read_fixture_text(name: str) -> str:
    return fixture_path(name).read_text(encoding="utf-8")


def make_artifact_meta(
    sha256: str,
    *,
    size_bytes: int = 1,
    mime: str = "text/plain",
    created_at: str | None = None,
    original_name: str | None = None,
    label: str | None = None,
    session_id: str | None = None,
    trace_id: str | None = None,
    agent_id: str | None = None,
) -> ArtifactMeta:
    return ArtifactMeta(
        sha256=sha256,
        size_bytes=size_bytes,
        mime=mime,
        created_at=created_at or iso_now(),
        original_name=original_name,
        original_path=None,
        label=label,
        session_id=session_id,
        trace_id=trace_id,
        agent_id=agent_id,
        meta_json=None,
    )


@contextmanager
def artifact_ctl(
    tmp_path: Path, overrides: Mapping[str, Any] | None = None
) -> Iterator[ArtifactCtl]:
    ctl = ArtifactCtl(make_config(tmp_path, overrides))
    try:
        yield ctl
    finally:
        ctl.close()


def _deep_merge(
    target: MutableMapping[str, Any], updates: Mapping[str, Any]
) -> MutableMapping[str, Any]:
    for key, value in updates.items():
        if (
            key in target
            and isinstance(target[key], MutableMapping)
            and isinstance(value, Mapping)
        ):
            target[key] = _deep_merge(target[key], value)  # type: ignore[arg-type]
            continue
        target[key] = copy.deepcopy(value)
    return target
