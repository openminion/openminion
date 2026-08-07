from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.e2e.cli.focus.harness.provider_matrix import (
    CERTIFICATION_REPORT_SCHEMA_VERSION,
    CERTIFICATION_RUN_SCHEMA_VERSION,
    load_provider_session_resilience_manifest,
    write_provider_session_resilience_report,
)

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[5]


def _write_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"agents": {"agent": {"provider": "fixture", "model": "m"}}}) + "\n",
        encoding="utf-8",
    )


def _target(
    *,
    config_ref: str,
    adapter: str,
    protocol: str,
    authority: str,
) -> dict[str, object]:
    return {
        "provider_class": {
            "adapter": adapter,
            "api_protocol": protocol,
            "endpoint_authority": authority,
        },
        "config_ref": config_ref,
        "agent_id": "agent",
        "expected_model": "m",
        "required_capabilities": ["chat"],
    }


def _manifest(tmp_path: Path) -> dict[str, object]:
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    _write_config(first)
    _write_config(second)
    return {
        "schema_version": CERTIFICATION_RUN_SCHEMA_VERSION,
        "run_id": "psrc-fixture",
        "targets": [
            _target(
                config_ref=str(first),
                adapter="minimax",
                protocol="openai-compatible",
                authority="api.minimax.io",
            ),
            _target(
                config_ref=str(second),
                adapter="anthropic",
                protocol="anthropic",
                authority="api.anthropic.com",
            ),
        ],
        "injected_failures": [
            {
                "provider_class": "minimax|openai-compatible|api.minimax.io",
                "failure_code": "timeout",
                "retry_eligible": True,
            }
        ],
    }


def _write_manifest(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


def test_provider_session_manifest_accepts_two_classes(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, _manifest(tmp_path))

    manifest = load_provider_session_resilience_manifest(path, root=ROOT)

    assert manifest.run_id == "psrc-fixture"
    assert len(manifest.targets) == 2
    assert len(manifest.injected_failures) == 1


@pytest.mark.parametrize(
    "mutation",
    (
        "one_class",
        "duplicate_class",
        "missing_capabilities",
        "missing_config",
        "secret",
    ),
)
def test_provider_session_manifest_rejects_invalid_cases(
    tmp_path: Path,
    mutation: str,
) -> None:
    payload = _manifest(tmp_path)
    targets = payload["targets"]
    assert isinstance(targets, list)
    if mutation == "one_class":
        payload["targets"] = targets[:1]
    elif mutation == "duplicate_class":
        targets[1]["provider_class"] = targets[0]["provider_class"]
    elif mutation == "missing_capabilities":
        targets[0]["required_capabilities"] = []
    elif mutation == "missing_config":
        targets[0]["config_ref"] = str(tmp_path / "missing.json")
    elif mutation == "secret":
        payload["api_key"] = "sk-test"
    path = _write_manifest(tmp_path, payload)

    with pytest.raises(ValueError):
        load_provider_session_resilience_manifest(path, root=ROOT)


def test_provider_session_report_writes_generated_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv("OPENMINION_GENERATED_ROOT", raising=False)
    path = _write_manifest(tmp_path, _manifest(tmp_path))
    manifest = load_provider_session_resilience_manifest(path, root=ROOT)

    json_path, markdown_path = write_provider_session_resilience_report(
        manifest,
        manifest_path=path,
        root=tmp_path,
        validation_only=True,
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == CERTIFICATION_REPORT_SCHEMA_VERSION
    assert payload["rows"][0]["classification"] == "blocked_external"
    assert payload["injected_failures"][0]["failure_code"] == "timeout"
    assert "api_key" not in json_path.read_text(encoding="utf-8").lower()
    assert markdown_path.read_text(encoding="utf-8").startswith(
        "# Provider Session Resilience Certification"
    )


def test_provider_session_runner_validate_only_writes_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv("OPENMINION_GENERATED_ROOT", raising=False)
    manifest_path = _write_manifest(tmp_path, _manifest(tmp_path))

    result = subprocess.run(
        [
            sys.executable,
            "tests/e2e/runners/run_provider_session_resilience.py",
            "--manifest",
            str(manifest_path),
            "--validate-only",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "psrc-fixture" in result.stdout
    assert "certification-report.json" in result.stdout
