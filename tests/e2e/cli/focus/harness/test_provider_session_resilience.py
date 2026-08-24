from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.e2e.runners import run_provider_session_resilience as runner
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
        "messages": [
            "Remember the marker psrc-continuity-ok. Reply only READY.",
            "Reply only with the marker I asked you to remember.",
        ],
        "required_output_marker": "psrc-continuity-ok",
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
        "missing_messages",
        "missing_marker",
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
    if mutation == "missing_messages":
        payload.pop("messages")
    elif mutation == "missing_marker":
        payload.pop("required_output_marker")
    elif mutation == "one_class":
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
    assert "<turn-1>" not in payload["rows"][0]["command"]
    assert "psrc-continuity-ok" not in payload["rows"][0]["command"]
    assert "<redacted-message-1>" in payload["rows"][0]["command"]
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


def test_provider_session_runner_collects_two_live_turns_per_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv("OPENMINION_GENERATED_ROOT", raising=False)
    monkeypatch.setattr(runner, "isolate_runtime_roots", lambda **_kwargs: tmp_path)
    manifest_path = _write_manifest(tmp_path, _manifest(tmp_path))
    commands: list[list[str]] = []

    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="done\n", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    exit_code = runner.main(["--manifest", str(manifest_path)])
    output = capsys.readouterr().out
    report_path = Path(output.splitlines()[0].split(": ", 1)[1])
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert len(commands) == 2
    assert all(command.count("--message") == 2 for command in commands)
    assert all(command.count("--session") == 1 for command in commands)
    assert all(
        command[-2:] == ["--require-output-marker", "psrc-continuity-ok"]
        for command in commands
    )
    assert [row["classification"] for row in report["rows"]] == ["pass", "pass"]


def test_provider_session_runner_sanitizes_failed_probe_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv("OPENMINION_GENERATED_ROOT", raising=False)
    monkeypatch.setattr(runner, "isolate_runtime_roots", lambda **_kwargs: tmp_path)
    manifest_path = _write_manifest(tmp_path, _manifest(tmp_path))
    results = iter(
        (
            subprocess.CompletedProcess(
                [],
                124,
                stdout=(
                    "api_key=never-persist-this\n"
                    "[probe-status] phase=turn_timeout exit_code=124\n"
                ),
                stderr="Bearer never-persist-this",
            ),
            subprocess.CompletedProcess([], 0, stdout="done\n", stderr=""),
        )
    )
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *_args, **_kwargs: next(results)
    )

    exit_code = runner.main(["--manifest", str(manifest_path)])
    output = capsys.readouterr().out
    report_path = Path(output.splitlines()[0].split(": ", 1)[1])
    report_text = report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)

    assert exit_code == 1
    assert report["rows"][0]["classification"] == "provider_residual"
    assert report["rows"][0]["failure_code"] == "turn_timeout"
    assert "never-persist-this" not in report_text


@pytest.mark.parametrize(
    ("returncode", "phase", "classification", "failure_code"),
    (
        (1, "durable_turn_completed", "provider_residual", "continuity_oracle_failed"),
        (124, "startup_timeout", "runtime_regression", "startup_timeout"),
    ),
)
def test_provider_session_runner_classifies_probe_requirement_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    returncode: int,
    phase: str,
    classification: str,
    failure_code: str,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv("OPENMINION_GENERATED_ROOT", raising=False)
    monkeypatch.setattr(runner, "isolate_runtime_roots", lambda **_kwargs: tmp_path)
    manifest_path = _write_manifest(tmp_path, _manifest(tmp_path))
    results = iter(
        (
            subprocess.CompletedProcess(
                [],
                returncode,
                stdout=f"[probe-status] phase={phase} exit_code={returncode}\n",
                stderr="probe requirement failed: missing required assistant output marker",
            ),
            subprocess.CompletedProcess([], 0, stdout="done\n", stderr=""),
        )
    )
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *_args, **_kwargs: next(results)
    )

    exit_code = runner.main(["--manifest", str(manifest_path)])
    output = capsys.readouterr().out
    report_path = Path(output.splitlines()[0].split(": ", 1)[1])
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["rows"][0]["classification"] == classification
    assert report["rows"][0]["failure_code"] == failure_code
