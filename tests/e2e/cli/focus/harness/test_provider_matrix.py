from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e.cli.focus.harness.provider_matrix import (
    build_provider_matrix,
    load_provider_matrix,
    write_provider_matrix,
)

pytestmark = pytest.mark.e2e


def _write_config(path: Path, *, env_name: str | None = None) -> None:
    value = "model-direct" if env_name is None else f"${{{env_name}}}"
    path.write_text(
        json.dumps(
            {
                "agents": {
                    "minimax-m2-7": {"provider": "minimax", "model": value},
                    "hello-agent": {"provider": "openrouter", "model": value},
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_provider_matrix_writes_versioned_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("OPENMINION_GENERATED_ROOT", str(tmp_path / "data" / "runtime"))
    for relative in (
        "test-configs/per-agent-minimax-official.json",
        "test-configs/per-agent-openrouter-gpt-4o-mini.json",
        "test-configs/per-agent-openrouter-claude-haiku-4-5.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_config(path)

    matrix = build_provider_matrix(root=tmp_path, run_id="matrix-test")
    json_path, markdown_path = write_provider_matrix(matrix, root=tmp_path)
    loaded = load_provider_matrix(json_path)

    assert loaded.schema_version == "session-context-provider-matrix.v1"
    assert len(loaded.rows) == 3
    assert {row.provider_class for row in loaded.rows} == {
        "minimax-direct",
        "openrouter-gpt4o-mini",
        "openrouter-haiku45",
    }
    assert markdown_path.read_text(encoding="utf-8").startswith(
        "# Session Context Provider Matrix"
    )
    assert all(row.redaction_status == "redacted" for row in loaded.rows)


def test_provider_matrix_classifies_missing_config_as_external_blocker(
    tmp_path: Path,
) -> None:
    matrix = build_provider_matrix(root=tmp_path, run_id="missing-config")

    assert {row.classification for row in matrix.rows} == {"blocked_external"}
    assert {row.failure_code for row in matrix.rows} == {"config_missing"}


def test_provider_matrix_classifies_missing_env_placeholder(tmp_path: Path) -> None:
    config_path = tmp_path / "test-configs/per-agent-minimax-official.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    _write_config(config_path, env_name="SCMU_TEST_PROVIDER_KEY")

    matrix = build_provider_matrix(root=tmp_path, run_id="missing-env")
    row = next(item for item in matrix.rows if item.provider_class == "minimax-direct")

    assert row.classification == "blocked_external"
    assert row.failure_code == "missing_env:SCMU_TEST_PROVIDER_KEY"
    assert "SCMU_TEST_PROVIDER_KEY" not in Path(row.transcript_ref).read_text(
        encoding="utf-8"
    )
