from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e.runners import run_daily_assistant_smoke_suite as smoke


def test_daily_assistant_smoke_runner_writes_catalog_shaped_ledger(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = smoke.main(["--output-dir", str(tmp_path)])

    ledger_path = tmp_path / "daily-assistant-smoke-ledger.json"
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    results = {item["scenario_id"]: item for item in payload["results"]}
    assert code == 0
    assert len(results) == 12
    assert payload["schema_version"] == "openminion.daily_assistant_smoke.v1"
    assert payload["summary"]["pass"] == 3
    assert payload["summary"]["blocked_external"] == 9
    assert results["D2AR-S08"]["disposition"] == "blocked_external"
    assert (
        results["D2AR-S08"]["evidence_refs"][0]["kind"] == "reminder_lifecycle_result"
    )
    assert results["D2AR-S09"]["disposition"] == "pass"
    assert results["D2AR-S09"]["evidence_refs"][0]["kind"] == "proactive_noop_result"
    assert results["D2AR-S10"]["evidence_refs"][0]["kind"] == "memory_control_result"
    assert results["D2AR-S12"]["evidence_refs"][0]["kind"] == "readiness_payload"
    assert results["D2AR-S01"]["missing_prerequisite"] == (
        "operator did not pass --include-live"
    )
    assert "daily assistant smoke:" in capsys.readouterr().out


def test_daily_assistant_smoke_pass_requires_structured_evidence() -> None:
    with pytest.raises(ValueError, match="pass requires structured evidence"):
        smoke.ScenarioResult(
            scenario_id="D2AR-S99",
            disposition="pass",
            owner="D2AR-16",
            message="Assistant said it worked.",
        )


def test_daily_assistant_smoke_blocked_external_requires_prerequisite() -> None:
    with pytest.raises(ValueError, match="missing_prerequisite"):
        smoke.ScenarioResult(
            scenario_id="D2AR-S99",
            disposition="blocked_external",
            owner="D2AR-16",
        )


def test_daily_assistant_smoke_catalog_loader_consumes_all_scenarios() -> None:
    definitions = smoke._load_catalog_definitions(smoke.CATALOG_PATH)

    assert [definition.scenario_id for definition in definitions] == [
        f"D2AR-S{idx:02d}" for idx in range(1, 13)
    ]
    assert all("D2AR-16" in definition.owners for definition in definitions)
    assert all(
        set(definition.allowed_dispositions) == smoke.DISPOSITIONS
        for definition in definitions
    )
