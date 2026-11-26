"""Offline proofs of the Focus observer's physical-send and privacy boundary."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from http.server import BaseHTTPRequestHandler
import io
import json
import logging
import socket
from socketserver import TCPServer
import subprocess
from threading import Barrier, Event, Thread
from time import monotonic
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.response import addinfourl

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.providers.factory import build_runtime_llm_handle
from openminion.modules.llm.providers.transport import curl, http, sse
from openminion.modules.llm.providers.transport.client import ProviderHTTPClient
from tests._csc_fixtures import _csc_install_default_agent
from tests.e2e.cli.focus.harness.memory_observation import (
    ObservationError,
    observe_requests,
)
from tests.e2e.cli.focus.harness import memory_observation

pytestmark = pytest.mark.e2e

ENDPOINT = "https://api.minimax.io/v1/chat/completions"
MODEL = "MiniMax-M3.1-Flash"
NONCE = "0123456789abcdef0123456789abcdef"
HEADERS = {"Authorization": "Bearer offline-only", "Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    real_connect = socket.socket.connect

    def deny(*args, **kwargs):
        pytest.fail("external I/O is forbidden in observer tests")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(subprocess, "Popen", deny)
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "0")
    monkeypatch.setenv("OPENMINION_LLM_DEBUG", "0")
    return real_connect


@pytest.fixture
def observation_args(tmp_path):
    return dict(
        endpoint=ENDPOINT,
        model=MODEL,
        request_limit=12,
        body_limit=262144,
        output_limit=32768,
        timeout_limit=60,
        deadline_monotonic=monotonic() + 60,
        session_id="session-test",
        target_hashes={"target": sha256(NONCE.encode()).hexdigest()},
        evidence_path=tmp_path / "observation.jsonl",
    )


def _response(request, *, text="offline answer", code=200, headers=None):
    payload = {
        "choices": [
            {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
    }
    response = addinfourl(
        io.BytesIO(json.dumps(payload).encode()), headers or {}, request.full_url, code
    )
    response.msg = "offline"
    return response


@pytest.fixture
def offline_io(monkeypatch):
    sent = []

    def open_response(opener, request, *, timeout):
        sent.append(request)
        return _response(request)

    monkeypatch.setattr(urllib_request.OpenerDirector, "open", open_response)
    return sent


def _payload(**overrides):
    return {
        "model": MODEL,
        "messages": [{"role": "user", "content": NONCE}],
        "max_tokens": 32768,
        **overrides,
    }


def _post(payload=None, **overrides):
    return http.http_json_post(
        **{
            "url": ENDPOINT,
            "payload": _payload() if payload is None else payload,
            "headers": HEADERS,
            "timeout_seconds": 60,
            "provider_name": "openai",
            "trace_metadata": {
                "session_id": "session-test",
                "llm_call_id": "call-test",
            },
            "env": {},
            **overrides,
        }
    )


def _trace(body, **overrides):
    http.trace_http_json_request(
        **{
            "url": ENDPOINT,
            "body_json": body,
            "headers": HEADERS,
            "timeout_seconds": 60,
            "transport": "urllib",
            **overrides,
        }
    )


def _rows(args):
    return [json.loads(line) for line in args["evidence_path"].read_text().splitlines()]


def test_exact_wire_matches_all_string_leaves_and_only_allowlisted_metadata(
    observation_args, offline_io
):
    payload = _payload(
        messages=[{"role": "user", "content": "ordinary text"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "memory_search",
                    "description": f"nested {NONCE}",
                    "parameters": {"type": "object"},
                },
            }
        ],
    )
    before = json.dumps(payload).encode()
    with observe_requests(**observation_args):
        _post(
            payload,
            trace_metadata={
                "llm_call_id": "call-test",
                "capture_id": "capture:test",
                "inference_step": 2,
                "private": NONCE,
                "turn_id": "",
            },
        )
    assert offline_io[0].data == before
    assert offline_io[0].get_header("Authorization") == HEADERS["Authorization"]
    request = next(row for row in _rows(observation_args) if row["event"] == "request")
    assert request["target_matches"] == ["target"]
    assert request["canonical"] == {
        "llm_call_id": "call-test",
        "capture_id": "capture:test",
        "inference_step": 2,
    }
    assert request["observer_session_id"] == "session-test"
    assert request["output_cap"] == 32768
    evidence = observation_args["evidence_path"].read_text()
    for content in (
        NONCE,
        "ordinary text",
        "offline-only",
        "offline answer",
        "memory_search",
    ):
        assert content not in evidence


def test_n_plus_one_latches_even_when_caller_swallows_failure(
    observation_args, offline_io
):
    observation_args["request_limit"] = 2
    with (
        pytest.raises(ObservationError, match="request_limit"),
        observe_requests(**observation_args),
    ):
        _post()
        _post()
        for _ in range(2):
            with pytest.raises(ObservationError, match="request_limit"):
                _post()
    assert len(offline_io) == 2
    assert (
        len([row for row in _rows(observation_args) if row["event"] == "rejected"]) == 1
    )


def test_concurrent_reservations_never_exceed_limit(observation_args, offline_io):
    observation_args["request_limit"] = 3
    barrier = Barrier(10)

    def call():
        barrier.wait()
        try:
            _post()
        except ObservationError:
            return False
        return True

    with (
        pytest.raises(ObservationError, match="request_limit"),
        observe_requests(**observation_args),
    ):
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(lambda _: call(), range(10)))
    assert sum(results) == len(offline_io) <= 3
    reservations = [
        row["request_index"]
        for row in _rows(observation_args)
        if row["event"] == "reserved"
    ]
    assert reservations == [1, 2, 3]


def test_failed_io_reservations_are_not_refunded(observation_args, monkeypatch):
    observation_args["request_limit"] = 2
    sent = []

    def unavailable(opener, request, *, timeout):
        sent.append(request)
        raise urllib_error.HTTPError(
            ENDPOINT, 503, "offline unavailable", {}, io.BytesIO(b"{}")
        )

    monkeypatch.setattr(urllib_request.OpenerDirector, "open", unavailable)
    with (
        pytest.raises(ObservationError, match="request_limit"),
        observe_requests(**observation_args),
    ):
        for _ in range(2):
            with pytest.raises(LLMCtlError):
                _post()
        _post()
    assert len(sent) == 2
    assert [
        row["request_index"]
        for row in _rows(observation_args)
        if row["event"] == "request"
    ] == [1, 2]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"url": "https://wrong.invalid/v1/chat/completions"}, "endpoint_mismatch"),
        ({"timeout_seconds": 61}, "timeout_limit"),
        ({"timeout_seconds": float("nan")}, "timeout_limit"),
        ({"timeout_seconds": 0}, "timeout_limit"),
    ],
)
def test_endpoint_and_timeout_rejected(observation_args, offline_io, overrides, reason):
    with (
        pytest.raises(ObservationError, match=reason),
        observe_requests(**observation_args),
    ):
        _post(**overrides)
    assert offline_io == []


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"model": "other"}, "model_mismatch"),
        ({"max_tokens": 32769}, "output_limit"),
        ({"max_tokens": None}, "output_limit"),
        ({"max_tokens": True}, "output_limit"),
        ({"max_tokens": "32768"}, "output_limit"),
        ({"max_completion_tokens": 32769}, "output_limit"),
        ({"max_completion_tokens": 100}, "conflicting_output_caps"),
        ({"stream": True}, "stream_denied"),
        ({"n": 2}, "multiple_outputs_denied"),
        ({"n": True}, "multiple_outputs_denied"),
        ({"service_tier": "priority"}, "service_tier_denied"),
        ({"service_tier": None}, "service_tier_denied"),
        ({"modalities": ["audio"]}, "media_denied"),
        ({"audio": {}}, "media_denied"),
        (
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64,AA=="},
                            }
                        ],
                    }
                ]
            },
            "media_denied",
        ),
    ],
)
def test_payload_bounds_fail_before_send(observation_args, offline_io, changes, reason):
    with (
        pytest.raises(ObservationError, match=reason),
        observe_requests(**observation_args),
    ):
        _post(_payload(**changes))
    assert offline_io == []


@pytest.mark.parametrize(
    "caps",
    [
        {},
        {"max_completion_tokens": 32768},
        {"max_tokens": 32768, "max_completion_tokens": 32768},
    ],
)
def test_output_cap_spelling_and_missing_cap(observation_args, offline_io, caps):
    payload = _payload()
    del payload["max_tokens"]
    payload.update(caps)
    if not caps:
        with (
            pytest.raises(ObservationError, match="output_limit"),
            observe_requests(**observation_args),
        ):
            _post(payload)
        assert offline_io == []
    else:
        with observe_requests(**observation_args):
            _post(payload)
        assert json.loads(offline_io[0].data) == payload


def test_body_limit_is_serialized_utf8_bytes(observation_args, offline_io):
    body = json.dumps(
        _payload(messages=[{"role": "user", "content": "\u00e9" * 20}]),
        ensure_ascii=False,
    )
    observation_args["body_limit"] = len(body)
    assert len(body.encode()) > len(body)
    with (
        pytest.raises(ObservationError, match="body_limit"),
        observe_requests(**observation_args),
    ):
        _trace(body)
    assert offline_io == []


@pytest.mark.parametrize("change", ["body", "headers", "url", "method", "timeout"])
def test_wire_must_equal_trace_before_urlopen(observation_args, offline_io, change):
    body = json.dumps(_payload())
    request = urllib_request.Request(
        ENDPOINT, data=body.encode(), headers=HEADERS, method="POST"
    )
    if change == "body":
        request.data += b" "
    elif change == "headers":
        request.add_header("Extra", "changed")
    elif change == "url":
        request.full_url = "https://wrong.invalid/"
    elif change == "method":
        request.method = "GET"
    with (
        pytest.raises(
            ObservationError,
            match="timeout_limit" if change == "timeout" else "wire_mismatch",
        ),
        observe_requests(**observation_args),
    ):
        _trace(body)
        urllib_request.urlopen(request, timeout=61 if change == "timeout" else 60)
    assert offline_io == []
    assert not any(row["event"] == "request" for row in _rows(observation_args))


def test_unobserved_or_replayed_send_is_rejected(observation_args, offline_io):
    with (
        pytest.raises(ObservationError, match="unobserved_send"),
        observe_requests(**observation_args),
    ):
        _post()
        urllib_request.urlopen(offline_io[0], timeout=60)
    assert len(offline_io) == 1


@pytest.mark.parametrize("transport", ["sse", "curl", "pool", "pool_direct"])
def test_other_transports_denied(observation_args, offline_io, transport):
    kwargs = dict(
        url=ENDPOINT,
        payload=_payload(),
        headers=HEADERS,
        timeout_seconds=60,
        provider_name="openai",
        env={},
    )
    with (
        pytest.raises(ObservationError, match="transport_denied"),
        observe_requests(**observation_args),
    ):
        if transport == "sse":
            next(sse.iter_sse_post_lines(**kwargs))
        elif transport == "curl":
            curl.curl_json_post(
                **kwargs,
                body_json=None,
                reason="offline",
                with_default_user_agent_fn=lambda headers: headers,
            )
        elif transport == "pool":
            _post(http_client=ProviderHTTPClient())
        else:
            ProviderHTTPClient().urlopen(urllib_request.Request(ENDPOINT), timeout=60)
    assert offline_io == []


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_real_urllib_opener_denies_redirect_before_followup(
    observation_args, monkeypatch, code
):
    sent = []

    def redirect(handler, request):
        sent.append(request)
        return _response(
            request, code=code, headers={"location": "https://wrong.invalid/redirect"}
        )

    monkeypatch.setattr(urllib_request.HTTPSHandler, "https_open", redirect)
    with (
        pytest.raises(ObservationError, match="redirect_denied"),
        observe_requests(**observation_args),
    ):
        _post()
    assert len(sent) == 1


def test_transport_content_tracing_disabled_and_patches_restored(
    observation_args, offline_io, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("content trace was invoked")

    monkeypatch.setattr(http, "trace_http_json_request", forbidden)
    monkeypatch.setattr(http, "trace_http_json_response", forbidden)
    monkeypatch.setattr(http, "write_llm_debug_event", forbidden)
    monkeypatch.setattr(curl, "write_llm_debug_event", forbidden)
    original_urlopen = urllib_request.urlopen
    with observe_requests(**observation_args):
        _post(env={"OPENMINION_LLM_DEBUG": "1", "OPENMINION_TRACE_REQUESTS": "1"})
    assert http.trace_http_json_request is forbidden
    assert http.trace_http_json_response is forbidden
    assert http.write_llm_debug_event is forbidden
    assert curl.write_llm_debug_event is forbidden
    assert urllib_request.urlopen is original_urlopen


def test_target_in_canonical_metadata_is_not_persisted(observation_args, offline_io):
    with (
        pytest.raises(ObservationError, match="target_in_metadata"),
        observe_requests(**observation_args),
    ):
        _post(trace_metadata={"record_id": NONCE})
    assert NONCE not in observation_args["evidence_path"].read_text()
    assert offline_io == []


@pytest.mark.parametrize("empty_first", [False, True])
def test_actual_configured_minimax_provider_path_and_adapter_retry(
    observation_args, monkeypatch, empty_first
):
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="openai")
    provider = config.providers.openai
    provider.api_key = "offline-only"
    provider.base_url = ENDPOINT.removesuffix("/chat/completions")
    provider.model = MODEL
    provider.max_tokens = 32768
    provider.timeout_seconds = 60
    provider.http_connection_reuse_enabled = False
    sent = []

    def open_response(opener, request, *, timeout):
        sent.append(request)
        return _response(
            request, text="" if empty_first and len(sent) == 1 else "offline answer"
        )

    monkeypatch.setattr(urllib_request.OpenerDirector, "open", open_response)
    with observe_requests(**observation_args):
        handle = build_runtime_llm_handle(config, logging.getLogger(__name__))
        try:
            response = handle.client.complete(
                [{"role": "user", "content": NONCE}],
                metadata={
                    "session_id": "session-test",
                    "llm_call_id": "call-configured",
                },
            )
            assert response.error is None
            assert response.output_text == "offline answer"
        finally:
            handle.close()
    requests = [row for row in _rows(observation_args) if row["event"] == "request"]
    assert len(requests) == len(sent) == (2 if empty_first else 1)
    assert all(row["target_matches"] == ["target"] for row in requests)
    assert all(row["canonical"]["llm_call_id"] == "call-configured" for row in requests)
    assert all("record_id" not in row["canonical"] for row in requests)
    assert [row["request_index"] for row in requests] == list(range(1, len(sent) + 1))


@pytest.mark.parametrize(
    "content",
    ["0" + NONCE, NONCE + "f", NONCE + NONCE],
    ids=["hex-prefix", "hex-suffix", "concatenated"],
)
def test_nonce_scan_detects_overlapping_windows(observation_args, offline_io, content):
    payload = _payload(messages=[{"role": "assistant", "content": content}])
    with observe_requests(**observation_args):
        _post(payload)
    row = next(row for row in _rows(observation_args) if row["event"] == "request")
    assert row["target_matches"] == ["target"]
    assert offline_io[0].data == json.dumps(payload).encode()
    assert NONCE not in observation_args["evidence_path"].read_text()


def test_nonce_scan_uses_decoded_string_values_not_dictionary_keys(
    observation_args, offline_io
):
    other = "fedcba9876543210fedcba9876543210"
    absent = "abcdef0123456789abcdef0123456789"
    observation_args["target_hashes"].update(
        {
            "other": sha256(other.encode()).hexdigest(),
            "key_only": sha256(absent.encode()).hexdigest(),
        }
    )
    payload = _payload(n=1, service_tier="standard", modalities=["text"])
    payload["tools"] = [
        {
            "type": "function",
            "function": {
                "name": "memory_write",
                "parameters": {
                    "properties": {
                        absent: {"enum": [[f"embedded:{other}", 42, None]]},
                    }
                },
            },
        }
    ]
    body = json.dumps(payload).replace(NONCE, "\\u0030" + NONCE[1:])
    observation_args["body_limit"] = len(body.encode())
    with observe_requests(**observation_args):
        _trace(body, payload={"misleading": "not the serialized payload"})
        request = urllib_request.Request(
            ENDPOINT, data=body.encode(), headers=HEADERS, method="POST"
        )
        with urllib_request.urlopen(request, timeout=60):
            pass
    row = next(row for row in _rows(observation_args) if row["event"] == "request")
    assert row["target_matches"] == ["other", "target"]
    assert row["canonical"] == {}
    assert offline_io[0].data == body.encode()


def test_latched_failure_restores_patches_on_exit(observation_args, offline_io):
    original = http.trace_http_json_request
    with (
        pytest.raises(ObservationError, match="invalid_json"),
        observe_requests(**observation_args),
    ):
        try:
            _trace("not JSON containing " + NONCE)
        except ObservationError:
            pass
        with pytest.raises(ObservationError, match="invalid_json"):
            _post()
    assert http.trace_http_json_request is original
    assert offline_io == []
    assert NONCE not in observation_args["evidence_path"].read_text()


def test_nested_observer_cannot_reset_reservations(observation_args, offline_io):
    with observe_requests(**observation_args):
        _post()
        with pytest.raises(ObservationError, match="observer_already_installed"):
            with observe_requests(**observation_args):
                pytest.fail("nested observer was installed")
        _post()
    assert [
        row["request_index"]
        for row in _rows(observation_args)
        if row["event"] == "request"
    ] == [1, 2]


def test_evidence_failure_latches_before_io(observation_args, offline_io, monkeypatch):
    path_type = type(observation_args["evidence_path"])

    class Unwritable(io.StringIO):
        def write(self, value):
            raise OSError("sensitive disk error")

    monkeypatch.setattr(path_type, "open", lambda *args, **kwargs: Unwritable())
    with (
        pytest.raises(ObservationError, match="evidence_write_failed"),
        observe_requests(**observation_args),
    ):
        for _ in range(2):
            with pytest.raises(ObservationError, match="evidence_write_failed"):
                _post()
    assert offline_io == []


def test_finish_is_idempotent_and_emits_one_completed_row(observation_args, offline_io):
    with observe_requests(**observation_args) as observation:
        _post()
        observation.finish()
        observation.finish()
        assert _rows(observation_args)[-1] == {"event": "completed", "reservations": 1}
    assert (
        len([row for row in _rows(observation_args) if row["event"] == "completed"])
        == 1
    )


def test_finish_rejects_late_worker_before_network(observation_args, offline_io):
    with (
        pytest.raises(ObservationError, match="observer_closed"),
        observe_requests(**observation_args) as observation,
    ):
        _post()
        observation.finish()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(ObservationError, match="observer_closed"):
                pool.submit(_post).result(timeout=2)
    assert len(offline_io) == 1
    assert _rows(observation_args)[-1]["event"] == "rejected"


def test_finish_rejects_outstanding_trace(observation_args, offline_io):
    with (
        pytest.raises(ObservationError, match="incomplete_dispatch"),
        observe_requests(**observation_args) as observation,
    ):
        _trace(json.dumps(_payload()))
        observation.finish()
    assert offline_io == []
    assert not any(row["event"] == "completed" for row in _rows(observation_args))


def test_finish_rejects_active_open_and_closes_admission(observation_args, monkeypatch):
    entered = Event()
    release = Event()
    sent = []

    def blocked_open(opener, request, *, timeout):
        sent.append(request)
        entered.set()
        assert release.wait(timeout=2)
        return _response(request)

    monkeypatch.setattr(urllib_request.OpenerDirector, "open", blocked_open)
    with (
        pytest.raises(ObservationError, match="incomplete_dispatch"),
        observe_requests(**observation_args) as observation,
    ):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_post)
            try:
                assert entered.wait(timeout=2)
                with pytest.raises(ObservationError, match="incomplete_dispatch"):
                    observation.finish()
                with pytest.raises(ObservationError, match="incomplete_dispatch"):
                    _post()
            finally:
                release.set()
            future.result(timeout=2)
    assert len(sent) == 1
    assert not any(row["event"] == "completed" for row in _rows(observation_args))


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1:43821",
        "api.minimax.io",
        "localhost:43821",
        "127.0.0.1",
        "127.0.0.1:0",
        "127.0.0.1:65536",
        "127.0.0.1:invalid",
        "127.0.0.1.:43821",
        "127.1:43821",
        "[::1]:43821",
    ],
)
def test_cleartext_endpoint_is_loopback_only(observation_args, offline_io, host):
    endpoint = f"http://{host}/v1/chat/completions"
    observation_args["endpoint"] = endpoint
    if host != "127.0.0.1:43821":
        with pytest.raises(ValueError, match="invalid observation endpoint"):
            with observe_requests(**observation_args):
                pytest.fail("nonloopback HTTP was allowed")
        assert offline_io == []
    else:
        with observe_requests(**observation_args):
            _post(url=endpoint)
        assert offline_io[0].full_url == endpoint


def test_real_urllib_post_to_literal_loopback(
    observation_args, monkeypatch, no_external_io
):
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                (
                    self.path,
                    self.headers["Authorization"],
                    self.rfile.read(int(self.headers["Content-Length"])),
                )
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    with TCPServer(("127.0.0.1", 0), Receiver) as server:
        address = server.server_address

        def connect(sock, destination):
            assert destination == address, "nonfixture socket destination"
            return no_external_io(sock, destination)

        def resolve(host, port, *args, **kwargs):
            assert (host, port) == address, "nonfixture hostname resolution"
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", address)
            ]

        monkeypatch.setattr(socket.socket, "connect", connect)
        monkeypatch.setattr(socket, "getaddrinfo", resolve)
        monkeypatch.setenv("no_proxy", "*")
        endpoint = f"http://127.0.0.1:{address[1]}/v1/chat/completions"
        observation_args["endpoint"] = endpoint
        worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        worker.start()
        try:
            with observe_requests(**observation_args):
                assert _post(url=endpoint, timeout_seconds=2) == {}
        finally:
            server.shutdown()
            worker.join(timeout=2)
        assert not worker.is_alive()
    assert received == [
        (
            "/v1/chat/completions",
            HEADERS["Authorization"],
            json.dumps(_payload()).encode(),
        )
    ]
    assert _rows(observation_args)[-1]["event"] == "completed"


@pytest.mark.parametrize("deadline", [float("inf"), float("nan"), 0, -1, True])
def test_deadline_must_be_finite_and_positive(observation_args, offline_io, deadline):
    observation_args["deadline_monotonic"] = deadline
    with pytest.raises(ValueError, match="invalid observation deadline"):
        with observe_requests(**observation_args):
            pytest.fail("invalid deadline was accepted")
    assert offline_io == []


def test_expired_campaign_cannot_install_fresh_admission_window(
    observation_args, offline_io, monkeypatch
):
    observation_args["deadline_monotonic"] = 100
    monkeypatch.setattr(memory_observation, "monotonic", lambda: 100)
    with pytest.raises(ObservationError, match="admission_deadline_expired"):
        with observe_requests(**observation_args):
            pytest.fail("expired campaign was admitted")
    assert offline_io == []
    assert _rows(observation_args) == [
        {"event": "rejected", "reason": "admission_deadline_expired", "reservations": 0}
    ]


def test_deadline_between_trace_and_send_latches_without_refund(
    observation_args, offline_io, monkeypatch
):
    clock = [100]
    observation_args["deadline_monotonic"] = 101
    monkeypatch.setattr(memory_observation, "monotonic", lambda: clock[0])
    body = json.dumps(_payload())
    request = urllib_request.Request(
        ENDPOINT, data=body.encode(), headers=HEADERS, method="POST"
    )
    with (
        pytest.raises(ObservationError, match="admission_deadline_expired"),
        observe_requests(**observation_args),
    ):
        _trace(body)
        clock[0] = 101
        with pytest.raises(ObservationError, match="admission_deadline_expired"):
            urllib_request.urlopen(request, timeout=60)
        clock[0] = 100
        with pytest.raises(ObservationError, match="admission_deadline_expired"):
            _post()
    assert offline_io == []
    assert _rows(observation_args)[-1]["reservations"] == 1


def test_deadline_rechecked_after_evidence_flush_before_io(
    observation_args, offline_io, monkeypatch
):
    clock = [100]
    observation_args["deadline_monotonic"] = 101
    monkeypatch.setattr(memory_observation, "monotonic", lambda: clock[0])
    write = memory_observation._Observation.write

    def delayed_write(observation, event, **facts):
        write(observation, event, **facts)
        if event == "request":
            clock[0] = 101

    monkeypatch.setattr(memory_observation._Observation, "write", delayed_write)
    with (
        pytest.raises(ObservationError, match="admission_deadline_expired"),
        observe_requests(**observation_args),
    ):
        _post()
    assert offline_io == []
    assert _rows(observation_args)[-1]["reservations"] == 1


def test_finish_cannot_certify_completion_after_deadline(
    observation_args, offline_io, monkeypatch
):
    clock = [100]
    observation_args["deadline_monotonic"] = 101
    monkeypatch.setattr(memory_observation, "monotonic", lambda: clock[0])
    with (
        pytest.raises(ObservationError, match="admission_deadline_expired"),
        observe_requests(**observation_args) as observation,
    ):
        _post()
        clock[0] = 101
        observation.finish()
    assert len(offline_io) == 1
    assert not any(row["event"] == "completed" for row in _rows(observation_args))
