from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus

from openminion.api.routes.contracts import APIRouteContext
from openminion.api.routes import sessions
from openminion.modules.session.storage import SQLiteSessionStore


@dataclass
class _Runtime:
    sessions: SQLiteSessionStore


def _ctx(store: SQLiteSessionStore, headers: dict[str, str] | None = None) -> APIRouteContext:
    return APIRouteContext(
        config_path=None,
        runtime=_Runtime(store),
        runtime_bootstrap_error=None,
        request_headers=headers,
        request_id="req-share",
    )


def test_session_share_route_create_access_revoke_and_query_reject() -> None:
    store = SQLiteSessionStore(":memory:")
    sid = store.create_session(session_id="api-share")
    store.append_turn(sid, "assistant", "visible answer")

    created = sessions.handle_request(
        _ctx(store),
        method_name="POST",
        path=f"/sessions/{sid}/shares",
        body={"ttl_seconds": 120, "created_by": "alice"},
        query=None,
    )
    assert created is not None
    assert created.status == HTTPStatus.CREATED
    token = created.payload["share"]["token"]
    share_id = created.payload["share"]["share_id"]
    assert created.payload["meta"]["response_headers"]["Cache-Control"] == "no-store"

    denied = sessions.handle_request(
        _ctx(store, {"Authorization": f"Bearer {token}"}),
        method_name="GET",
        path=f"/session-shares/{share_id}",
        body=None,
        query=f"token={token}",
    )
    assert denied is not None
    assert denied.status == HTTPStatus.BAD_REQUEST
    assert denied.payload["error"]["code"] == "SESSION_SHARE_TOKEN_TRANSPORT_FORBIDDEN"

    accessed = sessions.handle_request(
        _ctx(store, {"Authorization": f"Bearer {token}"}),
        method_name="GET",
        path=f"/session-shares/{share_id}",
        body=None,
        query=None,
    )
    assert accessed is not None
    assert accessed.status == HTTPStatus.OK
    assert accessed.payload["projection"]["turns"][0]["text"] == "visible answer"

    revoked = sessions.handle_request(
        _ctx(store),
        method_name="DELETE",
        path=f"/session-shares/{share_id}",
        body=None,
        query=None,
    )
    assert revoked is not None
    assert revoked.status == HTTPStatus.OK

    gone = sessions.handle_request(
        _ctx(store, {"Authorization": f"Bearer {token}"}),
        method_name="GET",
        path=f"/session-shares/{share_id}",
        body=None,
        query=None,
    )
    assert gone is not None
    assert gone.status == HTTPStatus.GONE
