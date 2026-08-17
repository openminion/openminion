"""Feature-local authentication for OpenMinion desktop client leases."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from http import HTTPStatus
from os import PathLike
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable, cast

from openminion.api.core.validation import parse_json_request_body
from openminion.api.responses.serialization import error_response, normalize_request_id
from openminion.api.server.observability import finalize_api_response
from openminion.base.config import ConfigManager, ConfigManagerError


MASTER_TOKEN_HEADER = "X-IPC-Token"
CLIENT_TOKEN_HEADER = "X-OpenMinion-Client-Token"
PROTOCOL_VERSION = 1
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 43_200
DEFAULT_TTL_SECONDS = MAX_TTL_SECONDS

_CLIENT_CAPABILITIES = (
    "daemon.health",
    "daemon.ready",
    "client.capabilities",
    "client.lease.renew",
    "client.lease.revoke",
)
_CLIENT_ROUTES = frozenset(
    {
        ("GET", "/v1/health"),
        ("GET", "/v1/ready"),
        ("GET", "/v1/client/capabilities"),
        ("POST", "/v1/client/leases/renew"),
        ("DELETE", "/v1/client/leases/current"),
    }
)
_CLIENT_BODY_LIMITS = {
    "/v1/client/leases": 16 * 1024,
    "/v1/client/leases/renew": 4 * 1024,
    "/v1/client/leases/current": 4 * 1024,
}
_CLIENT_RESPONSE_LIMIT = 64 * 1024
_ADMITTED_HEADERS = frozenset(
    {
        "accept", "content-type", "x-request-id", "x-ipc-token",
        "x-openminion-client-token", "host", "content-length", "connection",
        "accept-encoding", "user-agent",
    }
)


def build_config_id(
    config_path: str | Path,
    home_root: str | Path,
    data_root: str | Path,
) -> str:
    parts = (
        "openminion-config-v1",
        str(Path(config_path).expanduser().resolve(strict=False)),
        str(Path(home_root).expanduser().resolve(strict=False)),
        str(Path(data_root).expanduser().resolve(strict=False)),
    )
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def is_loopback(value: str) -> bool:
    try:
        return ipaddress.ip_address(str(value).split("%", 1)[0]).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class ClientIdentity:
    client_id: str
    config_id: str
    protocol: int
    capabilities: tuple[str, ...]


@dataclass
class _ClientLease:
    identity: ClientIdentity
    token_digest: bytes
    issued_at: datetime
    expires_at: datetime
    revoked: bool = False


class ClientAuthError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ClientAuthService:
    """Daemon-local lease owner; intentionally in-memory and feature-specific."""

    def __init__(
        self,
        *,
        master_token: str,
        config_path: str | Path,
        home_root: str | Path,
        data_root: str | Path,
        bind_host: str,
        daemon_version: str,
    ) -> None:
        self._master_token = str(master_token or "").strip()
        self.config_id = build_config_id(config_path, home_root, data_root)
        self.bind_host = str(bind_host or "").strip()
        self.daemon_version = str(daemon_version or "").strip()
        self._leases: dict[str, _ClientLease] = {}
        self._lock = threading.Lock()

    @property
    def desktop_enabled(self) -> bool:
        return bool(self._master_token)

    def authorize(
        self,
        *,
        method: str,
        path: str,
        master_tokens: Iterable[str],
        client_tokens: Iterable[str],
        peer_host: str,
    ) -> ClientIdentity | None:
        masters = tuple(master_tokens)
        clients = tuple(client_tokens)
        if len(masters) > 1 or len(clients) > 1 or (masters and clients):
            raise self._forbidden()
        if masters:
            self._require_loopback(peer_host)
            token = masters[0].strip()
            if not token or not self._master_token:
                if path == "/v1/client/leases" and not self._master_token:
                    raise ClientAuthError(
                        "desktop_ipc_token_required",
                        "Desktop admission requires a configured runtime.ipc_token.",
                    )
                raise self._forbidden()
            if not hmac.compare_digest(token, self._master_token):
                raise self._forbidden()
            return None
        if clients:
            self._require_loopback(peer_host)
            identity = self._authenticate_client(clients[0])
            if (method.upper(), path) not in _CLIENT_ROUTES:
                raise self._forbidden()
            return identity
        if path == "/v1/client/leases" and not self._master_token:
            raise ClientAuthError(
                "desktop_ipc_token_required",
                "Desktop admission requires a configured runtime.ipc_token.",
            )
        if self._master_token:
            raise self._forbidden()
        return None

    def mint(
        self,
        *,
        protocol_min: int,
        protocol_max: int,
        ttl_seconds: int,
    ) -> dict[str, object]:
        if not self.desktop_enabled:
            raise ClientAuthError(
                "desktop_ipc_token_required",
                "Desktop admission requires a configured runtime.ipc_token.",
            )
        if not protocol_min <= PROTOCOL_VERSION <= protocol_max:
            raise ClientAuthError("unsupported_protocol", "No supported protocol overlaps.")
        if not MIN_TTL_SECONDS <= ttl_seconds <= MAX_TTL_SECONDS:
            raise ClientAuthError("invalid_request", "requested_ttl_seconds is out of range.")
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl_seconds)
        identity = ClientIdentity(
            client_id=secrets.token_urlsafe(18),
            config_id=self.config_id,
            protocol=PROTOCOL_VERSION,
            capabilities=_CLIENT_CAPABILITIES,
        )
        digest = self._token_digest(token)
        with self._lock:
            self._leases[identity.client_id] = _ClientLease(
                identity=identity,
                token_digest=digest,
                issued_at=now,
                expires_at=expires_at,
            )
        return self._lease_payload(identity, token, now, expires_at)

    def renew(self, identity: ClientIdentity) -> dict[str, object]:
        with self._lock:
            lease = self._lease_for_identity(identity)
            now = datetime.now(UTC)
            lease.issued_at = now
            lease.expires_at = now + timedelta(seconds=DEFAULT_TTL_SECONDS)
            return self._lease_payload(identity, None, now, lease.expires_at)

    def revoke(self, identity: ClientIdentity) -> None:
        with self._lock:
            self._lease_for_identity(identity).revoked = True

    def capabilities(self, identity: ClientIdentity) -> dict[str, object]:
        return {
            "protocol_min": PROTOCOL_VERSION,
            "protocol_max": PROTOCOL_VERSION,
            "protocol": identity.protocol,
            "daemon_version": self.daemon_version,
            "config_id": self.config_id,
            "capabilities": list(identity.capabilities),
        }

    def _authenticate_client(self, token: str) -> ClientIdentity:
        normalized = str(token or "").strip()
        if not normalized:
            raise self._forbidden()
        digest = self._token_digest(normalized)
        with self._lock:
            lease = None
            for candidate in self._leases.values():
                if hmac.compare_digest(digest, candidate.token_digest):
                    lease = candidate
            if lease is None:
                raise self._forbidden()
            if lease.revoked or lease.expires_at <= datetime.now(UTC):
                raise self._forbidden()
            if lease.identity.config_id != self.config_id:
                raise self._forbidden()
            return lease.identity

    def _lease_for_identity(self, identity: ClientIdentity) -> _ClientLease:
        for lease in self._leases.values():
            if lease.identity.client_id == identity.client_id:
                if lease.revoked or lease.expires_at <= datetime.now(UTC):
                    break
                return lease
        raise self._forbidden()

    def _require_loopback(self, peer_host: str) -> None:
        if not is_loopback(self.bind_host) or not is_loopback(peer_host):
            raise self._forbidden()

    @staticmethod
    def _lease_payload(
        identity: ClientIdentity,
        token: str | None,
        issued_at: datetime,
        expires_at: datetime,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "client_id": identity.client_id,
            "protocol": identity.protocol,
            "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
            "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
            "config_id": identity.config_id,
            "capabilities": list(identity.capabilities),
        }
        if token is not None:
            payload["client_token"] = token
        return payload

    @staticmethod
    def _token_digest(token: str) -> bytes:
        return hashlib.sha256(token.encode("utf-8")).digest()

    @staticmethod
    def _forbidden() -> ClientAuthError:
        return ClientAuthError("forbidden", "Request is not authorized.")


class ClientAuthHTTPMixin:
    """Small HTTP adapter that keeps client policy out of the base API transport."""

    client_auth: ClientAuthService | None = None
    client_identity: ClientIdentity | None = None
    headers: Any
    rfile: Any
    client_address: tuple[str, int]
    client_request_path: str = ""
    client_response_limited: bool = False
    close_connection: bool

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        raise NotImplementedError

    def _authenticate_request(
        self,
        method: str,
        path: str,
        request_id: str | None,
    ) -> bool:
        if self.client_auth is None:
            self.client_identity = None
            return True
        masters = _header_values(self.headers, MASTER_TOKEN_HEADER)
        clients = _header_values(self.headers, CLIENT_TOKEN_HEADER)
        self.client_request_path = path
        self.client_response_limited = bool(clients) or path == "/v1/client/leases"
        try:
            if (masters or clients or path == "/v1/client/leases") and any(
                name.lower() not in _ADMITTED_HEADERS for name in self.headers.keys()
            ):
                raise ClientAuthError("forbidden", "Request is not authorized.")
            self.client_identity = self.client_auth.authorize(
                method=method,
                path=path,
                master_tokens=masters,
                client_tokens=clients,
                peer_host=_peer_host(self),
            )
            if clients and method.upper() == "GET":
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                except ValueError as exc:
                    raise ClientAuthError(
                        "invalid_request", "Invalid request body."
                    ) from exc
                transfer_encoding = self.headers.get("Transfer-Encoding")
                if transfer_encoding or content_length != 0:
                    if not transfer_encoding and 0 < content_length <= 4 * 1024:
                        self.rfile.read(content_length)
                    self.close_connection = True
                    raise ClientAuthError(
                        "invalid_request", "GET does not accept a body."
                    )
            return True
        except ClientAuthError as exc:
            status = (
                HTTPStatus.BAD_REQUEST
                if exc.code in {"desktop_ipc_token_required", "invalid_request"}
                else HTTPStatus.FORBIDDEN
            )
            resolved_status, payload = error_response(
                status,
                code=exc.code,
                message=str(exc),
                details={"path": path},
                retryable=False,
            )
            response = finalize_api_response(
                payload=payload,
                status=resolved_status,
                method=method,
                path=path,
                request_id=normalize_request_id(request_id),
                started_at=perf_counter(),
                logger=logging.getLogger("openminion.api"),
            )
            self._write_json(resolved_status, response)
            return False

    def _bounded_json_response(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
    ) -> tuple[HTTPStatus, bytes]:
        encoded = _compact_json(payload)
        if not self.client_response_limited or len(encoded) <= _CLIENT_RESPONSE_LIMIT:
            return status, encoded
        bounded_status, bounded = error_response(
            HTTPStatus.BAD_GATEWAY,
            code="response_too_large",
            message="Authenticated client response exceeded its size limit.",
            details={"limit_bytes": _CLIENT_RESPONSE_LIMIT},
            retryable=False,
        )
        meta = payload.get("meta")
        if isinstance(meta, dict):
            bounded["meta"] = {
                key: meta[key]
                for key in ("request_id", "method", "path")
                if key in meta
            }
        return bounded_status, _compact_json(bounded)

    def _read_optional_json_body(self, *, path: str) -> dict[str, Any]:
        body_limit = _CLIENT_BODY_LIMITS.get(path)
        if body_limit is not None and self.headers.get("Transfer-Encoding"):
            raise ValueError("Chunked client request bodies are not supported.")
        content_length_raw = self.headers.get("Content-Length", "0")
        try:
            content_length = int(content_length_raw)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length header.") from exc
        if content_length < 0:
            raise ValueError("Invalid Content-Length header.")
        if body_limit is not None and content_length > body_limit:
            raise ValueError(f"Request body exceeds the {body_limit}-byte limit.")
        if content_length == 0:
            return {}
        if body_limit is not None:
            content_type = str(self.headers.get("Content-Type", "") or "").lower()
            if content_type.split(";", 1)[0].strip() != "application/json":
                raise ValueError("Content-Type must be application/json.")
            compact_type = content_type.replace(" ", "")
            if ";" in content_type and "charset=utf-8" not in compact_type:
                raise ValueError("Client JSON must use UTF-8.")
        try:
            raw_body = self.rfile.read(content_length).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Request body must be valid UTF-8.") from exc
        return cast(
            dict[str, Any],
            parse_json_request_body(
                content_length_raw=content_length_raw,
                raw_body=raw_body,
            ),
        )


def _header_values(headers: object, name: str) -> tuple[str, ...]:
    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        return tuple(str(value) for value in (get_all(name) or ()))
    get = getattr(headers, "get", None)
    value = get(name) if callable(get) else None
    return (str(value),) if value is not None else ()


def _peer_host(handler: object) -> str:
    client_address = getattr(handler, "client_address", None)
    if isinstance(client_address, tuple) and client_address:
        return str(client_address[0])
    return "127.0.0.1"


def _compact_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def build_client_auth_service(
    *,
    bootstrap: object,
    config_path: str | None,
    home_root: str | PathLike[str] | None,
    data_root: str | PathLike[str] | None,
    bind_host: str,
) -> ClientAuthService | None:
    runtime = getattr(bootstrap, "runtime", None)
    if runtime is not None:
        config = runtime.config
        resolved_config = runtime.config_path
        resolved_home = runtime.home_root
        resolved_data = runtime.data_root
    else:
        try:
            manager = ConfigManager.load(
                config_path,
                home_root=_resolved_optional_path(home_root),
                data_root=_resolved_optional_path(data_root),
            )
        except (ConfigManagerError, OSError, ValueError):
            return None
        config = manager.base_config
        resolved_config = manager.config_path
        resolved_home = manager.home_root
        resolved_data = manager.data_root
    if resolved_config is None:
        return None
    return ClientAuthService(
        master_token=str(config.runtime.ipc_token or ""),
        config_path=resolved_config,
        home_root=resolved_home,
        data_root=resolved_data,
        bind_host=bind_host,
        daemon_version=_package_version(),
    )


def _resolved_optional_path(value: str | PathLike[str] | None) -> Path | None:
    if value is None or not str(value).strip():
        return None
    return Path(value).expanduser().resolve(strict=False)


def _package_version() -> str:
    try:
        return version("openminion")
    except PackageNotFoundError:
        return "0.0.0"


__all__ = [
    "CLIENT_TOKEN_HEADER",
    "ClientAuthError",
    "ClientAuthHTTPMixin",
    "ClientAuthService",
    "ClientIdentity",
    "DEFAULT_TTL_SECONDS",
    "MASTER_TOKEN_HEADER",
    "MAX_TTL_SECONDS",
    "MIN_TTL_SECONDS",
    "PROTOCOL_VERSION",
    "build_config_id",
    "build_client_auth_service",
    "is_loopback",
]
