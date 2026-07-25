from __future__ import annotations

from pathlib import Path

from scripts.smoke.toolshortlist_measurement import (
    ARTIFACT_VERSION,
    run_measurement,
    write_artifact,
)


def test_measurement_records_paired_enabled_disabled_samples() -> None:
    artifact = run_measurement(samples=5, latency_ms=0.0)

    assert artifact["artifact_schema_version"] == ARTIFACT_VERSION
    assert artifact["samples_per_scenario"] == 5
    assert artifact["tool_inventory_count"] > artifact["threshold"]
    assert len(artifact["pairs"]) == 10
    for pair in artifact["pairs"]:
        assert pair["disabled"]["mode"] == "disabled"
        assert pair["enabled"]["mode"] == "enabled"
        assert pair["disabled"]["correct"] is True
        assert pair["enabled"]["correct"] is True
        assert pair["enabled"]["extra_model_calls"] == 1
        assert pair["enabled"]["prompt_tool_schema_token_proxy"] < pair["disabled"][
            "prompt_tool_schema_token_proxy"
        ]


def test_measurement_recommendation_routes_policy_to_ptss() -> None:
    artifact = run_measurement(samples=5, latency_ms=0.0)

    assert artifact["recommendation"]["decision"] == "route_to_ptss"
    assert "PTSS-02" in artifact["recommendation"]["ptss_routing"]


def test_measurement_writes_artifact(tmp_path: Path) -> None:
    output = tmp_path / "measurement.json"
    path = write_artifact(run_measurement(samples=1, latency_ms=0.0), output=output)

    assert path == output
    assert output.read_text().startswith("{\n")
