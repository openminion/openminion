from __future__ import annotations

from pathlib import Path

from openminion.modules.brain.runner import resume


def test_resume_facade_exports_expected_symbols() -> None:
    expected = {
        "DefaultCronJobLinker",
        "ExponentialBackoffResumePolicy",
        "next_attempt_state",
        "resolve_cron_resume_selection",
        "schedule_backoff_resume",
        "schedule_recurring_resume",
    }
    assert set(resume.__all__) == expected
    for symbol in expected:
        assert hasattr(resume, symbol)


def test_resume_facade_no_longer_imports_services_runtime_helpers() -> None:
    path = Path(resume.__file__).resolve()
    text = path.read_text(encoding="utf-8")
    assert "openminion.services.runtime.cron_resume" not in text
