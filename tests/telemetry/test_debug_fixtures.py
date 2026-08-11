from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).with_name("fixtures") / "debug"


def _fixtures() -> dict[str, object]:
    return {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(ROOT.glob("*.json"))
    }


def test_debug_fixture_inventory_and_schema_versions_are_locked() -> None:
    fixtures = _fixtures()
    assert set(fixtures) == {
        "bundle-manifest.json",
        "bundle-result.json",
        "debug-failed.json",
        "debug-latest.json",
        "doctor.json",
        "invocation-graph.json",
        "rich-card.json",
        "selector-error.json",
        "terminal-source.json",
        "trace-summary.json",
    }
    assert fixtures["doctor.json"]["schema_version"] == (
        "openminion.telemetry_export_smoke.v1"
    )
    for name in ("debug-latest.json", "debug-failed.json", "selector-error.json"):
        assert fixtures[name]["schema_version"] == "openminion.telemetry_debug.v1"
    assert fixtures["bundle-manifest.json"]["schema_version"] == (
        "openminion.telemetry_debug_bundle.v1"
    )
    assert fixtures["bundle-result.json"]["schema_version"] == (
        "openminion.telemetry_debug_bundle_result.v1"
    )


def test_debug_fixtures_are_synthetic_and_privacy_safe() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(ROOT.glob("*.json"))
    )
    assert not re.search(r"/(?:Users|home|private|var)/", text)
    assert not re.search(r"https?://", text)
    assert not re.search(r"(?i)(authorization|api[_-]?key|password|secret)[=:]", text)
    for prohibited in (
        "prompt",
        "completion content",
        "free-form",
        "hostname",
        "username",
        "process_id",
        "endpoint_url",
    ):
        assert prohibited not in text
    assert "synthetic" in text
    assert "bundle-synthetic-1" in text


def test_fixture_structural_fields_remain_usable() -> None:
    fixtures = _fixtures()
    latest = fixtures["debug-latest.json"]
    assert latest["invocation"]["invocation_id"] == "invocation-synthetic-1"
    assert latest["selection"]["high_water_storage_sequence"] == 2
    assert fixtures["trace-summary.json"]["files"][0]["path"].endswith(
        "-structured.json"
    )
    assert fixtures["bundle-manifest.json"]["files"][0]["mode"] == "0600"
    assert "latest failed invocation" in fixtures["rich-card.json"]["body"]
