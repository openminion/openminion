from __future__ import annotations

from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.cli.chat.commands.message import _handle_skill_command
from openminion.cli.chat.commands.session import (
    _open_brain_session_store,
    _read_session_skill_state,
)


def _config(tmp_path: Path) -> OpenMinionConfig:
    return OpenMinionConfig.from_dict(
        {
            "storage": {"path": str(tmp_path / "openminion.db")},
            "agents": {
                "router-agent": {
                    "name": "router-agent",
                    "provider": "echo",
                    "skill": ["alpha"],
                    "skill_catalog": ["alpha", "beta"],
                },
            },
            "default_agent": "router-agent",
        }
    )


def _catalog() -> list[dict[str, str]]:
    return [
        {
            "id": "alpha",
            "name": "Alpha",
            "one_liner": "alpha helper",
            "version_hash": "a" * 64,
        },
        {
            "id": "beta",
            "name": "Beta",
            "one_liner": "beta helper",
            "version_hash": "b" * 64,
        },
    ]


def test_skill_command_load_unload_auto_and_clear(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        "openminion.cli.chat.commands.message._allowed_skill_catalog",
        lambda *, config, agent_id: _catalog(),
    )

    _handle_skill_command(
        line="/skill load beta",
        config=config,
        agent_id="router-agent",
        session_id="session-1",
    )
    store = _open_brain_session_store(config)
    try:
        loaded, unloaded, mode = _read_session_skill_state(store, "session-1")
        assert loaded == ["beta"]
        assert unloaded == []
        assert mode is None
    finally:
        store.close()

    _handle_skill_command(
        line="/skill unload alpha",
        config=config,
        agent_id="router-agent",
        session_id="session-1",
    )
    store = _open_brain_session_store(config)
    try:
        loaded, unloaded, mode = _read_session_skill_state(store, "session-1")
        assert loaded == ["beta"]
        assert unloaded == ["alpha"]
        assert mode is None
    finally:
        store.close()

    _handle_skill_command(
        line="/skill auto",
        config=config,
        agent_id="router-agent",
        session_id="session-1",
    )
    store = _open_brain_session_store(config)
    try:
        loaded, unloaded, mode = _read_session_skill_state(store, "session-1")
        assert loaded == ["beta"]
        assert unloaded == ["alpha"]
        assert mode == "auto"
    finally:
        store.close()

    _handle_skill_command(
        line="/skill clear",
        config=config,
        agent_id="router-agent",
        session_id="session-1",
    )
    store = _open_brain_session_store(config)
    try:
        loaded, unloaded, mode = _read_session_skill_state(store, "session-1")
        assert loaded == []
        assert unloaded == []
        assert mode is None
    finally:
        store.close()

    rendered = capsys.readouterr().out
    assert "loaded skill beta" in rendered
    assert "unloaded skill alpha" in rendered
    assert "session skill mode set to auto" in rendered
    assert "cleared session skill overrides" in rendered


def test_skill_list_shows_selection_mode_capacity_and_sources(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        "openminion.cli.chat.commands.message._allowed_skill_catalog",
        lambda *, config, agent_id: _catalog(),
    )

    _handle_skill_command(
        line="/skill load beta",
        config=config,
        agent_id="router-agent",
        session_id="session-2",
    )
    capsys.readouterr()

    _handle_skill_command(
        line="/skill list",
        config=config,
        agent_id="router-agent",
        session_id="session-2",
    )

    rendered = capsys.readouterr().out
    assert "selection_mode=" in rendered
    assert "effective=2" in rendered
    assert "capacity=" in rendered
    assert "source=config" in rendered
    assert "source=session" in rendered


def test_skill_load_rejects_when_session_skill_capacity_is_reached(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        "openminion.cli.chat.commands.message._allowed_skill_catalog",
        lambda *, config, agent_id: _catalog(),
    )
    monkeypatch.setattr(
        "openminion.modules.brain.bootstrap.skill.pipeline._direct_capacity",
        lambda catalog: 1,
    )

    _handle_skill_command(
        line="/skill load beta",
        config=config,
        agent_id="router-agent",
        session_id="session-capacity",
    )

    store = _open_brain_session_store(config)
    try:
        loaded, unloaded, mode = _read_session_skill_state(store, "session-capacity")
        assert loaded == []
        assert unloaded == []
        assert mode is None
    finally:
        store.close()

    rendered = capsys.readouterr().out
    assert "skill capacity reached" in rendered


def test_open_brain_session_store_uses_data_root_for_relative_storage_path(
    monkeypatch, tmp_path: Path
) -> None:
    config = OpenMinionConfig.from_dict(
        {
            "storage": {"path": "state/openminion.db"},
            "agents": {
                "router-agent": {
                    "name": "router-agent",
                    "provider": "echo",
                },
            },
            "default_agent": "router-agent",
        }
    )
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))

    store = _open_brain_session_store(config)
    try:
        store.create_session(
            session_id="session-relative-root",
            initial_agent_id="router-agent",
        )
    finally:
        store.close()

    assert (tmp_path / ".openminion" / "state" / "brain" / "sessions.db").exists()
    assert not (tmp_path / "state" / "brain" / "sessions.db").exists()
