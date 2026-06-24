from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openminion.modules.tool.runtime.environment import (
    agent_id_from_context,
    identity_db_candidates,
    storage_path_from_context,
)


def _ctx_with_raw(raw: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(policy=SimpleNamespace(raw=raw))


def test_agent_id_from_context_prefers_context_metadata(monkeypatch) -> None:
    monkeypatch.setenv("OPENMINION_AGENT_ID", "env-agent")
    ctx = _ctx_with_raw({"context_metadata": {"agent_id": "meta-agent"}})
    assert agent_id_from_context(ctx) == "meta-agent"


def test_agent_id_from_context_falls_back_to_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENMINION_AGENT_ID", "env-agent")
    ctx = _ctx_with_raw({})
    assert agent_id_from_context(ctx) == "env-agent"


def test_identity_db_candidates_includes_env_and_default(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    data = tmp_path / "data"
    explicit = tmp_path / "identity-explicit.db"
    home.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("OPENMINION_HOME", str(home))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data))
    monkeypatch.setenv("OPENMINION_IDENTITY_DB", str(explicit))

    candidates = identity_db_candidates()
    assert candidates[0] == explicit.resolve(strict=False)
    assert (data / "identity" / "identity.db").resolve(strict=False) in candidates


def test_identity_db_candidates_dedupes(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    data = tmp_path / "data"
    home.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    canonical = (data / "identity" / "identity.db").resolve(strict=False)

    monkeypatch.setenv("OPENMINION_HOME", str(home))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data))
    monkeypatch.setenv("OPENMINION_IDENTITY_DB", str(canonical))

    candidates = identity_db_candidates()
    assert candidates == (canonical,)


def test_storage_path_from_context_prefers_context_metadata(monkeypatch) -> None:
    monkeypatch.setenv("OPENMINION_STORAGE_PATH", "/tmp/env-storage")
    ctx = _ctx_with_raw({"context_metadata": {"storage_path": "/tmp/meta-storage"}})
    assert storage_path_from_context(ctx) == "/tmp/meta-storage"


def test_storage_path_from_context_falls_back_to_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENMINION_STORAGE_PATH", "/tmp/env-storage")
    ctx = _ctx_with_raw({})
    assert storage_path_from_context(ctx) == "/tmp/env-storage"
