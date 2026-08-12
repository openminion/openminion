from __future__ import annotations

import json
from pathlib import Path

import pytest

from .assert_collector_evidence import assert_collector_evidence


def _write_probe(root: Path, *, copies: int = 1) -> None:
    root.mkdir(parents=True, exist_ok=True)
    record = {
        "body": {"stringValue": "telemetry.export.probe"},
        "attributes": [
            {
                "key": "openminion.event_type",
                "value": {"stringValue": "telemetry.export.probe"},
            },
            {
                "key": "openminion.telemetry.probe",
                "value": {"boolValue": True},
            },
            {
                "key": "openminion.payload.criticality",
                "value": {"stringValue": "diagnostic"},
            },
            {
                "key": "openminion.payload.protocol",
                "value": {"stringValue": "grpc"},
            },
        ],
    }
    (root / "logs.json").write_text(
        "\n".join(json.dumps({"records": [record] * copies}) for _ in range(1)),
        encoding="utf-8",
    )


def test_assertion_accepts_one_content_free_probe(tmp_path: Path) -> None:
    _write_probe(tmp_path)
    assert_collector_evidence(tmp_path, require_probe=True)


@pytest.mark.parametrize("case", ["missing", "empty", "invalid", "duplicate"])
def test_assertion_rejects_bad_probe_artifacts(tmp_path: Path, case: str) -> None:
    if case == "empty":
        (tmp_path / "logs.json").write_text("", encoding="utf-8")
    elif case == "invalid":
        (tmp_path / "logs.json").write_text("not-json", encoding="utf-8")
    elif case == "duplicate":
        _write_probe(tmp_path, copies=2)
    with pytest.raises(AssertionError):
        assert_collector_evidence(tmp_path, require_probe=True)


def test_assertion_rejects_synthetic_only_lifecycle(tmp_path: Path) -> None:
    _write_probe(tmp_path)
    (tmp_path / "traces.json").write_text(
        json.dumps({"attributes": {"invocation_id": "invocation-1"}}),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="synthetic-only"):
        assert_collector_evidence(tmp_path, invocation_ids=("invocation-1",))
