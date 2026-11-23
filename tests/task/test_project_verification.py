from __future__ import annotations

import subprocess

from tests.tools.security.test_repair_preflight import (
    repair_admission as repair_admission,
)
from tests.tools.security.test_audit_report import audit_context as audit_context

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


def test_security_verifier_preserves_baseline_and_protected_inputs(repair_admission):
    import sys
    import pytest
    from openminion.tools.security.review import validate_security_repair
    from openminion.modules.task.project.verification import (
        run_security_repair_baseline,
        check_security_repair_files,
    )

    ctx, manager, candidate, selection, env = repair_admission
    context = validate_security_repair(
        selection,
        manager=manager,
        ctl=ctx.artifactctl,
        repository=candidate,
        agent_id="repairer",
        env=env,
    )
    evidence = run_security_repair_baseline(
        context,
        (f'{sys.executable} -c "raise SystemExit(1)"', "git status"),
        timeout_seconds=10,
    )
    assert [item.exit_code for item in evidence] == [1, 0]
    (candidate / "app.py").write_text("fixed\n")
    check_security_repair_files(context, candidate)
    (candidate / "rule.yml").write_text("changed verifier fixture\n")
    with pytest.raises(ValueError, match="protected input"):
        check_security_repair_files(context, candidate)


def test_security_verifier_rejects_new_unapproved_files(repair_admission):
    import pytest
    from openminion.tools.security.review import validate_security_repair
    from openminion.modules.task.project.verification import check_security_repair_files

    ctx, manager, candidate, selection, env = repair_admission
    context = validate_security_repair(
        selection,
        manager=manager,
        ctl=ctx.artifactctl,
        repository=candidate,
        agent_id="repairer",
        env=env,
    )
    (candidate / "other.py").write_text("unapproved\n")
    with pytest.raises(ValueError, match="outside approved scope"):
        check_security_repair_files(context, candidate)
