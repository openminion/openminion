from __future__ import annotations

import os
from pathlib import Path

import pytest

from openminion.modules.artifact.config import ArtifactCtlConfig, load_config

from .utils import make_config, write_config_file


def test_load_config_defaults_when_file_missing(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "missing.yaml")
    assert isinstance(cfg, ArtifactCtlConfig)
    assert cfg.blob_store.backend == "filesystem_cas"
    data_root_env = os.environ.get("OPENMINION_DATA_ROOT")
    if data_root_env:
        data_root = Path(data_root_env).resolve(strict=False)
    else:
        data_root = (Path(os.environ["OPENMINION_HOME"]) / ".openminion").resolve(
            strict=False
        )
    expected_root = (data_root / "artifact").resolve(strict=False)
    assert cfg.blob_store.root_dir == str(expected_root)
    assert cfg.index.sqlite_path == str(expected_root / "index.db")
    assert cfg.blob_store.max_ingest_bytes == 104_857_600
    assert cfg.views.auto_generate == ["digest", "text"]


def test_load_config_from_json_applies_overrides(tmp_path: Path) -> None:
    overrides = {
        "artifactctl": {
            "blob_store": {
                "root_dir": str(tmp_path / "custom-root"),
                "max_ingest_bytes": 2048,
            },
            "views": {
                "auto_generate": ["DIGEST", "TEXT", "TABLE"],
                "json_max_chars": 512,
            },
            "aliases": {"expire_default_days": 5},
        }
    }
    cfg_path = write_config_file(tmp_path, overrides)
    cfg = load_config(cfg_path)

    assert cfg.blob_store.root_dir.endswith("custom-root")
    assert cfg.blob_store.max_ingest_bytes == 2048
    assert cfg.views.auto_generate == ["digest", "text", "table"]
    assert cfg.views.json_max_chars == 512
    assert cfg.aliases.expire_default_days == 5


def test_load_config_raises_for_non_mapping_file(tmp_path: Path) -> None:
    config_path = tmp_path / "artifact.yaml"
    config_path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(config_path)


def test_load_config_accepts_mapping_input(tmp_path: Path) -> None:
    cfg_dict = make_config(tmp_path, {"artifactctl": {"retention": {"keep_days": 3}}})
    cfg = load_config(cfg_dict)
    assert cfg.retention.keep_days == 3
    assert cfg.retention.purge_grace_days == 7
