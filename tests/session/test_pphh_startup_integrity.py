from __future__ import annotations

import pytest

from openminion.modules.session.storage import store as session_store_module
from openminion.modules.session.storage.store import SQLiteSessionStore


def test_sqlite_session_store_runs_startup_integrity_check(
    monkeypatch, tmp_path
) -> None:
    calls: list[str] = []

    def _verify(path):
        calls.append(str(path))
        return {"ok": True}

    monkeypatch.setattr(session_store_module, "verify_session_store_integrity", _verify)

    store = SQLiteSessionStore(tmp_path / "sessions.db")
    try:
        assert calls
    finally:
        store.close()


def test_sqlite_session_store_rejects_failed_startup_integrity(
    monkeypatch,
    tmp_path,
) -> None:
    def _verify(path):
        del path
        return {"ok": False, "findings": [{"code": "quick_check_failed"}]}

    monkeypatch.setattr(session_store_module, "verify_session_store_integrity", _verify)

    with pytest.raises(RuntimeError, match="integrity violation"):
        SQLiteSessionStore(tmp_path / "sessions.db")
