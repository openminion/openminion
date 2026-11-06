"""POST /turns answers a busy session like POST /v1/turn: retryable 409."""

from __future__ import annotations

from http import HTTPStatus

from openminion.api.routes import turns
from openminion.api.routes.contracts import APIRouteContext
from openminion.modules.storage.runtime.session_store import RuntimeSessionTurnBusyError


def _ctx() -> APIRouteContext:
    return APIRouteContext(
        config_path=None,
        runtime=None,
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="req-legacy-busy",
    )


def test_legacy_turn_maps_busy_session_to_retryable_conflict(monkeypatch) -> None:
    def _raise_busy(**_kwargs):
        raise RuntimeSessionTurnBusyError(
            "session-busy",
            retry_after_s=7,
            active_owner="api-user",
            expires_at="2026-11-06T12:00:00+00:00",
        )

    monkeypatch.setattr(turns, "run_turn", _raise_busy)

    result = turns.handle_request(
        _ctx(),
        method_name="POST",
        path="/turns",
        body={"message": "hello"},
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.CONFLICT
    assert result.payload["error"]["code"] == "SESSION_TURN_BUSY"
    assert result.payload["error"]["retryable"] is True
    assert result.payload["error"]["retry_after_ms"] == 7000
    details = result.payload["error"]["details"]
    assert details["retry_after_s"] == 7
    assert details["active_owner"] == "api-user"
    assert details["expires_at"].startswith("2026-11-06")
