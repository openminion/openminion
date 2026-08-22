from __future__ import annotations

import subprocess

from openminion.modules.task.project import run_project_verification_commands


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
