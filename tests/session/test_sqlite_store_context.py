from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteSessionStore:
    db_path = tmp_path / "sess-context.db"
    session_store = SQLiteSessionStore(db_path)
    yield session_store
    session_store.close()


def test_prompt_context_lifecycle(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    ctx_id = store.create_prompt_context(session_id, prefix_hash="abc")
    active = store.get_active_prompt_context(session_id)
    assert active is not None
    assert active["prompt_context_id"] == ctx_id

    store.close_prompt_context(ctx_id, rollover_reason="rollover")
    assert store.get_active_prompt_context(session_id) is None


def test_checkpoint_and_seed_bundle_roundtrip(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    cp_id = store.save_compression_checkpoint(
        session_id, bundle_json="{}", reason="unit"
    )
    checkpoint = store.get_latest_checkpoint(session_id)
    assert checkpoint is not None
    assert checkpoint["checkpoint_id"] == cp_id

    seed_id = store.save_seed_bundle(
        session_id,
        source_bundle_id="bundle-1",
        sections_json="[]",
        total_tokens=10,
    )
    seed = store.get_latest_seed_bundle(session_id)
    assert seed is not None
    assert seed["seed_id"] == seed_id


def test_run_record_and_message_ref(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    run_id = store.create_run_record(session_id, run_type="llm", model_id="m1")
    store.finish_run_record(run_id, status="completed", input_tokens=1, output_tokens=2)

    ref_id = store.add_message_ref(
        session_id, role="assistant", run_id=run_id, content_inline="hi"
    )
    assert ref_id
