from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e.runners import run_autonomy_smoke as smoke

pytestmark = pytest.mark.e2e


def test_autonomy_smoke_marks_failed_optional_check_truthfully(capsys) -> None:
    suite = smoke.AutonomySmokeSuite.__new__(smoke.AutonomySmokeSuite)
    suite.checks = []

    suite._record_check(
        check_id="optional-probe",
        required=False,
        ok=False,
        summary="optional surface unavailable",
        details={},
    )

    assert "[OPTIONAL] optional-probe" in capsys.readouterr().out


def test_autonomy_smoke_daemon_mode_fails_without_fallback(tmp_path: Path) -> None:
    output_root = tmp_path / "output"

    code = smoke.main(
        [
            "--root",
            str(Path(__file__).resolve().parents[2]),
            "--output-dir",
            str(output_root),
            "--runtime-source",
            "daemon",
            "--timeout-seconds",
            "15",
        ]
    )

    report_path = next(output_root.glob("*/report.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    config = json.loads(Path(report["config_path"]).read_text(encoding="utf-8"))
    run_check = next(check for check in report["checks"] if check["id"] == "run-turn")
    stderr = Path(run_check["details"]["stderr_path"]).read_text(encoding="utf-8")
    report_text = json.dumps(report)

    assert code == 1
    assert config["runtime"]["daemon_auto_start"] is False
    assert "daemon runtime source unavailable" in stderr
    assert '"runtime_source": "inproc"' not in report_text
    assert "runtime_fallback_reason" not in report_text
