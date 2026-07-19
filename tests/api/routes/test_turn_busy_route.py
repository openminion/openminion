from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus

from openminion.api.routes.contracts import APIRouteContext
from openminion.api.routes import turns


@dataclass
class _Submission:
    session_id: str = "session-busy"
    run_id: str = "run-busy"


class _BusyError(RuntimeError):
    code = "SESSION_TURN_BUSY"
    retry_after_s = 7


def _ctx() -> APIRouteContext:
    return APIRouteContext(
        config_path=None,
        runtime=None,
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="req-busy",
    )


def test_v1_turn_maps_session_turn_busy_to_retryable_conflict(monkeypatch) -> None:
    monkeypatch.setattr(
        turns,
        "open_turn_submission",
        lambda **_kwargs: _Submission(),
    )

    def _raise_busy(*_args, **_kwargs):
        raise _BusyError("session turn is busy")

    monkeypatch.setattr(turns, "collect_sync_turn_payload", _raise_busy)
    closed: list[object] = []
    monkeypatch.setattr(turns, "close_submission", lambda submission: closed.append(submission))

    result = turns.handle_request(
        _ctx(),
        method_name="POST",
        path="/v1/turn",
        body={"message": "hello"},
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.CONFLICT
    assert result.payload["error"]["code"] == "SESSION_TURN_BUSY"
    assert result.payload["error"]["retryable"] is True
    assert result.payload["error"]["retry_after_ms"] == 7000
    assert result.payload["error"]["details"]["retry_after_s"] == 7
    assert closed == [_Submission()]
