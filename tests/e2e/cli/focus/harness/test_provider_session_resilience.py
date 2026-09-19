from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.e2e.runners import run_provider_session_resilience as runner
from tests.e2e.cli.focus.harness.provider_matrix import (
    CERTIFICATION_REPORT_SCHEMA_VERSION,
    CERTIFICATION_RUN_SCHEMA_VERSION,
    SCHEMA_VERSION,
    _render_certification_markdown,
    load_provider_session_resilience_manifest,
    write_provider_session_resilience_report,
)

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[5]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_config(path: Path, *, model: str, base_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "agents": {"agent": {"provider": "openai", "model": model}},
                "default_agent": "agent",
                "providers": {
                    "openai": {
                        "model": model,
                        "base_url": base_url,
                        "provider_identity": {
                            "transport_adapter": "openai_chat",
                            "wire_protocol_family": "openai_compatible",
                            "service_vendor": "openai",
                        },
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _write_catalog(path: Path, *, model: str, endpoint: str) -> None:
    path.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "id": "profile",
                        "provider": "openai",
                        "model": model,
                        "endpoint": endpoint,
                        "supports_json": True,
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _target(
    *,
    config_ref: str,
    authority: str,
) -> dict[str, object]:
    config_path = Path(config_ref)
    catalog_path = config_path.with_name(f"{config_path.stem}-catalog.json")
    model = f"model-{authority.split('.', 1)[0]}"
    base_url = f"https://{authority}/v1"
    _write_config(config_path, model=model, base_url=base_url)
    _write_catalog(catalog_path, model=model, endpoint=base_url)
    return {
        "provider_class": {
            "adapter": "openai_chat",
            "api_protocol": "openai_compatible",
            "endpoint_authority": authority,
        },
        "config_ref": config_ref,
        "config_sha256": _digest(config_path),
        "agent_id": "agent",
        "catalog_ref": str(catalog_path),
        "catalog_sha256": _digest(catalog_path),
        "profile_id": "profile",
        "expected_model": model,
        "required_capabilities": ["json"],
        "timeout_seconds": 30,
    }


def _manifest(tmp_path: Path) -> dict[str, object]:
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
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
                authority="one.example",
            ),
            _target(
                config_ref=str(second),
                authority="two.example",
            ),
        ],
        "injected_failures": [
            {
                "provider_class": "openai_chat|openai_compatible|one.example",
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


def _completed_attempts(target: object) -> list[dict[str, object]]:
    return [
        {
            "event_type": "llm.call.completed",
            "status": "completed",
            "turn_id": turn_id,
            "agent_id": target.agent_id,
            "provider_name": target.provider_name,
            "service_vendor": target.service_vendor,
            "model": target.expected_model,
            "session_id": f"psrc-fixture:{target.provider_class}",
            "llm_call_id": f"call-{turn_id}",
        }
        for turn_id in ("turn-1", "turn-2")
    ]


@pytest.mark.parametrize(
    ("mutation", "failure_code"),
    (
        ("missing", "provider_attempts_missing"),
        ("malformed", "provider_attempts_malformed"),
        ("single_turn", "distinct_completed_turns_missing"),
        ("wrong_agent", "completed_attempt_agent_id_mismatch"),
        ("wrong_provider", "completed_attempt_provider_name_mismatch"),
        ("wrong_service", "completed_attempt_service_vendor_mismatch"),
        ("wrong_model", "completed_attempt_model_mismatch"),
        ("wrong_session", "completed_attempt_session_id_mismatch"),
        ("malformed_turn", "completed_attempt_turn_id_missing"),
    ),
)
def test_completed_provider_attempts_fail_closed(
    tmp_path: Path,
    mutation: str,
    failure_code: str,
) -> None:
    manifest = load_provider_session_resilience_manifest(
        _write_manifest(tmp_path, _manifest(tmp_path)),
        root=ROOT,
    )
    target = manifest.targets[0]
    attempts: object = _completed_attempts(target)
    if mutation == "missing":
        attempts = None
    elif mutation == "malformed":
        attempts = ["not-an-attempt"]
    elif mutation == "single_turn":
        attempts = list(attempts)[:1]
    elif mutation == "malformed_turn":
        attempts[0]["turn_id"] = ["turn-1"]
    else:
        field = {
            "wrong_agent": "agent_id",
            "wrong_provider": "provider_name",
            "wrong_service": "service_vendor",
            "wrong_model": "model",
            "wrong_session": "session_id",
        }[mutation]
        list(attempts)[0][field] = "wrong"

    assert (
        runner._completed_attempt_failure(
            target, attempts, f"psrc-fixture:{target.provider_class}"
        )
        == failure_code
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "one_class",
        "duplicate_class",
        "missing_capabilities",
        "missing_config",
        "missing_messages",
        "missing_marker",
        "config_hash_mismatch",
        "catalog_hash_mismatch",
        "free_form_capability",
        "invalid_timeout",
        "three_messages",
        "three_targets",
        "secret",
        "uppercase_config_hash",
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
    elif mutation == "config_hash_mismatch":
        targets[0]["config_sha256"] = "0" * 64
    elif mutation == "catalog_hash_mismatch":
        targets[0]["catalog_sha256"] = "0" * 64
    elif mutation == "free_form_capability":
        targets[0]["required_capabilities"] = ["chat"]
    elif mutation == "invalid_timeout":
        targets[0]["timeout_seconds"] = 0
    elif mutation == "three_messages":
        payload["messages"].append("third")
    elif mutation == "three_targets":
        targets.append(dict(targets[1]))
    elif mutation == "secret":
        payload["api_key"] = "sk-test"
    elif mutation == "uppercase_config_hash":
        targets[0]["config_sha256"] = str(targets[0]["config_sha256"]).upper()
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
        validation_only=True,
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == CERTIFICATION_REPORT_SCHEMA_VERSION
    assert payload["rows"][0]["classification"] == "blocked_external"
    assert "manifest_path" not in payload
    assert "injected_failures" not in payload
    assert "<turn-1>" not in payload["rows"][0]["command"]
    assert "psrc-continuity-ok" not in payload["rows"][0]["command"]
    assert "<redacted-message-1>" in payload["rows"][0]["command"]
    assert payload["planned_injected_failure_count"] == 1
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
    manifest_path = _write_manifest(tmp_path, _manifest(tmp_path))
    commands: list[list[str]] = []
    outer_timeouts: list[float] = []

    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        outer_timeouts.append(float(_kwargs["timeout"]))
        summary_path = Path(command[command.index("--summary-output") + 1])
        agent_id = command[command.index("--agent") + 1]
        model = f"model-{('one' if len(commands) == 1 else 'two')}"
        summary_path.write_text(
            json.dumps(
                {
                    "provider_attempts": [
                        {
                            "event_type": "llm.call.completed",
                            "status": "completed",
                            "turn_id": "turn-1",
                            "agent_id": agent_id,
                            "provider_name": "openai",
                            "service_vendor": "openai",
                            "model": model,
                            "session_id": command[command.index("--session") + 1],
                        },
                        {
                            "event_type": "llm.call.completed",
                            "status": "completed",
                            "turn_id": "turn-2",
                            "agent_id": agent_id,
                            "provider_name": "openai",
                            "service_vendor": "openai",
                            "model": model,
                            "session_id": command[command.index("--session") + 1],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
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
    assert all("--require-final-output-marker" in command for command in commands)
    assert all("--timeout" in command for command in commands)
    assert outer_timeouts == [60.0, 60.0]
    assert [row["classification"] for row in report["rows"]] == ["pass", "pass"]


def test_provider_session_runner_sanitizes_failed_probe_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv("OPENMINION_GENERATED_ROOT", raising=False)
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
    assert report["rows"][0]["classification"] == "inconclusive"
    assert report["rows"][0]["failure_code"] == "probe_report_missing"
    assert report["rows"][0]["probe_phase"] == "turn_timeout"
    assert report["rows"][0]["probe_exit_code"] == 124
    assert "never-persist-this" not in report_text


@pytest.mark.parametrize(
    ("returncode", "phase", "classification", "failure_code"),
    (
        (1, "durable_turn_completed", "inconclusive", "probe_report_missing"),
        (124, "startup_timeout", "inconclusive", "probe_report_missing"),
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


@pytest.mark.parametrize(
    ("phase", "attempt_kind", "classification", "failure_code"),
    (
        ("turn_timeout", "completed", "inconclusive", "turn_timeout"),
        ("startup_timeout", "completed", "inconclusive", "startup_timeout"),
        ("unknown_phase", "completed", "inconclusive", "unknown_phase"),
        (
            "unknown_phase",
            "missing",
            "inconclusive",
            "provider_attempts_missing_or_malformed",
        ),
        (
            "unknown_phase",
            "malformed",
            "inconclusive",
            "provider_attempts_missing_or_malformed",
        ),
        (
            "durable_turn_completed",
            "completed",
            "inconclusive",
            "continuity_oracle_failed",
        ),
        ("turn_timeout", "TIMEOUT", "provider_residual", "TIMEOUT"),
        ("unknown_phase", "AUTH_ERROR", "provider_residual", "AUTH_ERROR"),
        ("unknown_phase", "RATE_LIMITED", "provider_residual", "RATE_LIMITED"),
        ("unknown_phase", "INTERNAL_ERROR", "inconclusive", "unknown_phase"),
        ("unknown_phase", "PROVIDER_ERROR", "inconclusive", "unknown_phase"),
        ("unknown_phase", "foreign_session", "inconclusive", "unknown_phase"),
        ("unknown_phase", "missing_turn", "inconclusive", "unknown_phase"),
        ("unknown_phase", "missing_call", "inconclusive", "unknown_phase"),
        ("unknown_phase", "recovered", "inconclusive", "unknown_phase"),
        ("config_env_missing", "missing", "blocked_external", "config_env_missing"),
    ),
)
def test_probe_classification_requires_supported_cause(
    tmp_path: Path,
    phase: str,
    attempt_kind: str,
    classification: str,
    failure_code: str,
) -> None:
    manifest = load_provider_session_resilience_manifest(
        _write_manifest(tmp_path, _manifest(tmp_path)), root=ROOT
    )
    target = manifest.targets[0]
    attempts: object = _completed_attempts(target)
    if attempt_kind == "missing":
        attempts = None
    elif attempt_kind == "malformed":
        attempts = ["not an attempt"]
    elif attempt_kind != "completed":
        failed = {
            **_completed_attempts(target)[-1],
            "event_type": "llm.call.failed",
            "status": "failed",
            "error_code": "TIMEOUT",
        }
        if attempt_kind == "foreign_session":
            failed["session_id"] = "unrelated-session"
        elif attempt_kind == "missing_turn":
            failed.pop("turn_id")
        elif attempt_kind == "missing_call":
            failed.pop("llm_call_id")
        elif attempt_kind != "recovered":
            failed["error_code"] = attempt_kind
        attempts = [failed]
        if attempt_kind == "recovered":
            attempts.extend(_completed_attempts(target))
    assert runner._probe_classification(
        target=target,
        session_id=f"psrc-fixture:{target.provider_class}",
        returncode=1,
        phase=phase,
        provider_attempts=attempts,
        requirement_failed=phase == "durable_turn_completed",
    ) == (classification, failure_code)


@pytest.mark.parametrize("report", (None, "not-json", "[]", "{}"))
def test_live_row_unknown_evidence_is_inconclusive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report: str | None,
) -> None:
    manifest = load_provider_session_resilience_manifest(
        _write_manifest(tmp_path, _manifest(tmp_path)), root=ROOT
    )

    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if report is not None:
            Path(command[command.index("--summary-output") + 1]).write_text(
                report, encoding="utf-8"
            )
        return subprocess.CompletedProcess(command, 0, stdout="done\n", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    row = runner._live_row(
        target=manifest.targets[0],
        run_id=manifest.run_id,
        messages=manifest.messages,
        required_output_marker=manifest.required_output_marker,
    )
    assert row["classification"] == "inconclusive"
    assert row["probe_exit_code"] == 0
    assert row["failure_code"] == (
        "probe_report_missing"
        if report is None
        else "provider_attempts_missing"
        if report == "{}"
        else "probe_report_malformed"
    )


def test_outer_timeout_is_inconclusive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = load_provider_session_resilience_manifest(
        _write_manifest(tmp_path, _manifest(tmp_path)), root=ROOT
    )

    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 60, output="secret", stderr="secret")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    row = runner._live_row(
        target=manifest.targets[0],
        run_id=manifest.run_id,
        messages=manifest.messages,
        required_output_marker=manifest.required_output_marker,
    )
    assert row["classification"] == "inconclusive"
    assert row["failure_code"] == "outer_timeout"
    assert row["probe_exit_code"] is None
    assert "secret" not in json.dumps(row)


@pytest.mark.parametrize(
    "classification",
    (
        "inconclusive",
        "blocked_external",
        "provider_residual",
        "runtime_regression",
        "not_applicable",
    ),
)
def test_every_nonpass_row_fails_certification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    classification: str,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv("OPENMINION_GENERATED_ROOT", raising=False)
    manifest_path = _write_manifest(tmp_path, _manifest(tmp_path))
    monkeypatch.setattr(
        runner, "_live_row", lambda **_kwargs: {"classification": classification}
    )
    monkeypatch.setattr(
        runner,
        "write_provider_session_resilience_report",
        lambda *_args, **_kwargs: (tmp_path / "report.json", tmp_path / "report.md"),
    )
    assert runner.main(["--manifest", str(manifest_path)]) == 1


def test_certification_v2_preserves_other_schema_versions_and_historical_rendering() -> (
    None
):
    assert (
        CERTIFICATION_REPORT_SCHEMA_VERSION
        == "provider-session-resilience-certification.v2"
    )
    assert CERTIFICATION_RUN_SCHEMA_VERSION == "provider-session-resilience-run.v1"
    assert SCHEMA_VERSION == "session-context-provider-matrix.v1"
    historical = {
        "schema_version": "provider-session-resilience-certification.v1",
        "run_id": "historical",
        "rows": [
            {
                "provider_class": "provider",
                "agent_id": "agent",
                "model": "model",
                "classification": "runtime_regression",
                "failure_code": "startup_timeout",
            }
        ],
    }
    rendered = _render_certification_markdown(historical)
    assert "runtime_regression" in rendered
    assert "startup_timeout" in rendered


def test_live_row_retains_redacted_attempt_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = load_provider_session_resilience_manifest(
        _write_manifest(tmp_path, _manifest(tmp_path)), root=ROOT
    )
    target = manifest.targets[0]
    attempts = _completed_attempts(target)
    attempts[-1].update(
        raw_trace="never-persist-this",
        api_key="never-persist-this",
        trace_key="Bearer never-persist-this",
        usage={"input_tokens": 2, "raw_trace": "never-persist-this"},
    )

    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        Path(command[command.index("--summary-output") + 1]).write_text(
            json.dumps({"provider_attempts": attempts}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, stdout="done\n", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    row = runner._live_row(
        target=target,
        run_id=manifest.run_id,
        messages=manifest.messages,
        required_output_marker=manifest.required_output_marker,
    )
    assert row["classification"] == "pass"
    assert len(row["provider_attempts"]) == 2
    assert row["provider_attempts"][-1]["usage"] == {"input_tokens": 2}
    assert "never-persist-this" not in json.dumps(row)
    assert "raw_trace" not in json.dumps(row)
