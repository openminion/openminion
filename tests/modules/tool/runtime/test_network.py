from __future__ import annotations

import socket
import threading
from types import SimpleNamespace

import pytest

import openminion.modules.tool.runtime.public_https as public_https

from openminion.modules.tool.runtime.public_https import (
    PublicHttpsError,
    _PinnedHttpsConnection,
    request_public_https,
)


def _answer(address: str) -> tuple:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    sockaddr = (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
    return family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr


class _Response:
    def __init__(
        self,
        *,
        status: int = 200,
        headers: list[tuple[str, str]] | None = None,
        body: bytes = b"{}",
    ) -> None:
        self.status = status
        self._headers = headers or [("Content-Type", "application/json")]
        self._body = body

    def getheaders(self) -> list[tuple[str, str]]:
        return self._headers

    def read(self, size: int) -> bytes:
        return self._body[:size]


class _Connection:
    def __init__(
        self,
        response: _Response | None = None,
        *,
        connect_error: Exception | None = None,
    ) -> None:
        self.response = response or _Response()
        self.connect_error = connect_error
        self.connected = False
        self.closed = False
        self.requests: list[tuple[str, str, bytes | None, dict[str, str]]] = []

    def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    def request(
        self,
        method: str,
        target: str,
        *,
        body: bytes | None,
        headers: dict[str, str],
    ) -> None:
        assert self.connected
        self.requests.append((method, target, body, headers))

    def getresponse(self) -> _Response:
        return self.response

    def close(self) -> None:
        self.closed = True


def _request(
    *,
    answers: list[tuple] | None = None,
    connection: _Connection | None = None,
    **kwargs,
):
    calls: list[tuple[str, int]] = []
    selected: list[tuple] = []
    active_connection = connection or _Connection()

    def resolver(host: str, port: int, *, type: int):
        assert type == socket.SOCK_STREAM
        calls.append((host, port))
        return answers or [_answer("93.184.216.34")]

    def connection_factory(host: str, port: int, address: tuple, timeout: float):
        assert host == "example.com"
        assert port == 443
        assert 0 < timeout <= 15.0
        selected.append(address)
        return active_connection

    result = request_public_https(
        "https://example.com/rpc?fields=abi",
        resolver=resolver,
        connection_factory=connection_factory,
        **kwargs,
    )
    return result, active_connection, calls, selected


def test_public_https_get_uses_one_resolution_and_first_address() -> None:
    answers = [_answer("93.184.216.34"), _answer("93.184.216.35")]

    result, connection, calls, selected = _request(answers=answers)

    assert result.status == 200
    assert result.headers == {"content-type": "application/json"}
    assert result.body == b"{}"
    assert calls == [("example.com", 443)]
    assert selected == [answers[0]]
    assert connection.requests == [("GET", "/rpc?fields=abi", None, {})]
    assert connection.closed is True


def test_public_https_post_preserves_fixed_internal_headers() -> None:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "OpenMinion/test",
    }

    _, connection, _, _ = _request(method="POST", body=b"{}", headers=headers)

    assert connection.requests == [("POST", "/rpc?fields=abi", b"{}", headers)]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "file:///tmp/example",
        "https://user:password@example.com",
        "https://example.com/path#fragment",
        "https://example.com:99999",
    ],
)
def test_public_https_rejects_invalid_endpoint_shape(url: str) -> None:
    with pytest.raises(PublicHttpsError) as excinfo:
        request_public_https(url)

    assert excinfo.value.code == "INVALID_URL"


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.1.1",
        "224.0.0.1",
        "240.0.0.1",
        "0.0.0.0",
        "::1",
        "::ffff:127.0.0.1",
        "::ffff:93.184.216.34",
    ],
)
def test_public_https_rejects_forbidden_and_mapped_destinations(address: str) -> None:
    with pytest.raises(PublicHttpsError) as excinfo:
        _request(answers=[_answer(address)])

    assert excinfo.value.code == "FORBIDDEN_DESTINATION"


def test_public_https_rejects_public_private_dns_mix() -> None:
    with pytest.raises(PublicHttpsError) as excinfo:
        _request(answers=[_answer("93.184.216.34"), _answer("127.0.0.1")])

    assert excinfo.value.code == "FORBIDDEN_DESTINATION"


def test_public_https_accepts_public_dual_stack_and_pins_first_address() -> None:
    answers = [_answer("93.184.216.34"), _answer("2606:2800:220:1::")]

    result, _, _, selected = _request(answers=answers)

    assert result.status == 200
    assert selected == [answers[0]]


def test_public_https_fails_closed_on_resolution_error() -> None:
    def resolver(*args, **kwargs):
        del args, kwargs
        raise socket.gaierror("synthetic")

    with pytest.raises(PublicHttpsError) as excinfo:
        request_public_https("https://example.com", resolver=resolver)

    assert excinfo.value.code == "RESOLUTION_FAILED"


def test_public_https_total_timeout_includes_resolution() -> None:
    release = threading.Event()

    def resolver(*args, **kwargs):
        del args, kwargs
        release.wait(1)
        return [_answer("93.184.216.34")]

    try:
        with pytest.raises(PublicHttpsError) as excinfo:
            request_public_https(
                "https://example.com",
                timeout=0.01,
                resolver=resolver,
            )
    finally:
        release.set()

    assert excinfo.value.code == "TIMEOUT"


def test_public_https_contains_timed_out_resolution_work() -> None:
    release = threading.Event()
    calls = 0

    def resolver(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        release.wait(1)
        return [_answer("93.184.216.34")]

    try:
        for _ in range(2):
            with pytest.raises(PublicHttpsError, match="TIMEOUT"):
                request_public_https(
                    "https://example.com",
                    timeout=0.01,
                    resolver=resolver,
                )
    finally:
        release.set()

    assert calls == 1


def test_public_https_returns_redirect_without_following_it() -> None:
    connection = _Connection(
        _Response(status=302, headers=[("Location", "https://other.example/path")])
    )

    result, _, calls, _ = _request(connection=connection)

    assert result.status == 302
    assert result.headers == {"location": "https://other.example/path"}
    assert calls == [("example.com", 443)]
    assert len(connection.requests) == 1


def test_public_https_ignores_proxy_environment(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "https://127.0.0.1:8443")

    _, connection, calls, _ = _request()

    assert calls == [("example.com", 443)]
    assert len(connection.requests) == 1


def test_public_https_timeout_does_not_retry_or_fall_back() -> None:
    answers = [_answer("93.184.216.34"), _answer("93.184.216.35")]
    connection = _Connection(connect_error=TimeoutError())

    with pytest.raises(PublicHttpsError) as excinfo:
        _request(answers=answers, connection=connection)

    assert excinfo.value.code == "TIMEOUT"
    assert connection.requests == []
    assert connection.closed is True


def test_public_https_enforces_response_body_bound() -> None:
    connection = _Connection(_Response(body=b"12345"))

    with pytest.raises(PublicHttpsError) as excinfo:
        _request(connection=connection, max_body_bytes=4)

    assert excinfo.value.code == "RESPONSE_TOO_LARGE"
    assert connection.closed is True


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _SlowResponse(_Response):
    def __init__(self, clock: _Clock) -> None:
        super().__init__(body=b"")
        self.clock = clock

    def read1(self, size: int) -> bytes:
        del size
        self.clock.now += 6.0
        return b"x"


def test_public_https_enforces_one_deadline_across_response_reads(
    monkeypatch,
) -> None:
    clock = _Clock()
    monkeypatch.setattr(public_https.time, "monotonic", clock)
    connection = _Connection(_SlowResponse(clock))

    with pytest.raises(PublicHttpsError) as excinfo:
        _request(connection=connection, timeout=10.0)

    assert excinfo.value.code == "TIMEOUT"
    assert connection.closed is True


def test_public_https_rejects_invalid_method_and_bounds_without_network() -> None:
    with pytest.raises(PublicHttpsError, match="METHOD_NOT_ALLOWED"):
        request_public_https("https://example.com", method="PUT")
    with pytest.raises(PublicHttpsError, match="INVALID_BOUND"):
        request_public_https("https://example.com", timeout=0)
    with pytest.raises(PublicHttpsError, match="INVALID_BOUND"):
        request_public_https("https://example.com", max_body_bytes=0)


class _RawSocket:
    def __init__(self, peer: str) -> None:
        self.peer = peer
        self.closed = False
        self.timeout = 0.0

    def getpeername(self) -> tuple[str, int]:
        return self.peer, 443

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def close(self) -> None:
        self.closed = True


class _TlsContext:
    def __init__(self) -> None:
        self.server_hostname = ""

    def wrap_socket(self, raw_socket, *, server_hostname: str):
        self.server_hostname = server_hostname
        return SimpleNamespace(raw_socket=raw_socket)


def test_pinned_connection_verifies_peer_then_preserves_tls_hostname() -> None:
    raw_socket = _RawSocket("93.184.216.34")
    context = _TlsContext()
    connection = _PinnedHttpsConnection(
        "example.com",
        443,
        _answer("93.184.216.34"),
        15.0,
        connector=lambda *args: raw_socket,
    )
    connection._context = context

    connection.connect()

    assert context.server_hostname == "example.com"
    assert connection.sock.raw_socket is raw_socket


def test_pinned_connection_reduces_tls_timeout_after_connect(monkeypatch) -> None:
    clock = _Clock()
    raw_socket = _RawSocket("93.184.216.34")
    context = _TlsContext()

    def connect(*_args):
        clock.now += 6.0
        return raw_socket

    monkeypatch.setattr(public_https.time, "monotonic", clock)
    connection = _PinnedHttpsConnection(
        "example.com",
        443,
        _answer("93.184.216.34"),
        10.0,
        connector=connect,
    )
    connection._context = context

    connection.connect()

    assert raw_socket.timeout == 4.0


@pytest.mark.parametrize("peer", ["127.0.0.1", "93.184.216.35"])
def test_pinned_connection_rejects_changed_peer_before_tls(peer: str) -> None:
    raw_socket = _RawSocket(peer)
    context = _TlsContext()
    connection = _PinnedHttpsConnection(
        "example.com",
        443,
        _answer("93.184.216.34"),
        15.0,
        connector=lambda *args: raw_socket,
    )
    connection._context = context

    with pytest.raises(PublicHttpsError) as excinfo:
        connection.connect()

    assert excinfo.value.code == "PEER_MISMATCH"
    assert context.server_hostname == ""
    assert raw_socket.closed is True
