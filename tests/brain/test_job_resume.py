from types import SimpleNamespace

from openminion.modules.brain.constants import (
    BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
    BRAIN_STATE_JOB_PENDING,
)
from openminion.modules.brain.runner.tick import job_resume


def test_pending_delegate_resume_persists_attachments_once(monkeypatch) -> None:
    appended: list[tuple[tuple[object, ...], dict[str, object]]] = []
    runner = SimpleNamespace(
        session_api=SimpleNamespace(
            append_turn=lambda *args, **kwargs: appended.append((args, kwargs))
        )
    )
    state = SimpleNamespace(
        status=BRAIN_STATE_JOB_PENDING,
        delegation_job_id="job-1",
        active_mode_name=BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
        trace_id=None,
        pending_jobs=[],
    )
    expected = SimpleNamespace(status="job_pending")
    monkeypatch.setattr(
        job_resume,
        "invoke_decision_direct",
        lambda *_args, **_kwargs: SimpleNamespace(to_step_output=lambda: expected),
    )
    refs = ["artifact://sha256/" + "b" * 64]

    result = job_resume.try_resume(
        runner=runner,
        state=state,
        user_input="continue delegated work",
        attachments=refs,
        trace_id="trace-resume",
        logger=SimpleNamespace(),
        session_id="session-resume",
    )

    assert result is expected
    assert len(appended) == 1
    assert appended[0][0] == (
        "session-resume",
        "user",
        "continue delegated work",
    )
    assert appended[0][1]["attachments"] == refs
