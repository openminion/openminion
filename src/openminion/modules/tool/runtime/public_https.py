from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import http.client
import ipaddress
import socket
import ssl
from typing import Any
import urllib.parse

from .network import is_forbidden_ip

DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 15.0

_AddressInfo = tuple[int, int, int, str, tuple[Any, ...]]
_Resolver = Callable[..., Sequence[_AddressInfo]]
_Connector = Callable[[int, int, int, tuple[Any, ...], float], Any]
_ConnectionFactory = Callable[
    [str, int, _AddressInfo, float], http.client.HTTPSConnection
]


@dataclass(frozen=True, slots=True)
class PublicHttpsResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class PublicHttpsError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _connect_socket(
    family: int,
    socktype: int,
    proto: int,
    sockaddr: tuple[Any, ...],
    timeout: float,
) -> socket.socket:
    connection = socket.socket(family, socktype, proto)
    connection.settimeout(timeout)
    try:
        connection.connect(sockaddr)
    except OSError:
        connection.close()
        raise
    return connection


class _PinnedHttpsConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        port: int,
        address: _AddressInfo,
        timeout: float,
        *,
        connector: _Connector = _connect_socket,
        context: ssl.SSLContext | None = None,
    ) -> None:
        super().__init__(
            host=host,
            port=port,
            timeout=timeout,
            context=context or ssl.create_default_context(),
        )
        self._address = address
        self._connector = connector

    def connect(self) -> None:
        family, socktype, proto, _, sockaddr = self._address
        raw_socket = self._connector(
            family,
            socktype,
            proto,
            sockaddr,
            float(self.timeout),
        )
        expected_peer = ipaddress.ip_address(str(sockaddr[0]))
        try:
            observed_peer = ipaddress.ip_address(str(raw_socket.getpeername()[0]))
            if observed_peer != expected_peer or is_forbidden_ip(observed_peer):
                raise PublicHttpsError("PEER_MISMATCH")
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except (OSError, ValueError, PublicHttpsError):
            raw_socket.close()
            raise


def _resolve_public_address(
    host: str,
    port: int,
    *,
    resolver: _Resolver,
) -> _AddressInfo:
    try:
        answers = list(resolver(host, port, type=socket.SOCK_STREAM))
    except OSError as exc:
        raise PublicHttpsError("RESOLUTION_FAILED") from exc
    if not answers:
        raise PublicHttpsError("RESOLUTION_FAILED")

    families: set[int] = set()
    for answer in answers:
        try:
            family, _, _, _, sockaddr = answer
            candidate = ipaddress.ip_address(str(sockaddr[0]))
        except (IndexError, TypeError, ValueError) as exc:
            raise PublicHttpsError("RESOLUTION_FAILED") from exc
        if (
            isinstance(candidate, ipaddress.IPv6Address)
            and candidate.ipv4_mapped is not None
        ) or is_forbidden_ip(candidate):
            raise PublicHttpsError("FORBIDDEN_DESTINATION")
        families.add(family)
    if len(families) != 1:
        raise PublicHttpsError("MIXED_DESTINATIONS")
    return answers[0]


def _default_connection_factory(
    host: str,
    port: int,
    address: _AddressInfo,
    timeout: float,
) -> http.client.HTTPSConnection:
    return _PinnedHttpsConnection(host, port, address, timeout)


def request_public_https(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    resolver: _Resolver = socket.getaddrinfo,
    connection_factory: _ConnectionFactory = _default_connection_factory,
) -> PublicHttpsResponse:
    normalized_method = str(method).strip().upper()
    if normalized_method not in {"GET", "POST"}:
        raise PublicHttpsError("METHOD_NOT_ALLOWED")
    if not isinstance(body, (bytes, type(None))):
        raise PublicHttpsError("INVALID_BODY")
    if timeout <= 0 or max_body_bytes < 1:
        raise PublicHttpsError("INVALID_BOUND")

    parsed = urllib.parse.urlsplit(str(url).strip())
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise PublicHttpsError("INVALID_URL") from exc
    host = str(parsed.hostname or "").strip()
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.fragment)
    ):
        raise PublicHttpsError("INVALID_URL")

    address = _resolve_public_address(host, port, resolver=resolver)
    connection = connection_factory(host, port, address, float(timeout))
    target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    try:
        connection.connect()
        connection.request(
            normalized_method,
            target,
            body=body,
            headers=dict(headers or {}),
        )
        response = connection.getresponse()
        response_body = response.read(max_body_bytes + 1)
        if len(response_body) > max_body_bytes:
            raise PublicHttpsError("RESPONSE_TOO_LARGE")
        return PublicHttpsResponse(
            status=int(response.status),
            headers={
                str(key).lower(): str(value) for key, value in response.getheaders()
            },
            body=response_body,
        )
    except PublicHttpsError:
        raise
    except (TimeoutError, socket.timeout) as exc:
        raise PublicHttpsError("TIMEOUT") from exc
    except ssl.SSLError as exc:
        raise PublicHttpsError("TLS_FAILED") from exc
    except (http.client.HTTPException, OSError) as exc:
        raise PublicHttpsError("NETWORK_FAILED") from exc
    finally:
        connection.close()


__all__ = [
    "DEFAULT_MAX_BODY_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "PublicHttpsError",
    "PublicHttpsResponse",
    "request_public_https",
]
