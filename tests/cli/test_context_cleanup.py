from pathlib import Path

from openminion.cli.commands.context_cleanup import SessionCleanupUtility


def test_error_marker_detection_is_narrow() -> None:
    utility = SessionCleanupUtility(Path("runtime.db"))

    assert utility._is_error_text("State machine error: invalid transition") is True
    assert utility._is_error_text("ordinary assistant response") is False
    assert utility._is_error_text("") is False


def test_missing_store_returns_explicit_error(monkeypatch) -> None:
    utility = SessionCleanupUtility(Path("runtime.db"))
    monkeypatch.setattr(utility, "_get_store", lambda: None)

    assert utility.scan_session("missing") == {"error": "Store not available"}
