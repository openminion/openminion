from __future__ import annotations

from http import HTTPStatus
from types import SimpleNamespace

import pytest

from openminion.api.routes.contracts import APIRouteContext
from openminion.api.routes.sessions import handle_request
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database


def _ctx(
    store: SQLiteSessionStore,
    host: str,
    *,
    sessions: SessionStore | None = None,
) -> APIRouteContext:
    runtime = SimpleNamespace(
        config=SimpleNamespace(gateway=SimpleNamespace(host=host)),
        session_continuation_store=store,
        sessions=sessions,
    )
    return APIRouteContext(None, runtime, None, None, "request")


def _source(store: SQLiteSessionStore) -> None:
    store.create_session(session_id="source", initial_agent_id="agent-a")
    store.put_working_state(
        "source",
        state_inline={"session_work_summary": "Finish the bounded continuation."},
    )


def _room_sessions(tmp_path) -> tuple[SessionStore, object]:
    database_path = tmp_path / "runtime" / "openminion.db"
    migrate_database(database_path)
    connection = connect_database(database_path)
    sessions = SessionStore(connection)
    sessions.create_room(
        channel="cli",
        target="team",
        session_id="room-source",
        metadata={"local_human_id": "human-a"},
    )
    sessions.add_participant(
        session_id="room-source",
        participant_type="human",
        participant_id="human-a",
        role="owner",
    )
    for agent_id in ("agent-a", "agent-b"):
        sessions.add_participant(
            session_id="room-source",
            participant_type="agent",
            participant_id=agent_id,
        )
    sessions.set_active_agent(session_id="room-source", agent_id="agent-a")
    return sessions, connection


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_local_route_previews_creates_and_applies(tmp_path, host: str) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    _source(store)
    store.create_session(session_id="target", initial_agent_id="agent-a")
    ctx = _ctx(store, host)

    preview = handle_request(
        ctx,
        method_name="POST",
        path="/v1/sessions/source/continuations",
        body={"dry_run": True, "target_agent_id": "agent-a"},
        query=None,
    )
    assert preview is not None
    assert preview.status == HTTPStatus.OK
    assert preview.payload["continuation"]["status"] == "previewed"
    assert (
        store.get_events("source", types=["session.continuation.packet_created"]) == []
    )

    created = handle_request(
        ctx,
        method_name="POST",
        path="/v1/sessions/source/continuations",
        body={"target_agent_id": "agent-a"},
        query=None,
    )
    assert created is not None
    packet_id = created.payload["continuation"]["packet"]["packet_id"]
    applied = handle_request(
        ctx,
        method_name="POST",
        path=f"/v1/sessions/target/continuations/{packet_id}/apply",
        body={},
        query=None,
    )
    assert applied is not None
    assert applied.status == HTTPStatus.OK
    assert applied.payload["status"] == "applied"


def test_external_route_fails_closed_without_header_identity(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    _source(store)
    ctx = _ctx(store, "0.0.0.0")
    ctx = APIRouteContext(
        ctx.config_path,
        ctx.runtime,
        ctx.runtime_bootstrap_error,
        {"Authorization": "Bearer ignored"},
        ctx.request_id,
    )

    result = handle_request(
        ctx,
        method_name="POST",
        path="/v1/sessions/source/continuations",
        body={"target_agent_id": "agent-a"},
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.FORBIDDEN
    assert result.payload["error"]["code"] == "external_api_continuation_disabled"
    assert (
        store.get_events("source", types=["session.continuation.packet_created"]) == []
    )


def test_room_handoff_route_rechecks_membership_before_apply(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    _seed_room_source(store)
    store.create_session(session_id="worker", initial_agent_id="agent-b")
    sessions, connection = _room_sessions(tmp_path)
    ctx = _ctx(store, "127.0.0.1", sessions=sessions)
    try:
        preview = handle_request(
            ctx,
            method_name="POST",
            path="/v1/rooms/room-source/handoffs",
            body={
                "dry_run": True,
                "target_agent_id": "agent-b",
                "target_session_id": "worker",
                "task_step_id": "step-1",
            },
            query=None,
        )
        assert preview is not None
        assert preview.status == HTTPStatus.OK
        payload = preview.payload["continuation"]["preview"]["payload"]
        assert payload["continuation_kind"] == "room_agent_handoff"
        assert (
            store.get_events(
                "room-source", types=["session.continuation.packet_created"]
            )
            == []
        )

        created = handle_request(
            ctx,
            method_name="POST",
            path="/v1/rooms/room-source/handoffs",
            body={
                "target_agent_id": "agent-b",
                "target_session_id": "worker",
                "task_step_id": "step-1",
            },
            query=None,
        )
        assert created is not None
        packet_id = created.payload["continuation"]["packet"]["packet_id"]

        sessions.remove_participant(
            session_id="room-source",
            participant_type="agent",
            participant_id="agent-b",
        )
        applied = handle_request(
            ctx,
            method_name="POST",
            path=f"/v1/sessions/worker/continuations/{packet_id}/apply",
            body={},
            query=None,
        )
        assert applied is not None
        assert applied.status == HTTPStatus.CONFLICT
        assert applied.payload["reason_code"] == "continuation_room_binding_required"
        assert (
            store.get_events("worker", types=["session.continuation.packet_applied"])
            == []
        )
        rejected = store.get_events(
            "room-source", types=["session.continuation.rejected"]
        )
        assert len(rejected) == 1
        assert rejected[0]["payload"]["reason_code"] == (
            "continuation_room_binding_required"
        )
    finally:
        connection.close()


def test_room_handoff_route_applies_for_current_members(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    _seed_room_source(store)
    store.create_session(session_id="worker", initial_agent_id="agent-b")
    sessions, connection = _room_sessions(tmp_path)
    ctx = _ctx(store, "127.0.0.1", sessions=sessions)
    try:
        created = handle_request(
            ctx,
            method_name="POST",
            path="/v1/rooms/room-source/handoffs",
            body={
                "target_agent_id": "agent-b",
                "target_session_id": "worker",
                "task_step_id": "step-1",
            },
            query=None,
        )
        assert created is not None
        packet_id = created.payload["continuation"]["packet"]["packet_id"]

        applied = handle_request(
            ctx,
            method_name="POST",
            path=f"/v1/sessions/worker/continuations/{packet_id}/apply",
            body={},
            query=None,
        )
        assert applied is not None
        assert applied.status == HTTPStatus.OK
        assert applied.payload["status"] == "applied"
    finally:
        connection.close()


def _seed_room_source(store: SQLiteSessionStore) -> None:
    store.create_session(session_id="room-source", initial_agent_id="agent-a")
    store.put_working_state(
        "room-source",
        state_inline={"session_work_summary": "Hand off the bounded room work."},
    )
    store.append_event(
        "room-source",
        event_type="task_plan.declared",
        payload={
            "plan": {
                "plan_id": "plan-1",
                "objective": "Finish the bounded room work.",
                "steps": [
                    {
                        "step_id": "step-1",
                        "description": "Review the change.",
                        "status": "pending",
                    }
                ],
            }
        },
    )
