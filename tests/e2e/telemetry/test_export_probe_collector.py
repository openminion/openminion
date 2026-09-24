from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time

import pytest

from openminion.base.config.base import DEFAULT_CONFIG_DIR, DEFAULT_CONFIG_FILENAME

from . import conftest as collector_setup
from .assert_collector_evidence import assert_collector_evidence

pytestmark = pytest.mark.e2e


def test_unavailable_collector_keeps_prior_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = tmp_path / "prior-evidence.json"
    prior.write_text("saved", encoding="utf-8")
    monkeypatch.setattr(collector_setup, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(
        collector_setup,
        "_require_docker_daemon",
        lambda: pytest.skip("Docker unavailable"),
    )

    with pytest.raises(pytest.skip.Exception, match="Docker unavailable"):
        next(collector_setup.collector_artifacts.__wrapped__())

    assert prior.read_text(encoding="utf-8") == "saved"
    assert list(tmp_path.iterdir()) == [prior]


def test_live_export_probe_reaches_pinned_collector(
    collector_artifacts: Path,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / DEFAULT_CONFIG_DIR / DEFAULT_CONFIG_FILENAME
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "runtime": {
                    "telemetry_exporter": {
                        "enabled": True,
                        "endpoint": "http://127.0.0.1:14317",
                        "protocol": "grpc",
                        "service_name": "openminion-export-probe-e2e",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            str(Path(__file__).parents[3] / ".venv" / "bin" / "telemetryctl"),
            "--home-root",
            str(tmp_path),
            "--data-root",
            str(tmp_path / "data"),
            "doctor",
            "--live-export",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "ready"
    assert payload["proof"]["otlp_transport"] is True
    assert payload["probe"]["signal"] == "logs"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            assert_collector_evidence(collector_artifacts, require_probe=True)
            break
        except AssertionError:
            time.sleep(0.25)
    else:
        assert_collector_evidence(collector_artifacts, require_probe=True)
