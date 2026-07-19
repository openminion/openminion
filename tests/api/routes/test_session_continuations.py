from __future__ import annotations

from http import HTTPStatus
from types import SimpleNamespace

import pytest

from openminion.api.routes.contracts import APIRouteContext
from openminion.api.routes.sessions import handle_request
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


def _ctx(store: SQLiteSessionStore, host: str) -> APIRouteContext:
    runtime = SimpleNamespace(
        config=SimpleNamespace(gateway=SimpleNamespace(host=host)),
        session_continuation_store=store,
    )
    return APIRouteContext(None, runtime, None, None, "request")


def _source(store: SQLiteSessionStore) -> None:
    store.create_session(session_id="source", initial_agent_id="agent-a")
    store.put_working_state(
        "source",
        state_inline={"session_work_summary": "Finish the bounded continuation."},
    )


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
