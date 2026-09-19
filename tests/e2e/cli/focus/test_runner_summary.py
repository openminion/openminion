from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from tests.e2e.runners import run_cli_focus_e2e

import pytest

pytestmark = pytest.mark.e2e


def test_focus_runner_writes_summary_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    summary_path = tmp_path / "focus-summary.json"

    def fake_run(paths, *, env, extra_args=(), timeout_seconds=None):
        assert paths == ("tests/e2e/cli/focus/test_local.py",)
        assert len(extra_args) == 1
        assert extra_args[0].startswith("--junitxml=")
        assert timeout_seconds is None
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        return 0

    monkeypatch.setenv(
        "OPENMINION_CLI_FOCUS_E2E_SUMMARY_OUTPUT",
        str(summary_path),
    )
    monkeypatch.setattr(run_cli_focus_e2e, "_run", fake_run)

    assert run_cli_focus_e2e.main(["local"]) == 0

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["mode"] == "local"
    assert summary["paths"] == ["tests/e2e/cli/focus/test_local.py"]
    assert summary["exit_code"] == 0
    assert summary["live"] is False
    assert summary["complex"] is False
    assert summary["elapsed_seconds"] >= 0


def test_live_minimax_runner_forwards_official_focus_profile(tmp_path: Path) -> None:
    home = tmp_path / "workspace"
    config_path = home / "test-configs" / "per-agent-minimax-official.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}\n", encoding="utf-8")
    capture_path = tmp_path / "captured.json"
    fake_python = tmp_path / "python3.11"
    fake_python.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "from pathlib import Path\n"
        "Path(os.environ['OPENMINION_TEST_CAPTURE']).write_text(json.dumps({\n"
        "    'config': os.environ.get('OPENMINION_CLI_FOCUS_E2E_CONFIG'),\n"
        "    'agent': os.environ.get('OPENMINION_CLI_FOCUS_E2E_AGENT'),\n"
        "}))\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    runner = (
        Path(__file__).resolve().parents[2]
        / "runners"
        / "run_live_minimax_regression_matrix.sh"
    )
    env = os.environ.copy()
    env.update(
        {
            "OPENMINION_HOME": str(home),
            "OPENMINION_PYTHON": str(fake_python),
            "OPENMINION_TEST_CAPTURE": str(capture_path),
        }
    )
    env.pop("OPENMINION_CLI_FOCUS_E2E_CONFIG", None)
    env.pop("OPENMINION_CLI_FOCUS_E2E_AGENT", None)

    result = subprocess.run(
        [str(runner), "gate"],
        cwd=runner.parents[3],
        env=env,
        check=False,
    )

    assert result.returncode == 0
    captured = json.loads(capture_path.read_text(encoding="utf-8"))
    assert captured == {
        "config": str(config_path),
        "agent": "minimax-m2-7",
    }


def _write_junit(path: Path, outcomes: tuple[str, ...] = ("pass", "pass")) -> None:
    from xml.etree.ElementTree import Element, SubElement, ElementTree

    root = Element("testsuites")
    suite = SubElement(root, "testsuite")
    for index, outcome in enumerate(outcomes):
        node = run_cli_focus_e2e.BASELINE_CASES[index % 2]
        source, name = node.split("::", 1)
        case = SubElement(
            suite, "testcase", classname=source[:-3].replace("/", "."), name=name
        )
        if outcome != "pass":
            SubElement(case, outcome)
    ElementTree(root).write(path, encoding="unicode")


@pytest.mark.parametrize(
    ("outcomes", "complete", "passed", "executed"),
    [
        (("pass", "pass"), True, 2, 2),
        (("pass",), False, 1, 1),
        (("pass", "pass", "pass"), False, 3, 3),
        (("skipped", "skipped"), True, 0, 0),
        (("failure", "pass"), True, 1, 2),
        (("error", "pass"), True, 1, 1),
    ],
)
def test_junit_case_accounting(
    tmp_path: Path, outcomes, complete, passed, executed
) -> None:
    path = tmp_path / "report.xml"
    _write_junit(path, outcomes)
    result = run_cli_focus_e2e._case_accounting(path, run_cli_focus_e2e.BASELINE_CASES)
    assert result["complete"] is complete
    assert result["passed"] == passed
    assert result["executed"] == executed
    assert result["collected"] == len(outcomes)


@pytest.mark.parametrize("report", [None, "<broken", "<testsuites />"])
def test_missing_malformed_and_empty_report_cannot_certify(
    tmp_path: Path, report
) -> None:
    path = tmp_path / "report.xml"
    if report is not None:
        path.write_text(report, encoding="utf-8")
    result = run_cli_focus_e2e._case_accounting(path, run_cli_focus_e2e.BASELINE_CASES)
    assert result["complete"] is False
    assert result["passed"] == 0
    assert result["missing_cases"] == list(run_cli_focus_e2e.BASELINE_CASES)


@pytest.mark.parametrize(
    ("outcomes", "code", "expected", "disposition"),
    [
        (("pass", "pass"), 0, 0, "pass"),
        (("pass",), 0, 1, "failed"),
        (("skipped", "skipped"), 0, 1, "failed"),
        (("pass", "pass", "pass"), 0, 1, "failed"),
        (("failure", "pass"), 1, 1, "failed"),
        (None, 0, 1, "inconclusive"),
        (None, 124, 124, "inconclusive"),
        (("pass", "pass"), 130, 130, "inconclusive"),
    ],
)
def test_baseline_summary_requires_executed_exact_cases(
    monkeypatch,
    tmp_path: Path,
    outcomes,
    code,
    expected,
    disposition,
) -> None:
    summary_path = tmp_path / "summary.json"
    monkeypatch.setenv(run_cli_focus_e2e._SUMMARY_ENV, str(summary_path))
    monkeypatch.setattr(run_cli_focus_e2e, "_live_preflight", lambda env: "")
    monkeypatch.setattr(
        run_cli_focus_e2e,
        "_config_identity",
        lambda env: {
            "status": "resolved",
            "model": "MiniMax-M2.7",
            "service_vendor": "minimax",
            "endpoint_authority": "api.minimax.io",
            "credentials_available": True,
        },
    )

    def fake_run(paths, *, env, extra_args=(), timeout_seconds=None):
        assert paths == run_cli_focus_e2e.BASELINE_CASES
        assert env[run_cli_focus_e2e._BASELINE_ENV] == "1"
        if outcomes is not None:
            _write_junit(Path(extra_args[-1].split("=", 1)[1]), outcomes)
        if code == 130:
            raise KeyboardInterrupt
        return code

    monkeypatch.setattr(run_cli_focus_e2e, "_run", fake_run)
    assert run_cli_focus_e2e.main(["baseline-live"]) == expected
    summary = json.loads(summary_path.read_text())
    assert summary["terminal_disposition"] == disposition
    assert summary["usage"] is None
    assert summary["case_accounting"]["expected"] == 2
    assert len(summary["candidate_commit"]) == 40
    assert len(summary["corpus_digest"]) == 64
    assert summary["environment"]["dependencies"]["pytest"]


def test_preflight_summary_marks_cases_not_run_and_discards_old_junit(
    monkeypatch, tmp_path: Path
) -> None:
    summary_path = tmp_path / "summary.json"
    junit = summary_path.with_suffix(".junit.xml")
    _write_junit(junit)
    monkeypatch.setenv(run_cli_focus_e2e._SUMMARY_ENV, str(summary_path))
    monkeypatch.delenv(run_cli_focus_e2e._CONFIG_ENV, raising=False)
    monkeypatch.setattr(
        run_cli_focus_e2e, "_run", lambda *a, **kw: pytest.fail("not run")
    )
    assert run_cli_focus_e2e.main(["baseline-live"]) == 2
    summary = json.loads(summary_path.read_text())
    assert summary["terminal_disposition"] == "blocked_external"
    assert summary["case_accounting"]["executed"] == 0
    assert summary["case_accounting"]["report_status"] == "absent"
    assert summary["evidence_paths"]["junit"] is None
    assert summary["preflight_error"]


def test_summary_redacts_secrets_before_writing(tmp_path: Path) -> None:
    path = tmp_path / "summary.json"
    run_cli_focus_e2e._write_run_summary(
        path=path,
        mode="baseline-live",
        suite=run_cli_focus_e2e.SUITES["baseline-live"],
        exit_code=2,
        elapsed_seconds=0,
        details={
            "preflight_error": "authorization Bearer private-secret-123456789",
            "api_key": "private-secret",
        },
    )
    assert "private-secret" not in path.read_text()


def test_config_identity_excludes_credentials_but_records_effective_model(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "config.json"
    config = {
        "agents": {"minimax-m2-7": {"provider": "openai", "model": "MiniMax-M2.7"}},
        "default_agent": "minimax-m2-7",
        "providers": {
            "openai": {
                "api_key": "private-secret-one",
                "model": "MiniMax-M2.7",
                "base_url": "https://api.minimax.io/v1",
                "provider_identity": {
                    "transport_adapter": "openai_chat",
                    "wire_protocol_family": "openai_compatible",
                    "service_vendor": "minimax",
                },
            }
        },
    }
    path.write_text(json.dumps(config))
    env = {run_cli_focus_e2e._CONFIG_ENV: str(path)}
    first = run_cli_focus_e2e._config_identity(env)
    config["providers"]["openai"]["api_key"] = "private-secret-two"
    path.write_text(json.dumps(config))
    second = run_cli_focus_e2e._config_identity(env)
    assert first["status"] == "resolved"
    assert first["model"] == "MiniMax-M2.7"
    assert first["service_vendor"] == "minimax"
    assert first["config_digest"] == second["config_digest"]
    assert "private-secret" not in json.dumps(first)


def test_safe_junit_excludes_all_private_assertion_content(tmp_path: Path) -> None:
    from xml.etree.ElementTree import ElementTree, Element, SubElement

    path = tmp_path / "summary.junit.xml"
    root = Element("testsuites", private="private-secret-literal")
    suite = SubElement(root, "testsuite")
    source, name = run_cli_focus_e2e.BASELINE_CASES[0].split("::", 1)
    case = SubElement(
        suite, "testcase", classname=source[:-3].replace("/", "."), name=name
    )
    failure = SubElement(case, "failure", message="private-secret-literal")
    failure.text = "private-secret-literal"
    SubElement(case, "system-out").text = "private-secret-literal"
    foreign = SubElement(
        suite,
        "testcase",
        classname="private-secret-literal",
        name="private-secret-literal",
    )
    SubElement(foreign, "skipped", message="private-secret-literal")
    ElementTree(root).write(path, encoding="unicode")
    safe = run_cli_focus_e2e._safe_junit(path)
    assert safe is not None
    assert safe.name == "summary.junit.safe.xml"
    assert "private-secret-literal" not in safe.read_text()
    accounting = run_cli_focus_e2e._case_accounting(
        safe, run_cli_focus_e2e.BASELINE_CASES
    )
    assert accounting["failed"] == 1
    assert accounting["skipped"] == 1


@pytest.mark.parametrize(
    ("identity", "reason"),
    [
        ({"status": "unavailable"}, "could not be resolved"),
        (
            {"status": "resolved", "model": "gpt", "service_vendor": "openai"},
            "official MiniMax",
        ),
        (
            {
                "status": "resolved",
                "model": "MiniMax-M2.7",
                "service_vendor": "minimax",
                "endpoint_authority": "api.minimax.io",
                "credentials_available": False,
            },
            "credential is unavailable",
        ),
    ],
)
def test_baseline_preflight_rejects_unresolved_echo_and_missing_credentials(
    monkeypatch,
    tmp_path: Path,
    identity,
    reason,
) -> None:
    summary = tmp_path / "summary.json"
    monkeypatch.setenv(run_cli_focus_e2e._SUMMARY_ENV, str(summary))
    monkeypatch.setattr(run_cli_focus_e2e, "_live_preflight", lambda env: "")
    monkeypatch.setattr(run_cli_focus_e2e, "_config_identity", lambda env: identity)
    monkeypatch.setattr(
        run_cli_focus_e2e, "_run", lambda *a, **kw: pytest.fail("must not run")
    )
    assert run_cli_focus_e2e.main(["baseline-live"]) == 2
    payload = json.loads(summary.read_text())
    assert reason in payload["preflight_error"]
    assert payload["terminal_disposition"] == "blocked_external"
    assert payload["case_accounting"]["executed"] == 0


def test_config_digest_records_model_change_and_ignores_isolated_storage(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    payload = {
        "agents": {"minimax-m2-7": {"provider": "openai", "model": "MiniMax-M2.7"}},
        "default_agent": "minimax-m2-7",
        "storage": {"path": str(tmp_path / "one.db")},
        "providers": {"openai": {"model": "MiniMax-M2.7"}},
    }
    path.write_text(json.dumps(payload))
    env = {run_cli_focus_e2e._CONFIG_ENV: str(path)}
    first = run_cli_focus_e2e._config_identity(env)
    payload["storage"]["path"] = str(tmp_path / "two.db")
    path.write_text(json.dumps(payload))
    assert (
        run_cli_focus_e2e._config_identity(env)["config_digest"]
        == first["config_digest"]
    )
    payload["providers"]["openai"]["model"] = "MiniMax-M2.5"
    path.write_text(json.dumps(payload))
    assert (
        run_cli_focus_e2e._config_identity(env)["config_digest"]
        != first["config_digest"]
    )


@pytest.mark.parametrize(
    "override",
    [
        {"action_policy": {"default_action": "allow"}},
        {"action_policy": {"allow_read_only_without_prompt": False}},
        {"agents": {"minimax-m2-7": {"system_prompt": "Different instructions"}}},
    ],
)
def test_config_digest_records_permissions_and_prompt(tmp_path: Path, override) -> None:
    path = tmp_path / "config.json"
    payload = {
        "agents": {"minimax-m2-7": {"provider": "openai", "model": "MiniMax-M2.7"}},
        "default_agent": "minimax-m2-7",
    }
    path.write_text(json.dumps(payload))
    env = {run_cli_focus_e2e._CONFIG_ENV: str(path)}
    first = run_cli_focus_e2e._config_identity(env)
    for key, value in override.items():
        if key == "agents":
            payload[key]["minimax-m2-7"].update(value["minimax-m2-7"])
        else:
            payload[key] = value
    path.write_text(json.dumps(payload))
    assert (
        run_cli_focus_e2e._config_identity(env)["config_digest"]
        != first["config_digest"]
    )


def test_invalid_capability_config_writes_nonpass_preflight_summary(
    monkeypatch, tmp_path: Path
) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"runtime": {"provider_policy": {"enabled": "invalid"}}})
    )
    summary = tmp_path / "summary.json"
    monkeypatch.setenv(run_cli_focus_e2e._CONFIG_ENV, str(config))
    monkeypatch.setenv(run_cli_focus_e2e._SUMMARY_ENV, str(summary))
    assert run_cli_focus_e2e.main(["baseline-live"]) == 2
    result = json.loads(summary.read_text())
    assert result["terminal_disposition"] == "blocked_external"
    assert result["case_accounting"]["executed"] == 0
    assert result["config_identity"]["status"] == "unavailable"
