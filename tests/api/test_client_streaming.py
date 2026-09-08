from __future__ import annotations

import io
import json
from dataclasses import dataclass
from http import HTTPStatus
from unittest import mock

from openminion.api.server import _OpenMinionAPIHandler
from openminion.api.server.client_auth import ClientAuthService
from openminion.api.server.client_streaming import (
    _desktop_chunk,
    handle_turn_stream_request,
)
from openminion.base.version import OPENMINION_VERSION


@dataclass
class _FakeChunk:
    idx: int


class _FakeHandle:
    def __init__(self, *, chunks: list[_FakeChunk], result: object) -> None:
        self._chunks = chunks
        self._result = result
        self.cancelled = False

    def stream(self, timeout_s: float):  # noqa: ANN001
        del timeout_s
        return iter(self._chunks)

    def result(self, timeout_s: float):  # noqa: ANN001
        del timeout_s
        return self._result

    def cancel(self) -> bool:
        self.cancelled = True
        return True


@dataclass
class _FakeRequest:
    session_id: str
    trace_id: str


@dataclass
class _FakeSubmission:
    handle: _FakeHandle
    request: _FakeRequest
    timeout_s: float
    session_id: str
    run_id: str


def _desktop_turn_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "trace_id": "11111111-1111-4111-8111-111111111111",
        "session_id": "desktop-session",
        "agent_id": "openminion",
        "input_text": "hello",
        "mode": "oneshot",
        "stream": True,
        "channel": "console",
        "user": "api-user",
        "idempotency_key": "22222222-2222-4222-8222-222222222222",
    }
    body.update(overrides)
    return body


def _submission(*, chunks: list[_FakeChunk] | None = None) -> _FakeSubmission:
    return _FakeSubmission(
        handle=_FakeHandle(chunks=chunks or [], result=object()),
        request=_FakeRequest(
            session_id="desktop-session",
            trace_id="11111111-1111-4111-8111-111111111111",
        ),
        timeout_s=1.0,
        session_id="desktop-session",
        run_id="11111111-1111-4111-8111-111111111111",
    )


def _install_json_body(
    handler: _OpenMinionAPIHandler, body: dict[str, object]
) -> None:
    encoded = json.dumps(body).encode("utf-8")
    handler.headers = {**dict(handler.headers), "Content-Length": str(len(encoded))}
    handler.rfile = io.BytesIO(encoded)


def test_desktop_chunk_projection_drops_raw_tool_values() -> None:
    projected = _desktop_chunk(
        {
            "trace_id": "trace-1",
            "kind": "tool_completed",
            "ts": "2026-08-20T00:00:00Z",
            "data": {
                "tool_name": "workspace.search",
                "call_id": "call-1",
                "state": "ok",
                "ok": True,
                "duration_ms": 12,
                "exit_code": 0,
                "args": {"path": "/private", "query": "secret"},
                "content": "sensitive result",
                "runtime_binding_id": "private-binding",
            },
        }
    )

    assert projected == {
        "trace_id": "trace-1",
        "kind": "tool_completed",
        "ts": "2026-08-20T00:00:00Z",
        "data": {
            "tool_name": "workspace.search",
            "call_id": "call-1",
            "state": "ok",
            "ok": True,
            "duration_ms": 12,
            "exit_code": 0,
        },
    }


def test_desktop_tool_completion_accepts_nullable_metrics() -> None:
    projected = _desktop_chunk(
        {
            "trace_id": "trace-1",
            "kind": "tool_completed",
            "ts": "2026-08-20T00:00:00Z",
            "data": {
                "tool_name": "workspace.search",
                "call_id": "call-1",
                "state": "ok",
                "ok": True,
            },
        }
    )

    assert projected is not None
    assert projected["data"]["duration_ms"] is None
    assert projected["data"]["exit_code"] is None


def test_desktop_stream_validates_body_and_echoes_identity() -> None:
    rejected: list[tuple[HTTPStatus, dict]] = []
    with mock.patch(
        "openminion.api.server.client_streaming.open_turn_submission"
    ) as open_submission:
        handle_turn_stream_request(
            body=_desktop_turn_body(extra=True),
            request_id="33333333-3333-4333-8333-333333333333",
            config_path=None,
            runtime=None,
            start_sse_response=lambda: None,
            write_sse_event=lambda **_kwargs: None,
            write_json=lambda status, payload: rejected.append((status, payload)),
            observe_request_metrics=lambda **_kwargs: 0,
            log_request_done=lambda **_kwargs: None,
            perf_counter=lambda: 0.0,
            desktop_client=True,
        )
    open_submission.assert_not_called()
    assert rejected[0][0] == HTTPStatus.BAD_REQUEST
    assert rejected[0][1]["error"]["code"] == "invalid_request"

    events: list[tuple[str, object]] = []
    with (
        mock.patch(
            "openminion.api.server.client_streaming.open_turn_submission",
            return_value=_submission(),
        ),
        mock.patch("openminion.api.server.client_streaming.close_submission"),
        mock.patch(
            "openminion.api.server.client_streaming.turn_response_to_dict",
            return_value={"final_text": "ok"},
        ),
    ):
        handle_turn_stream_request(
            body=_desktop_turn_body(),
            request_id="33333333-3333-4333-8333-333333333333",
            config_path=None,
            runtime=None,
            start_sse_response=lambda: None,
            write_sse_event=lambda *, event, data: events.append((event, data)),
            write_json=lambda *_args: None,
            observe_request_metrics=lambda **_kwargs: 0,
            log_request_done=lambda **_kwargs: None,
            perf_counter=lambda: 0.0,
            desktop_client=True,
        )

    assert events[0] == (
        "meta",
        {
            "request_id": "33333333-3333-4333-8333-333333333333",
            "trace_id": "11111111-1111-4111-8111-111111111111",
            "session_id": "desktop-session",
        },
    )


def test_desktop_stream_cancels_an_oversized_projected_event() -> None:
    events: list[tuple[str, object]] = []
    submission = _submission(chunks=[_FakeChunk(1)])
    with (
        mock.patch(
            "openminion.api.server.client_streaming.open_turn_submission",
            return_value=submission,
        ),
        mock.patch("openminion.api.server.client_streaming.close_submission"),
        mock.patch(
            "openminion.api.server.client_streaming.turn_chunk_to_dict",
            return_value={
                "trace_id": "trace-1",
                "kind": "final_text",
                "ts": "2026-08-20T00:00:00Z",
                "data": {"text": "x" * (256 * 1024)},
            },
        ),
    ):
        handle_turn_stream_request(
            body=_desktop_turn_body(),
            request_id="33333333-3333-4333-8333-333333333333",
            config_path=None,
            runtime=None,
            start_sse_response=lambda: None,
            write_sse_event=lambda *, event, data: events.append((event, data)),
            write_json=lambda *_args: None,
            observe_request_metrics=lambda **_kwargs: 0,
            log_request_done=lambda **_kwargs: None,
            perf_counter=lambda: 0.0,
            desktop_client=True,
        )

    assert submission.handle.cancelled
    assert [event for event, _ in events] == ["meta", "error", "done"]
    assert events[1][1]["code"] == "stream_limit_exceeded"


def test_authenticated_desktop_stream_uses_client_handler() -> None:
    service = ClientAuthService(
        master_token="master-token",
        config_path="config.json",
        home_root=".",
        data_root=".openminion",
        bind_host="127.0.0.1",
        daemon_version=OPENMINION_VERSION,
    )
    lease = service.mint(protocol_min=1, protocol_max=1, ttl_seconds=60)
    handler = object.__new__(_OpenMinionAPIHandler)
    handler.path = "/v1/turn/stream"
    handler.headers = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "X-OpenMinion-Client-Token": lease["client_token"],
        "X-Request-ID": "33333333-3333-4333-8333-333333333333",
    }
    handler.config_path = None
    handler.runtime = None
    handler.runtime_bootstrap_error = None
    handler.client_address = ("127.0.0.1", 1234)
    handler.client_auth = service
    _install_json_body(handler, _desktop_turn_body())
    handler._handle_client_turn_stream = mock.Mock()  # type: ignore[attr-defined]
    handler._write_json = mock.Mock()  # type: ignore[attr-defined]

    with mock.patch(
        "openminion.api.server.app.handle_http_turn_stream_request"
    ) as legacy_stream:
        _OpenMinionAPIHandler.do_POST(handler)

    handler._handle_client_turn_stream.assert_called_once_with(  # type: ignore[attr-defined]
        body=_desktop_turn_body(),
        request_id="33333333-3333-4333-8333-333333333333",
    )
    legacy_stream.assert_not_called()
