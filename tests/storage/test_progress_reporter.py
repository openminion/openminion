from __future__ import annotations

import pytest

from openminion.modules.storage.progress import (
    NullProgressReporter,
    ProgressReporter,
    TqdmProgressReporter,
    select_default_reporter,
)


def test_null_reporter_satisfies_protocol() -> None:
    reporter = NullProgressReporter()
    assert isinstance(reporter, ProgressReporter)
    reporter.on_start(total=10, label="test")
    reporter.on_progress(advance=1, message="step")
    reporter.on_end(success=True, message="done")


def test_tqdm_reporter_is_noop_when_stdout_not_tty(monkeypatch) -> None:
    # pytest's capsys patches stdout so isatty() returns False; we assert the
    # documented degraded behavior: no exception, no internal bar.
    reporter = TqdmProgressReporter()
    reporter.on_start(total=5, label="downloading")
    assert reporter._bar is None  # noqa: SLF001 — verifying documented behavior
    reporter.on_progress(advance=1, message="row 1")
    reporter.on_end(success=True)


def test_tqdm_reporter_force_starts_bar_when_tqdm_available() -> None:

    pytest.importorskip("tqdm")

    reporter = TqdmProgressReporter(force=True)
    assert isinstance(reporter, ProgressReporter)
    reporter.on_start(total=3, label="forced")
    assert reporter._bar is not None  # noqa: SLF001
    reporter.on_progress(advance=1, message="row")
    reporter.on_end(success=True)
    assert reporter._bar is None  # noqa: SLF001


def test_select_default_reporter_returns_null_when_inactive() -> None:

    chosen = select_default_reporter()
    assert isinstance(chosen, NullProgressReporter)


def test_tqdm_reporter_swallows_internal_failures(monkeypatch) -> None:
    reporter = TqdmProgressReporter(force=True)
    if not reporter._is_active():  # noqa: SLF001 — guard for environments lacking tqdm
        pytest.skip("tqdm not available")
    reporter.on_start(total=1, label="resilient")

    class _Broken:
        def set_postfix_str(self, *a, **kw):
            raise RuntimeError("boom")

        def update(self, *a, **kw):
            raise RuntimeError("boom")

        def close(self, *a, **kw):
            raise RuntimeError("boom")

    reporter._bar = _Broken()  # noqa: SLF001
    reporter.on_progress(advance=1, message="x")
    reporter.on_end(success=False, message="y")
