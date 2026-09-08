import unittest
from dataclasses import dataclass
from http import HTTPStatus
import io
import json
import tempfile
import threading
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlparse
from tests._csc_fixtures import _csc_install_default_agent


from openminion.api.server import (
    _OpenMinionAPIHandler,
    dispatch_request,
    get_api_metrics_snapshot,
    reset_api_metrics,
)
from openminion.api.runtime import APIRuntime
from openminion.base.config import OpenMinionConfig, save_config
from openminion.api.server.streaming import (
    TURN_STREAM_CAPABILITIES,
    handle_turn_stream_attach_request,
    handle_turn_stream_request,
    try_handle_turn_stream_attach,
)
from openminion.services.runtime.manager import (
    TurnChunk,
    TurnError,
    TurnResponse,
)
from openminion.modules.runtime.contracts import TURN_STREAM_SCHEMA_VERSION


def _install_json_body(handler: _OpenMinionAPIHandler, body: dict) -> None:
    encoded = json.dumps(body).encode("utf-8")
    handler.headers = {**dict(handler.headers), "Content-Length": str(len(encoded))}
    handler.rfile = io.BytesIO(encoded)


@dataclass
class _FakeChunk:
    idx: int


class _FakeHandle:
    def __init__(
        self,
        *,
        chunks: list[_FakeChunk],
        result=None,
        result_exc: Exception | None = None,
    ) -> None:
        self._chunks = chunks
        self._result = result
        self._result_exc = result_exc

    def stream(self, timeout_s: float):  # noqa: ANN001
        del timeout_s
        return iter(self._chunks)

    def result(self, timeout_s: float):  # noqa: ANN001
        del timeout_s
        if self._result_exc is not None:
            raise self._result_exc
        return self._result


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


class _BusyError(RuntimeError):
    code = "SESSION_TURN_BUSY"
    retry_after_s = 7


class APIStreamingTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_api_metrics()

    def tearDown(self) -> None:
        reset_api_metrics()

    def test_streaming_emits_meta_chunk_response_done_order(self) -> None:
        events: list[tuple[str, object]] = []
        observed_status: list[int] = []

        def _write_sse_event(*, event: str, data: object) -> None:
            events.append((event, data))

        submission = _FakeSubmission(
            handle=_FakeHandle(chunks=[_FakeChunk(1), _FakeChunk(2)], result=object()),
            request=_FakeRequest(session_id="s-1", trace_id="r-1"),
            timeout_s=1.0,
            session_id="s-1",
            run_id="r-1",
        )

        with (
            mock.patch(
                "openminion.api.server.streaming.open_turn_submission",
                return_value=submission,
            ),
            mock.patch(
                "openminion.api.server.streaming.close_submission"
            ) as close_submission,
            mock.patch(
                "openminion.api.server.streaming.turn_chunk_to_dict",
                side_effect=lambda c: {"idx": c.idx},
            ),
            mock.patch(
                "openminion.api.server.streaming.turn_response_to_dict",
                return_value={"final_text": "ok"},
            ),
        ):
            handle_turn_stream_request(
                body={"message": "hello"},
                request_id="req-1",
                config_path=None,
                runtime=None,
                start_sse_response=lambda: None,
                write_sse_event=_write_sse_event,
                write_json=lambda status, payload: (_ for _ in ()).throw(
                    AssertionError(f"unexpected json write {status} {payload}")
                ),
                observe_request_metrics=lambda **kwargs: (
                    observed_status.append(int(kwargs["status"])) or 0
                ),
                log_request_done=lambda **kwargs: None,
                perf_counter=lambda: 0.0,
            )

        self.assertEqual(
            [event for event, _ in events],
            ["meta", "chunk", "chunk", "response", "done"],
        )
        self.assertEqual(observed_status, [200])
        meta = events[0][1]
        self.assertIsInstance(meta, dict)
        self.assertEqual(meta["stream_schema_version"], TURN_STREAM_SCHEMA_VERSION)
        self.assertEqual(meta["capabilities"], list(TURN_STREAM_CAPABILITIES))
        close_submission.assert_called_once()

    def test_turn_response_errors_emit_response_error_and_done_error(self) -> None:
        events: list[tuple[str, object]] = []
        observed_status: list[int] = []
        submission = _FakeSubmission(
            handle=_FakeHandle(
                chunks=[],
                result=TurnResponse(
                    final_text="",
                    errors=[
                        TurnError(
                            code="provider_failed",
                            message="provider unavailable",
                            retryable=True,
                            details={"provider": "example"},
                        )
                    ],
                ),
            ),
            request=_FakeRequest(session_id="s-error", trace_id="r-error"),
            timeout_s=1.0,
            session_id="s-error",
            run_id="r-error",
        )

        with (
            mock.patch(
                "openminion.api.server.streaming.open_turn_submission",
                return_value=submission,
            ),
            mock.patch("openminion.api.server.streaming.close_submission"),
        ):
            handle_turn_stream_request(
                body={"message": "hello"},
                request_id="req-error",
                config_path=None,
                runtime=None,
                start_sse_response=lambda: None,
                write_sse_event=lambda *, event, data: events.append((event, data)),
                write_json=lambda status, payload: (_ for _ in ()).throw(
                    AssertionError(f"unexpected json write {status} {payload}")
                ),
                observe_request_metrics=lambda **kwargs: (
                    observed_status.append(int(kwargs["status"])) or 0
                ),
                log_request_done=lambda **kwargs: None,
                perf_counter=lambda: 0.0,
            )

        self.assertEqual(
            [event for event, _ in events],
            ["meta", "response", "error", "done"],
        )
        error = events[2][1]
        self.assertIsInstance(error, dict)
        self.assertEqual(error["code"], "provider_failed")
        self.assertEqual(error["details"], {"provider": "example"})
        self.assertEqual(events[-1][1]["status"], "error")
        self.assertEqual(observed_status, [int(HTTPStatus.INTERNAL_SERVER_ERROR)])

    def test_streaming_timeout_emits_error_and_done_error_status(self) -> None:
        events: list[tuple[str, object]] = []
        observed_status: list[int] = []

        def _write_sse_event(*, event: str, data: object) -> None:
            events.append((event, data))

        submission = _FakeSubmission(
            handle=_FakeHandle(chunks=[], result_exc=TimeoutError("slow")),
            request=_FakeRequest(session_id="s-2", trace_id="r-2"),
            timeout_s=1.0,
            session_id="s-2",
            run_id="r-2",
        )

        with (
            mock.patch(
                "openminion.api.server.streaming.open_turn_submission",
                return_value=submission,
            ),
            mock.patch(
                "openminion.api.server.streaming.close_submission"
            ) as close_submission,
            mock.patch(
                "openminion.api.server.streaming.turn_chunk_to_dict",
                side_effect=lambda c: {"idx": c.idx},
            ),
        ):
            handle_turn_stream_request(
                body={"message": "hello"},
                request_id="req-2",
                config_path=None,
                runtime=None,
                start_sse_response=lambda: None,
                write_sse_event=_write_sse_event,
                write_json=lambda status, payload: (_ for _ in ()).throw(
                    AssertionError(f"unexpected json write {status} {payload}")
                ),
                observe_request_metrics=lambda **kwargs: (
                    observed_status.append(int(kwargs["status"])) or 0
                ),
                log_request_done=lambda **kwargs: None,
                perf_counter=lambda: 0.0,
            )

        self.assertEqual([event for event, _ in events], ["meta", "error", "done"])
        error_event = events[1][1]
        self.assertIsInstance(error_event, dict)
        self.assertEqual(error_event.get("code"), "turn_timeout")
        done_event = events[2][1]
        self.assertIsInstance(done_event, dict)
        self.assertEqual(done_event.get("status"), "error")
        self.assertEqual(observed_status, [int(HTTPStatus.GATEWAY_TIMEOUT)])
        close_submission.assert_called_once()

    def test_streaming_open_busy_writes_retryable_conflict_json(self) -> None:
        captured: list[tuple[HTTPStatus, dict]] = []
        observed_status: list[int] = []

        with mock.patch(
            "openminion.api.server.streaming.open_turn_submission",
            side_effect=_BusyError("session turn is busy"),
        ):
            handle_turn_stream_request(
                body={"message": "hello"},
                request_id="req-busy-open",
                config_path=None,
                runtime=None,
                start_sse_response=lambda: (_ for _ in ()).throw(
                    AssertionError("SSE response should not start")
                ),
                write_sse_event=lambda **_kwargs: (_ for _ in ()).throw(
                    AssertionError("SSE event should not be written")
                ),
                write_json=lambda status, payload: captured.append((status, payload)),
                observe_request_metrics=lambda **kwargs: (
                    observed_status.append(int(kwargs["status"])) or 0
                ),
                log_request_done=lambda **kwargs: None,
                perf_counter=lambda: 0.0,
            )

        self.assertEqual(len(captured), 1)
        status, payload = captured[0]
        self.assertEqual(status, HTTPStatus.CONFLICT)
        self.assertEqual(payload["error"]["code"], "SESSION_TURN_BUSY")
        self.assertTrue(payload["error"]["retryable"])
        self.assertEqual(payload["error"]["retry_after_ms"], 7000)
        self.assertEqual(payload["error"]["details"]["retry_after_s"], 7)
        self.assertEqual(observed_status, [int(HTTPStatus.CONFLICT)])

    def test_streaming_result_busy_emits_error_and_conflict_metrics(self) -> None:
        events: list[tuple[str, object]] = []
        observed_status: list[int] = []

        submission = _FakeSubmission(
            handle=_FakeHandle(
                chunks=[],
                result_exc=_BusyError("session turn is busy"),
            ),
            request=_FakeRequest(session_id="s-busy", trace_id="r-busy"),
            timeout_s=1.0,
            session_id="s-busy",
            run_id="r-busy",
        )

        with (
            mock.patch(
                "openminion.api.server.streaming.open_turn_submission",
                return_value=submission,
            ),
            mock.patch("openminion.api.server.streaming.close_submission"),
        ):
            handle_turn_stream_request(
                body={"message": "hello"},
                request_id="req-busy-result",
                config_path=None,
                runtime=None,
                start_sse_response=lambda: None,
                write_sse_event=lambda *, event, data: events.append((event, data)),
                write_json=lambda status, payload: (_ for _ in ()).throw(
                    AssertionError(f"unexpected json write {status} {payload}")
                ),
                observe_request_metrics=lambda **kwargs: (
                    observed_status.append(int(kwargs["status"])) or 0
                ),
                log_request_done=lambda **kwargs: None,
                perf_counter=lambda: 0.0,
            )

        self.assertEqual([event for event, _ in events], ["meta", "error", "done"])
        error_event = events[1][1]
        self.assertIsInstance(error_event, dict)
        self.assertEqual(error_event.get("code"), "SESSION_TURN_BUSY")
        self.assertEqual(error_event.get("retry_after_ms"), 7000)
        self.assertEqual(error_event.get("details"), {"retry_after_s": 7})
        self.assertEqual(observed_status, [int(HTTPStatus.CONFLICT)])

    def test_sse_path_records_stream_route_metrics(self) -> None:
        handler = object.__new__(_OpenMinionAPIHandler)
        handler.path = "/v1/turn/stream"
        handler.headers = {
            "Accept": "text/event-stream",
            "X-Request-ID": "req-metric-1",
        }
        handler.config_path = None
        handler.runtime = None
        handler.runtime_bootstrap_error = None
        _install_json_body(handler, {"message": "hello"})
        handler.wfile = io.BytesIO()
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()

        submission = _FakeSubmission(
            handle=_FakeHandle(chunks=[_FakeChunk(1)], result=object()),
            request=_FakeRequest(session_id="s-3", trace_id="r-3"),
            timeout_s=1.0,
            session_id="s-3",
            run_id="r-3",
        )

        with (
            mock.patch(
                "openminion.api.server.streaming.open_turn_submission",
                return_value=submission,
            ),
            mock.patch("openminion.api.server.streaming.close_submission"),
            mock.patch(
                "openminion.api.server.streaming.turn_chunk_to_dict",
                side_effect=lambda c: {"idx": c.idx},
            ),
            mock.patch(
                "openminion.api.server.streaming.turn_response_to_dict",
                return_value={"final_text": "ok"},
            ),
        ):
            _OpenMinionAPIHandler.do_POST(handler)

        metrics = get_api_metrics_snapshot()
        by_route = metrics["requests"]["by_route"]
        self.assertEqual(by_route.get("POST /v1/turn/stream"), 1)

    def test_stream_json_and_sse_share_metrics_route_key(self) -> None:
        sync_submission = _FakeSubmission(
            handle=_FakeHandle(chunks=[], result=object()),
            request=_FakeRequest(session_id="s-json", trace_id="r-json"),
            timeout_s=1.0,
            session_id="s-json",
            run_id="r-json",
        )
        with (
            mock.patch(
                "openminion.api.routes.turns.open_turn_submission",
                return_value=sync_submission,
            ),
            mock.patch(
                "openminion.api.routes.turns.collect_sync_turn_payload",
                return_value={"trace_id": "r-json", "turn": {"trace_id": "r-json"}},
            ),
            mock.patch("openminion.api.routes.turns.close_submission"),
        ):
            status, payload = dispatch_request(
                "POST",
                "/v1/turn/stream",
                None,
                body={"message": "hello"},
                request_id="req-json-stream",
            )
        self.assertEqual(int(status), 200)
        self.assertTrue(payload["ok"])

        handler = object.__new__(_OpenMinionAPIHandler)
        handler.path = "/v1/turn/stream"
        handler.headers = {
            "Accept": "text/event-stream",
            "X-Request-ID": "req-sse-stream",
        }
        handler.config_path = None
        handler.runtime = None
        handler.runtime_bootstrap_error = None
        _install_json_body(handler, {"message": "hello"})
        handler.wfile = io.BytesIO()
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()

        sse_submission = _FakeSubmission(
            handle=_FakeHandle(chunks=[_FakeChunk(1)], result=object()),
            request=_FakeRequest(session_id="s-sse", trace_id="r-sse"),
            timeout_s=1.0,
            session_id="s-sse",
            run_id="r-sse",
        )
        with (
            mock.patch(
                "openminion.api.server.streaming.open_turn_submission",
                return_value=sse_submission,
            ),
            mock.patch("openminion.api.server.streaming.close_submission"),
            mock.patch(
                "openminion.api.server.streaming.turn_chunk_to_dict",
                side_effect=lambda c: {"idx": c.idx},
            ),
            mock.patch(
                "openminion.api.server.streaming.turn_response_to_dict",
                return_value={"final_text": "ok"},
            ),
        ):
            _OpenMinionAPIHandler.do_POST(handler)

        metrics = get_api_metrics_snapshot()
        by_route = metrics["requests"]["by_route"]
        by_route_status = metrics["requests"]["by_route_status_classes"]
        self.assertEqual(by_route.get("POST /v1/turn/stream"), 2)
        self.assertEqual(by_route_status["POST /v1/turn/stream"]["2xx"], 2)

    def test_sync_turn_response_errors_are_not_reported_as_success(self) -> None:
        submission = _FakeSubmission(
            handle=_FakeHandle(chunks=[], result=object()),
            request=_FakeRequest(session_id="s-json-error", trace_id="r-json-error"),
            timeout_s=1.0,
            session_id="s-json-error",
            run_id="r-json-error",
        )
        turn = {
            "trace_id": "r-json-error",
            "final_text": "",
            "errors": [
                {
                    "code": "provider_failed",
                    "message": "provider unavailable",
                    "retryable": True,
                    "details": {"provider": "example"},
                }
            ],
        }
        with (
            mock.patch(
                "openminion.api.routes.turns.open_turn_submission",
                return_value=submission,
            ),
            mock.patch(
                "openminion.api.routes.turns.collect_sync_turn_payload",
                return_value={"trace_id": "r-json-error", "turn": turn},
            ),
            mock.patch("openminion.api.routes.turns.close_submission"),
        ):
            status, payload = dispatch_request(
                "POST",
                "/v1/turn",
                None,
                body={"message": "hello"},
                request_id="req-json-error",
            )

        self.assertEqual(status, HTTPStatus.INTERNAL_SERVER_ERROR)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "provider_failed")
        self.assertTrue(payload["error"]["retryable"])
        self.assertEqual(payload["turn"], turn)

    def test_stream_endpoint_with_runtime_emits_real_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                handler = _build_stream_handler(
                    body={
                        "message": "hello from streaming runtime",
                        "session_id": "sse-runtime-1",
                        "stream": True,
                    },
                    request_id="req-runtime-sse-1",
                    runtime=runtime,
                    config_path=str(config_path),
                )
                _OpenMinionAPIHandler.do_POST(handler)

                event_names = [
                    event for event, _ in _parse_sse_events(handler.wfile.getvalue())
                ]
                self.assertGreaterEqual(len(event_names), 4)
                self.assertEqual(event_names[0], "meta")
                self.assertIn("chunk", event_names)
                self.assertIn("response", event_names)
                self.assertEqual(event_names[-1], "done")

                chunk_payloads = [
                    payload
                    for event, payload in _parse_sse_events(handler.wfile.getvalue())
                    if event == "chunk"
                ]
                status_chunks = [
                    payload
                    for payload in chunk_payloads
                    if isinstance(payload, dict) and payload.get("kind") == "status"
                ]
                self.assertTrue(status_chunks)
                first_status = status_chunks[0]
                self.assertEqual(first_status["data"]["status_key"], "working")
                self.assertIn("label", first_status["data"])

                done_payload = _parse_sse_events(handler.wfile.getvalue())[-1][1]
                self.assertIsInstance(done_payload, dict)
                self.assertEqual(done_payload.get("status"), "complete")
                self.assertIn(b"id: ", handler.wfile.getvalue())
            finally:
                runtime.close()

    def test_stream_endpoint_concurrent_clients_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                lock = threading.Lock()
                results: dict[int, list[str]] = {}
                errors: list[str] = []

                def _run_client(client_idx: int) -> None:
                    try:
                        handler = _build_stream_handler(
                            body={
                                "message": f"hello from stream client {client_idx}",
                                "session_id": f"sse-concurrent-{client_idx}",
                                "stream": True,
                            },
                            request_id=f"req-runtime-sse-{client_idx}",
                            runtime=runtime,
                            config_path=str(config_path),
                        )
                        _OpenMinionAPIHandler.do_POST(handler)
                        names = [
                            event
                            for event, _ in _parse_sse_events(handler.wfile.getvalue())
                        ]
                        with lock:
                            results[client_idx] = names
                    except Exception as exc:  # noqa: BLE001
                        with lock:
                            errors.append(str(exc))

                workers = [
                    threading.Thread(target=_run_client, args=(idx,)) for idx in (1, 2)
                ]
                for worker in workers:
                    worker.start()
                for worker in workers:
                    worker.join(timeout=30)

                self.assertFalse(errors)
                self.assertEqual(sorted(results.keys()), [1, 2])
                for names in results.values():
                    self.assertGreaterEqual(len(names), 4)
                    self.assertEqual(names[0], "meta")
                    self.assertIn("chunk", names)
                    self.assertIn("response", names)
                    self.assertEqual(names[-1], "done")

                metrics = get_api_metrics_snapshot()
                self.assertEqual(
                    metrics["requests"]["by_route"].get("POST /v1/turn/stream"), 2
                )
            finally:
                runtime.close()


def _build_stream_handler(
    *,
    body: dict,
    request_id: str,
    runtime: APIRuntime,
    config_path: str,
) -> _OpenMinionAPIHandler:
    handler = object.__new__(_OpenMinionAPIHandler)
    handler.path = "/v1/turn/stream"
    handler.headers = {"Accept": "text/event-stream", "X-Request-ID": request_id}
    handler.config_path = config_path
    handler.runtime = runtime
    handler.runtime_bootstrap_error = None
    _install_json_body(handler, body)
    handler.wfile = io.BytesIO()
    handler.send_response = mock.Mock()
    handler.send_header = mock.Mock()
    handler.end_headers = mock.Mock()
    return handler


def _parse_sse_events(payload: bytes) -> list[tuple[str, object]]:
    events: list[tuple[str, object]] = []
    for block in payload.decode("utf-8").strip().split("\n\n"):
        if not block.strip():
            continue
        event_name: str | None = None
        event_data: object = {}
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.partition(":")[2].strip()
            elif line.startswith("data:"):
                raw = line.partition(":")[2].strip()
                event_data = json.loads(raw)
        if event_name:
            events.append((event_name, event_data))
    return events


def _write_echo_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    os.environ["OPENMINION_DATA_ROOT"] = str(tmp_path / ".openminion")
    config.runtime.log_level = "ERROR"
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(tmp_path / "state" / "api-streaming-sse.db")
    save_config(config, str(config_path))
    return config_path


class APIStreamingNegotiationTests(unittest.TestCase):
    def test_do_post_turn_stream_uses_sse_when_accepts_event_stream(self) -> None:
        handler = object.__new__(_OpenMinionAPIHandler)
        handler.path = "/v1/turn/stream"
        handler.headers = {"Accept": "text/event-stream", "X-Request-ID": "req-sse-1"}
        handler.config_path = None
        handler.runtime = None
        handler.runtime_bootstrap_error = None
        _install_json_body(handler, {"message": "hello"})
        handler._write_json = mock.Mock()  # type: ignore[attr-defined]

        with (
            mock.patch("openminion.api.server.app.dispatch_request") as dispatch,
            mock.patch(
                "openminion.api.server.app.handle_http_turn_stream_request"
            ) as handle_stream,
        ):
            _OpenMinionAPIHandler.do_POST(handler)

        handle_stream.assert_called_once_with(
            handler,
            body={"message": "hello"},
            request_id="req-sse-1",
        )
        handler._write_json.assert_not_called()  # type: ignore[attr-defined]
        dispatch.assert_not_called()

    def test_do_post_turn_stream_defaults_to_json_dispatch(self) -> None:
        handler = object.__new__(_OpenMinionAPIHandler)
        handler.path = "/v1/turn/stream"
        handler.headers = {"Accept": "application/json", "X-Request-ID": "req-json-1"}
        handler.config_path = "cfg.json"
        handler.runtime = None
        handler.runtime_bootstrap_error = None
        _install_json_body(handler, {"message": "hello"})
        handler._write_json = mock.Mock()  # type: ignore[attr-defined]

        with mock.patch(
            "openminion.api.server.app.dispatch_request",
            return_value=(HTTPStatus.OK, {"ok": True}),
        ) as dispatch:
            _OpenMinionAPIHandler.do_POST(handler)

        dispatch.assert_called_once_with(
            "POST",
            "/v1/turn/stream",
            "cfg.json",
            body={"message": "hello"},
            runtime=None,
            runtime_bootstrap_error=None,
            request_headers=handler.headers,
            request_id="req-json-1",
        )
        handler._write_json.assert_called_once_with(HTTPStatus.OK, {"ok": True})  # type: ignore[attr-defined]

    def test_do_get_routes_sse_attach_with_cursor(self) -> None:
        handler = object.__new__(_OpenMinionAPIHandler)
        handler.path = "/v1/turn/trace-1/stream?after_sequence=7"
        handler.headers = {
            "Accept": "text/event-stream",
            "Last-Event-ID": "trace-1:6",
            "X-Request-ID": "req-attach",
        }
        handler.config_path = None
        handler.runtime = None
        handler.runtime_bootstrap_error = None

        with (
            mock.patch("openminion.api.server.app.dispatch_request") as dispatch,
            mock.patch(
                "openminion.api.server.app.try_handle_turn_stream_attach",
                return_value=True,
            ) as handle_attach,
        ):
            _OpenMinionAPIHandler.do_GET(handler)

        handle_attach.assert_called_once_with(
            handler,
            parsed=mock.ANY,
            request_id="req-attach",
        )
        dispatch.assert_not_called()


class APIStreamAttachTests(unittest.TestCase):
    def test_http_attach_parser_uses_query_cursor(self) -> None:
        handler = SimpleNamespace(
            headers={
                "Accept": "text/event-stream",
                "Last-Event-ID": "trace-attach:6",
            },
            config_path=None,
            runtime=None,
            _write_sse_event=mock.Mock(),
            _write_json=mock.Mock(),
        )
        parsed = urlparse("/v1/turn/trace-attach/stream?after_sequence=7")

        with mock.patch(
            "openminion.api.server.streaming.handle_turn_stream_attach_request"
        ) as attach:
            handled = try_handle_turn_stream_attach(
                handler,
                parsed=parsed,
                request_id="req-attach",
            )

        self.assertTrue(handled)
        self.assertEqual(attach.call_args.kwargs["trace_id"], "trace-attach")
        self.assertEqual(attach.call_args.kwargs["after_sequence"], 7)
        self.assertEqual(attach.call_args.kwargs["request_id"], "req-attach")

    def test_active_status_route_returns_language_neutral_snapshot(self) -> None:
        handle = SimpleNamespace(
            session_id="session-status",
            agent_id="agent-status",
            stream_schema_version=TURN_STREAM_SCHEMA_VERSION,
            replay_floor_sequence=4,
            current_phase_status=lambda: {
                "schema_version": "openminion.phase-status.v1",
                "status_key": "executing",
                "facts": {
                    "schema_version": "openminion.phase-status.v1",
                    "status_key": "executing",
                    "step_index": 2,
                },
            },
        )
        runtime = SimpleNamespace(
            runtime_manager=SimpleNamespace(
                get_turn_handle=lambda trace_id: (
                    handle if trace_id == "trace-status" else None
                )
            )
        )

        status, payload = dispatch_request(
            "GET",
            "/v1/turn/trace-status/status",
            None,
            runtime=runtime,
            request_id="req-status",
        )

        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"]["facts"]["status_key"], "executing")
        self.assertEqual(
            payload["stream"]["schema_version"], TURN_STREAM_SCHEMA_VERSION
        )
        self.assertEqual(payload["stream"]["replay_floor_sequence"], 4)

    def test_attach_replays_from_cursor_with_sse_ids(self) -> None:
        chunks = [
            TurnChunk(
                trace_id="trace-attach",
                kind="status",
                data={"status_key": "working"},
                sequence=2,
                event_id="trace-attach:2",
            ),
            TurnChunk(
                trace_id="trace-attach",
                kind="final_text",
                data={"text": "done"},
                sequence=3,
                event_id="trace-attach:3",
            ),
        ]

        class _AttachHandle:
            session_id = "session-attach"
            agent_id = "agent-attach"
            replay_floor_sequence = 1

            def subscribe(self, *, after_sequence, timeout_s):  # noqa: ANN001
                del timeout_s
                return iter(
                    chunk for chunk in chunks if chunk.sequence > after_sequence
                )

            def result(self, timeout_s):  # noqa: ANN001
                del timeout_s
                return TurnResponse(final_text="done")

        handle = _AttachHandle()
        runtime = SimpleNamespace(
            runtime_manager=SimpleNamespace(
                get_turn_handle=lambda trace_id: (
                    handle if trace_id == "trace-attach" else None
                )
            )
        )
        events: list[tuple[str, object, str | None]] = []

        handle_turn_stream_attach_request(
            trace_id="trace-attach",
            after_sequence=1,
            request_id="req-attach",
            config_path=None,
            runtime=runtime,
            start_sse_response=lambda: None,
            write_sse_event=lambda *, event, data, event_id=None: events.append(
                (event, data, event_id)
            ),
            write_json=lambda status, payload: (_ for _ in ()).throw(
                AssertionError(f"unexpected json write {status} {payload}")
            ),
            observe_request_metrics=lambda **kwargs: 0,
            log_request_done=lambda **kwargs: None,
            perf_counter=lambda: 0.0,
        )

        self.assertEqual(
            [event for event, _, _ in events],
            ["meta", "chunk", "chunk", "response", "done"],
        )
        self.assertEqual(events[1][2], "trace-attach:2")
        self.assertEqual(events[2][2], "trace-attach:3")
        self.assertEqual(events[0][1]["replay_floor_sequence"], 1)


class APIIPCAuthenticationTests(unittest.TestCase):
    def _handler(self, token: str | None) -> _OpenMinionAPIHandler:
        handler = object.__new__(_OpenMinionAPIHandler)
        handler.path = "/v1/health"
        handler.headers = {
            "X-Request-ID": "req-auth",
            **({"X-IPC-Token": token} if token is not None else {}),
        }
        handler.config_path = None
        handler.runtime = None
        handler.runtime_bootstrap_error = None
        handler.ipc_token = "configured-secret"
        handler.wfile = io.BytesIO()
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        return handler

    def test_missing_or_wrong_ipc_token_is_rejected_before_dispatch(self) -> None:
        for token in (None, "wrong"):
            handler = self._handler(token)
            with mock.patch("openminion.api.server.app.dispatch_request") as dispatch:
                _OpenMinionAPIHandler.do_GET(handler)
            dispatch.assert_not_called()
            handler.send_response.assert_called_once_with(int(HTTPStatus.UNAUTHORIZED))
            payload = json.loads(handler.wfile.getvalue())
            self.assertEqual(payload["error"]["code"], "ipc_auth_required")

    def test_matching_ipc_token_reaches_dispatch(self) -> None:
        handler = self._handler("configured-secret")
        handler._write_json = mock.Mock()  # type: ignore[attr-defined]
        with mock.patch(
            "openminion.api.server.app.dispatch_request",
            return_value=(HTTPStatus.OK, {"ok": True}),
        ) as dispatch:
            _OpenMinionAPIHandler.do_GET(handler)

        dispatch.assert_called_once()
        handler._write_json.assert_called_once_with(HTTPStatus.OK, {"ok": True})  # type: ignore[attr-defined]

    def test_sse_request_requires_token_before_stream_setup(self) -> None:
        handler = self._handler(None)
        handler.path = "/v1/turn/stream"
        handler.headers = {
            "Accept": "text/event-stream",
            "X-Request-ID": "req-sse-auth",
            "Content-Length": "999",
        }
        with mock.patch(
            "openminion.api.server.app.handle_http_turn_stream_request"
        ) as handle_stream:
            _OpenMinionAPIHandler.do_POST(handler)

        handle_stream.assert_not_called()
        handler.send_response.assert_called_once_with(int(HTTPStatus.UNAUTHORIZED))
