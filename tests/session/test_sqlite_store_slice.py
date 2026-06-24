from __future__ import annotations

from pathlib import Path
from threading import RLock

import pytest

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.session.storage.slices import SliceStore


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteSessionStore:
    db_path = tmp_path / "sess-slice.db"
    session_store = SQLiteSessionStore(db_path)
    yield session_store
    session_store.close()


def test_slice_cache_invalidation(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    first = store.get_slice(session_id, purpose="act", limits={"max_turns": 4})
    second = store.get_slice(session_id, purpose="act", limits={"max_turns": 4})
    assert first["slice_version"] == second["slice_version"]

    store.append_turn(session_id, role="user", content="hi")
    third = store.get_slice(session_id, purpose="act", limits={"max_turns": 4})
    assert third["slice_version"] != first["slice_version"]


def test_slice_v15_includes_context_fields(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    prompt_ctx = store.create_prompt_context(session_id)
    checkpoint_id = store.save_compression_checkpoint(session_id, bundle_json="{}")
    seed_id = store.save_seed_bundle(
        session_id, source_bundle_id="bundle", sections_json="[]", total_tokens=1
    )

    store.append_event(
        session_id,
        event_type="session.compaction.archive",
        payload={"relative_path": "archive/2026-03-11.jsonl"},
    )

    slice_v15 = store.get_slice(session_id, purpose="act", limits={"max_turns": 2})
    assert slice_v15["prompt_context_id"] == prompt_ctx
    assert slice_v15["checkpoint_id"] == checkpoint_id
    assert slice_v15["seed_bundle_id"] == seed_id
    assert slice_v15["archive_refs"]


def test_non_decide_slice_carries_first_user_task_anchor(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )
    store.append_turn(session_id, role="user", content="Build the scratch project.")
    store.append_turn(session_id, role="assistant", content="I will start.")
    store.append_turn(session_id, role="tool", content="file.write pyproject")
    store.append_turn(session_id, role="assistant", content="Need confirmation.")
    store.append_turn(session_id, role="user", content="yes")
    store.append_turn(session_id, role="tool", content="file.list_dir result")

    slice_v15 = store.get_slice(session_id, purpose="act", limits={"max_turns": 2})

    recent = slice_v15["recent_turns"]
    assert [turn["role"] for turn in recent] == ["user", "user", "tool"]
    assert recent[0]["text"] == "Build the scratch project."
    assert recent[-1]["text"] == "file.list_dir result"


def test_decide_slice_preserves_tail_only_without_task_anchor(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )
    store.append_turn(session_id, role="user", content="Build the scratch project.")
    store.append_turn(session_id, role="assistant", content="I will start.")
    store.append_turn(session_id, role="tool", content="file.write pyproject")
    store.append_turn(session_id, role="assistant", content="Need confirmation.")
    store.append_turn(session_id, role="user", content="yes")
    store.append_turn(session_id, role="tool", content="file.list_dir result")

    slice_v15 = store.get_slice(session_id, purpose="decide", limits={"max_turns": 2})

    recent = slice_v15["recent_turns"]
    assert [turn["text"] for turn in recent] == ["yes", "file.list_dir result"]


def test_non_decide_slice_does_not_duplicate_anchor_already_in_tail(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )
    store.append_turn(session_id, role="user", content="Build the scratch project.")
    store.append_turn(session_id, role="assistant", content="I will start.")

    slice_v15 = store.get_slice(session_id, purpose="act", limits={"max_turns": 8})

    assert [turn["text"] for turn in slice_v15["recent_turns"]] == [
        "Build the scratch project.",
        "I will start.",
    ]


def test_slice_v15_includes_total_turn_count_and_conversation_summary(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    store.append_turn(
        session_id,
        role="user",
        content="Please plan a two week Japan trip with hotels and budget.",
    )
    store.append_event(
        session_id,
        event_type="tool.completed",
        payload={"tool_name": "web.search", "summary": "Japan travel search"},
    )
    store.append_turn(
        session_id,
        role="assistant",
        content=(
            "I created a detailed Japan plan with Tokyo, Kyoto, Hiroshima, "
            "and Osaka hotel areas."
        ),
    )
    store.append_event(
        session_id,
        event_type="turn.outcome",
        payload={"mode_name": "act"},
    )

    slice_v15 = store.get_slice(
        session_id,
        purpose="decide",
        limits={"max_turns": 2},
    )

    assert slice_v15["total_turn_count"] == 2
    assert "conversation_summary" in slice_v15
    assert "turn_index=1" in slice_v15["conversation_summary"]
    assert "user_preview=" in slice_v15["conversation_summary"]
    assert 'route_type="act"' in slice_v15["conversation_summary"]
    assert "assistant_response_tokens=" in slice_v15["conversation_summary"]
    assert 'tool_families_used=["web"]' in slice_v15["conversation_summary"]
    assert "assistant_tail_preview=" in slice_v15["conversation_summary"]


def test_slice_v15_reconstructs_active_task_plan(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    store.append_event(
        session_id,
        event_type="task_plan.declared",
        payload={
            "plan": {
                "plan_id": "plan-1",
                "objective": "Build APD",
                "steps": [
                    {
                        "step_id": "inspect",
                        "description": "Inspect seams",
                        "tool_families": ["file"],
                    },
                    {
                        "step_id": "patch",
                        "description": "Patch code",
                        "depends_on": ["inspect"],
                        "tool_families": ["code"],
                    },
                ],
            }
        },
    )
    store.append_event(
        session_id,
        event_type="task_plan.step_completed",
        payload={
            "plan_id": "plan-1",
            "step_id": "inspect",
            "output_summary": "Found the context and session seams.",
        },
    )

    slice_v15 = store.get_slice(
        session_id,
        purpose="decide",
        limits={"max_turns": 2},
    )

    active_plan = slice_v15["active_task_plan"]
    assert active_plan["plan_id"] == "plan-1"
    assert active_plan["steps"][0]["status"] == "completed"
    assert active_plan["steps"][0]["output_summary"] == (
        "Found the context and session seams."
    )
    assert active_plan["steps"][1]["depends_on"] == ["inspect"]

    store.append_event(
        session_id,
        event_type="task_plan.revised",
        payload={
            "reason": "scope changed",
            "plan": {
                "plan_id": "plan-1",
                "objective": "Build APD",
                "steps": [
                    {
                        "step_id": "ship",
                        "description": "Ship the feature",
                        "tool_families": ["code"],
                    }
                ],
            },
        },
    )
    after_revision = store.get_slice(
        session_id,
        purpose="decide",
        limits={"max_turns": 2},
    )
    assert after_revision["active_task_plan"]["steps"][0]["step_id"] == "ship"

    store.append_event(
        session_id,
        event_type="task_plan.completed",
        payload={"plan_id": "plan-1", "reason": "done"},
    )
    after_completion = store.get_slice(
        session_id,
        purpose="decide",
        limits={"max_turns": 2},
    )
    assert after_completion["active_task_plan"] is None


def test_recent_tool_events_are_distilled_for_slice_consumers(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    store.append_event(
        session_id,
        event_type="tool.completed",
        payload={
            "tool_name": "web.search",
            "summary": "Web search for iran news returned Reuters and BBC coverage.",
        },
        refs={"artifact_refs": ["artifact:search-1"]},
    )

    recent_tool_events = store.get_recent_tool_events(session_id, 1)
    assert recent_tool_events[0]["tool_name"] == "web.search"
    assert (
        recent_tool_events[0]["excerpt"]
        == "Web search for iran news returned Reuters and BBC coverage."
    )
    assert recent_tool_events[0]["artifact_refs"] == ["artifact:search-1"]

    slice_v15 = store.get_slice(
        session_id,
        purpose="act",
        limits={"max_turns": 2, "max_tool_events": 1},
    )
    assert slice_v15["recent_tool_events"][0]["tool_name"] == "web.search"
    assert (
        slice_v15["recent_tool_events"][0]["excerpt"]
        == "Web search for iran news returned Reuters and BBC coverage."
    )


def test_slice_store_rejects_incomplete_source() -> None:
    with pytest.raises(TypeError, match="missing required callables"):
        SliceStore(
            object(),
            lock=RLock(),
            slice_cache={},
            normalize_limits=lambda limits: {"limits": limits},
            stable_hash=lambda _: "hash",
        )
