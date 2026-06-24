from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.modules.session.storage.store import SQLiteSessionStore
from openminion.modules.storage.runtime.sqlite import resolve_database_path
from openminion.modules.storage.runtime.vector_index import (
    InMemoryVectorIndex,
    LocalEmbeddingProvider,
    create_vector_index_adapter,
)


def test_resolve_database_path_uses_injected_env(tmp_path: Path) -> None:
    env = {
        "OPENMINION_HOME": str(tmp_path / "home"),
        "OPENMINION_DATA_ROOT": str(tmp_path / "data"),
    }

    resolved = resolve_database_path("state/custom.db", env=env)

    assert resolved == (tmp_path / "data" / "state" / "custom.db").resolve()


def test_sqlite_session_store_uses_injected_env_for_provider_selection() -> None:
    with pytest.raises(RuntimeError):
        SQLiteSessionStore(
            ":memory:",
            env={"OPENMINION_SESSION_STORAGE_PROVIDER": "remote"},
        )


def test_sqlite_session_store_uses_injected_env_for_blob_root(tmp_path: Path) -> None:
    data_root = (tmp_path / ".openminion").resolve()
    db_path = data_root / "session" / "sessions.db"
    store = SQLiteSessionStore(
        db_path,
        env={"OPENMINION_DATA_ROOT": str(data_root)},
    )
    try:
        blob_root = store._hybrid_store.blob_store.root_dir
        assert blob_root == (data_root / "storage").resolve()
    finally:
        store.close()


def test_local_embedding_provider_accepts_injected_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = SimpleNamespace(SentenceTransformer=lambda model: object())
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    provider = LocalEmbeddingProvider(
        env={"OPENMINION_ENABLE_SENTENCE_TRANSFORMERS": "1"}
    )

    assert provider._ensure_sentence_transformer() is True


def test_local_embedding_provider_auto_detects_sentence_transformers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = SimpleNamespace(SentenceTransformer=lambda model: object())
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    provider = LocalEmbeddingProvider(env={})

    assert provider._ensure_sentence_transformer() is True


def test_local_embedding_provider_explicit_disable_skips_sentence_transformers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = SimpleNamespace(SentenceTransformer=lambda model: object())
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    provider = LocalEmbeddingProvider(
        env={"OPENMINION_ENABLE_SENTENCE_TRANSFORMERS": "0"}
    )

    assert provider._ensure_sentence_transformer() is False


def test_create_vector_index_adapter_passes_env_to_sqlite_connect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    class _Conn:
        def close(self) -> None:
            return None

    def _fake_connect_database(db_path: str | Path, *, env=None):
        captured["db_path"] = str(db_path)
        captured["env"] = env
        return _Conn()

    monkeypatch.setattr(
        "openminion.modules.storage.runtime.vector_index.connect_database",
        _fake_connect_database,
    )
    monkeypatch.setattr(
        "openminion.modules.storage.runtime.migrations.run_migrations",
        lambda conn: None,
    )

    adapter = create_vector_index_adapter(
        db_path=tmp_path / "memory.db",
        embedding_provider=LocalEmbeddingProvider(),
        vector_index=InMemoryVectorIndex(dim=384),
        env={"OPENMINION_DATA_ROOT": str(tmp_path / ".openminion")},
    )

    assert adapter is not None
    assert str(captured["db_path"]).endswith("memory.db")
    assert isinstance(captured["env"], dict)
