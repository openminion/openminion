from __future__ import annotations

import subprocess

from openminion.modules.task.project import run_project_verification_commands
from openminion.modules.task.constants import (
    PROJECT_VERIFICATION_OUTPUT_SUMMARY_MAX_CHARS,
)


def test_project_verification_uses_configured_command_timeout(
    tmp_path,
    monkeypatch,
) -> None:
    observed_timeouts: list[int] = []

    def run(argv, **kwargs):  # noqa: ANN001, ANN003
        observed_timeouts.append(int(kwargs["timeout"]))
        return subprocess.CompletedProcess(argv, 0, stdout="verification passed")

    monkeypatch.setattr(subprocess, "run", run)

    evidence = run_project_verification_commands(
        ("verify",),
        workspace=tmp_path,
        timeout_seconds=900,
    )

    assert observed_timeouts == [900]
    assert evidence[0].status.value == "passed"


def test_project_verification_preserves_bounded_redacted_output(
    tmp_path,
    monkeypatch,
) -> None:
    output = "progress\n" + ("x" * 4_100) + "\napi_key=secret-value\nE expected 2"

    def run(argv, **kwargs):  # noqa: ANN001, ANN003
        return subprocess.CompletedProcess(argv, 1, stdout=output)

    monkeypatch.setattr(subprocess, "run", run)

    evidence = run_project_verification_commands(("verify",), workspace=tmp_path)

    assert evidence[0].status.value == "failed"
    assert "E expected 2" in evidence[0].summary
    assert "secret-value" not in evidence[0].summary
    assert len(evidence[0].summary) <= PROJECT_VERIFICATION_OUTPUT_SUMMARY_MAX_CHARS
