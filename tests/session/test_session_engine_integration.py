from __future__ import annotations

from pathlib import Path

from openminion.modules.session.runtime.factory import build_module_session_store
from openminion.modules.storage.engine import StorageEngineConfig


def test_session_store_constructed_via_storage_engine_runs_full_lifecycle(
    tmp_path: Path,
) -> None:
    store = build_module_session_store(
        config=StorageEngineConfig(
            root_dir=tmp_path / "storage",
            sqlite_path=tmp_path / "sessions.db",
            fallback_root=tmp_path,
            record_backend="record.sqlite",
        ),
        database_path=tmp_path / "sessions.db",
    )
    try:
        session_id = store.create_session(
            initial_agent_id="agent-a",
            profile_version="profile-v1",
            title="Engine lifecycle",
            tags=["smoke"],
        )

        loaded = store.get_session(session_id)
        assert loaded is not None
        assert loaded["title"] == "Engine lifecycle"
        assert loaded["active_agent_id"] == "agent-a"

        store.bind_agent(
            session_id,
            "agent-b",
            "profile-v2",
            reason="handoff",
        )
        store.append_turn(session_id, "user", "hello from engine")
        store.put_working_state(session_id, state_inline={"mode": "respond"})
        store.archive_session(session_id)

        archived = store.get_session(session_id)
        turns = store.list_turns(session_id)
        events = store.list_events(session_id)

        assert archived is not None
        assert archived["status"] == "archived"
        assert archived["active_agent_id"] == "agent-b"
        assert turns[-1]["content"] == "hello from engine"
        assert any(event["type"] == "agent.bound" for event in events)
    finally:
        store.close()
