"""Bounded loopback media transport for authenticated desktop clients."""

from __future__ import annotations

import json
import logging
import re
import secrets
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from time import monotonic
from typing import Any, BinaryIO, Callable
from unicodedata import category
from urllib.parse import unquote, unquote_to_bytes

from openminion.api.responses.serialization import error_response, normalize_request_id
from openminion.api.server.observability import finalize_api_response
from openminion.modules.artifact.config import from_base_config
from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.artifact.errors import ArtifactCtlError

from .client_auth import ClientAuthService, ClientIdentity, _header_values


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PENDING_COUNT = 8
MAX_PENDING_BYTES = 40 * 1024 * 1024
MAX_TURN_COUNT = 4
MAX_TURN_BYTES = 20 * 1024 * 1024
MAX_ACTIVE_PER_CLIENT = 16
MAX_ACTIVE_GLOBAL = 64
MAX_TERMINAL_RECORDS = 512
UPLOAD_DEADLINE_SECONDS = 15.0
_UPLOAD_CHUNK_BYTES = 64 * 1024
_PENDING_TTL = timedelta(minutes=15)
_TERMINAL_TTL = timedelta(minutes=5)
_MEDIA_ID = re.compile(r"[0-9a-f]{48}")
_MEDIA_COLLECTION = re.compile(r"/v1/client/sessions/([^/]+)/media")
_MEDIA_ITEM = re.compile(r"/v1/client/sessions/([^/]+)/media/([^/]+)")
_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
_UNRESERVED_NAME = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~"
)
_FORMATS = {
    "image/png": ("image", True),
    "image/jpeg": ("image", True),
    "image/webp": ("image", True),
    "application/pdf": ("file", False),
    "text/plain": ("file", False),
    "application/json": ("file", False),
}


class ClientMediaError(RuntimeError):
    def __init__(
        self,
        status: HTTPStatus,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        retry_after_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.retryable = retryable
        self.retry_after_ms = retry_after_ms


@dataclass(frozen=True)
class _UploadReservation:
    token: str
    client_id: str
    session_id: str
    size_bytes: int


@dataclass
class _MediaRecord:
    client_id: str
    session_id: str
    media_id: str
    artifact_ref: str
    name: str
    mime_type: str
    size_bytes: int
    kind: str
    turn_compatible: bool
    created_at: datetime
    expires_at: datetime
    state: str = "unbound"
    bound_trace_id: str | None = None
    terminal_at: datetime | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "media_id": self.media_id,
            "session_id": self.session_id,
            "name": self.name,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "kind": self.kind,
            "turn_compatible": self.turn_compatible,
            "expires_at": _utc_text(self.expires_at),
        }


class ClientMediaCoordinator:
    """Lease-local opaque media mappings backed by the existing artifact CAS."""

    def __init__(
        self,
        *,
        client_auth: ClientAuthService,
        runtime: Any,
        artifactctl: Any,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        from threading import RLock

        self._client_auth = client_auth
        self._runtime = runtime
        self._artifactctl = artifactctl
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = RLock()
        self._records: dict[str, _MediaRecord] = {}
        self._reservations: dict[str, _UploadReservation] = {}
        self._cancelled_reservations: dict[str, str] = {}
        self._closing_sessions: dict[str, int] = {}
        self._closed = False

    def begin_upload(
        self,
        identity: ClientIdentity,
        session_id: str,
        size_bytes: int,
    ) -> _UploadReservation:
        if size_bytes <= 0:
            raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")
        if size_bytes > MAX_UPLOAD_BYTES:
            raise _media_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "media_too_large")
        with self._lock:
            self._purge_locked()
            self._require_open_identity_locked(identity)
            self._require_active_session_locked(session_id)
            if self._closing_sessions.get(session_id, 0):
                raise _media_error(HTTPStatus.CONFLICT, "session_closed")
            if identity.client_id in self._reservations:
                raise _backpressure()
            pending = self._pending_locked(identity.client_id, session_id)
            reserved = sum(
                item.size_bytes
                for item in self._reservations.values()
                if item.client_id == identity.client_id
                and item.session_id == session_id
            )
            if len(pending) >= MAX_PENDING_COUNT:
                raise _backpressure()
            if (
                sum(item.size_bytes for item in pending) + reserved + size_bytes
                > MAX_PENDING_BYTES
            ):
                raise _backpressure()
            reservation = _UploadReservation(
                token=secrets.token_urlsafe(24),
                client_id=identity.client_id,
                session_id=session_id,
                size_bytes=size_bytes,
            )
            self._reservations[identity.client_id] = reservation
            return reservation

    def commit_upload(
        self,
        reservation: _UploadReservation,
        *,
        data: bytes,
        name: str,
        mime_type: str,
        request_id: str,
    ) -> _MediaRecord:
        if len(data) != reservation.size_bytes:
            raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")
        kind, compatible = _validate_media(mime_type, data)
        try:
            artifact = self._artifactctl.ingest_bytes(
                data,
                mime=mime_type,
                original_name=name,
                session_id=reservation.session_id,
                trace_id=request_id,
            )
        except ClientMediaError:
            raise
        except Exception as exc:
            raise _media_error(
                HTTPStatus.INTERNAL_SERVER_ERROR, "media_storage_failed"
            ) from exc
        with self._lock:
            current = self._reservations.get(reservation.client_id)
            if current != reservation:
                outcome = self._cancelled_reservations.pop(reservation.token, None)
                if self._closed or outcome == "forbidden":
                    raise _media_error(HTTPStatus.FORBIDDEN, "forbidden")
                if outcome == "session_closed" or self._closing_sessions.get(
                    reservation.session_id, 0
                ):
                    raise _media_error(HTTPStatus.CONFLICT, "session_closed")
                raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")
            if not self._client_auth.is_client_active(reservation.client_id):
                self._reservations.pop(reservation.client_id, None)
                raise _media_error(HTTPStatus.FORBIDDEN, "forbidden")
            self._require_active_session_locked(reservation.session_id)
            self._reservations.pop(reservation.client_id, None)
            now = self._now()
            record = _MediaRecord(
                client_id=reservation.client_id,
                session_id=reservation.session_id,
                media_id=self._new_media_id_locked(),
                artifact_ref=str(artifact.ref),
                name=name,
                mime_type=mime_type,
                size_bytes=reservation.size_bytes,
                kind=kind,
                turn_compatible=compatible,
                created_at=now,
                expires_at=now + _PENDING_TTL,
            )
            self._records[record.media_id] = record
            return record

    def abort_upload(self, reservation: _UploadReservation) -> None:
        with self._lock:
            if self._reservations.get(reservation.client_id) == reservation:
                self._reservations.pop(reservation.client_id, None)
            self._cancelled_reservations.pop(reservation.token, None)

    def open_media(
        self,
        identity: ClientIdentity,
        session_id: str,
        media_id: str,
    ) -> tuple[_MediaRecord, BinaryIO]:
        with self._lock:
            record = self._owned_record_locked(identity, session_id, media_id)
            try:
                stream = self._artifactctl.open(record.artifact_ref)
            except ArtifactCtlError as exc:
                if exc.code == "NOT_FOUND":
                    raise _media_error(HTTPStatus.NOT_FOUND, "media_not_found") from exc
                raise _media_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR, "media_storage_failed"
                ) from exc
            except Exception as exc:
                raise _media_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR, "media_storage_failed"
                ) from exc
            return record, stream

    def release(
        self,
        identity: ClientIdentity,
        session_id: str,
        media_id: str,
    ) -> None:
        with self._lock:
            self._owned_record_locked(identity, session_id, media_id)
            self._records.pop(media_id, None)

    def resolve_for_turn(
        self,
        identity: ClientIdentity,
        session_id: str,
        trace_id: str,
        media_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        if (
            len(media_ids) > MAX_TURN_COUNT
            or len(set(media_ids)) != len(media_ids)
            or any(_MEDIA_ID.fullmatch(media_id) is None for media_id in media_ids)
        ):
            raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")
        with self._lock:
            self._purge_locked()
            self._require_open_identity_locked(identity)
            self._require_active_session_locked(session_id)
            if self._closing_sessions.get(session_id, 0):
                raise _media_error(HTTPStatus.CONFLICT, "session_closed")
            records = [
                self._owned_record_locked(identity, session_id, media_id)
                for media_id in media_ids
            ]
            if any(not record.turn_compatible for record in records):
                raise _media_error(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported_media_type"
                )
            if sum(record.size_bytes for record in records) > MAX_TURN_BYTES:
                raise _media_error(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "media_too_large"
                )
            for record in records:
                if record.state != "unbound" and record.bound_trace_id != trace_id:
                    raise _media_error(HTTPStatus.CONFLICT, "media_already_attached")
            new_records = [record for record in records if record.state == "unbound"]
            active = [
                record
                for record in self._records.values()
                if record.state == "bound_active"
            ]
            client_active = sum(
                record.client_id == identity.client_id for record in active
            )
            if client_active + len(new_records) > MAX_ACTIVE_PER_CLIENT:
                raise _backpressure()
            if len(active) + len(new_records) > MAX_ACTIVE_GLOBAL:
                raise _backpressure()
            for record in new_records:
                record.state = "bound_active"
                record.bound_trace_id = trace_id
            return tuple(record.artifact_ref for record in records)

    def unbind_before_start(
        self,
        identity: ClientIdentity,
        session_id: str,
        trace_id: str,
    ) -> None:
        with self._lock:
            for record in self._records.values():
                if (
                    record.client_id == identity.client_id
                    and record.session_id == session_id
                    and record.bound_trace_id == trace_id
                    and record.state == "bound_active"
                ):
                    record.state = "unbound"
                    record.bound_trace_id = None

    def complete_trace(
        self,
        identity: ClientIdentity,
        session_id: str,
        trace_id: str,
    ) -> None:
        with self._lock:
            now = self._now()
            for record in self._records.values():
                if (
                    record.client_id == identity.client_id
                    and record.session_id == session_id
                    and record.bound_trace_id == trace_id
                    and record.state == "bound_active"
                ):
                    record.state = "bound_terminal"
                    record.terminal_at = now
            self._purge_locked()

    def revoke_client(self, identity: ClientIdentity) -> None:
        with self._lock:
            self._client_auth.revoke(identity)
            if reservation := self._reservations.pop(identity.client_id, None):
                self._cancelled_reservations[reservation.token] = "forbidden"
            self._records = {
                key: value
                for key, value in self._records.items()
                if value.client_id != identity.client_id
            }

    def cancel_session(self, session_id: str, _reason: str) -> Callable[[], None]:
        with self._lock:
            self._closing_sessions[session_id] = (
                self._closing_sessions.get(session_id, 0) + 1
            )
            for reservation in self._reservations.values():
                if reservation.session_id == session_id:
                    self._cancelled_reservations[reservation.token] = "session_closed"
            self._reservations = {
                key: value
                for key, value in self._reservations.items()
                if value.session_id != session_id
            }
            self._records = {
                key: value
                for key, value in self._records.items()
                if value.session_id != session_id
            }
        return lambda: self._finish_session_close(session_id)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._records.clear()
            self._reservations.clear()
            self._cancelled_reservations.clear()
        self._artifactctl.close()

    def _finish_session_close(self, session_id: str) -> None:
        with self._lock:
            count = self._closing_sessions.get(session_id, 0)
            if count <= 1:
                self._closing_sessions.pop(session_id, None)
            else:
                self._closing_sessions[session_id] = count - 1

    def _owned_record_locked(
        self,
        identity: ClientIdentity,
        session_id: str,
        media_id: str,
    ) -> _MediaRecord:
        self._require_open_identity_locked(identity)
        self._require_active_session_locked(session_id)
        if self._closing_sessions.get(session_id, 0):
            raise _media_error(HTTPStatus.CONFLICT, "session_closed")
        record = self._records.get(media_id)
        if (
            record is None
            or record.client_id != identity.client_id
            or record.session_id != session_id
        ):
            raise _media_error(HTTPStatus.NOT_FOUND, "media_not_found")
        now = self._now()
        expired = record.state == "unbound" and record.expires_at <= now
        expired = expired or (
            record.state == "bound_terminal"
            and record.terminal_at is not None
            and record.terminal_at + _TERMINAL_TTL <= now
        )
        if expired:
            self._records.pop(media_id, None)
            raise _media_error(HTTPStatus.GONE, "media_expired")
        self._purge_locked()
        return record

    def _require_open_identity_locked(self, identity: ClientIdentity) -> None:
        if self._closed or not self._client_auth.is_active(identity):
            raise _media_error(HTTPStatus.FORBIDDEN, "forbidden")

    def _require_active_session_locked(self, session_id: str) -> None:
        record = self._runtime.sessions.get_session(session_id)
        status = getattr(record, "status", None)
        if isinstance(record, dict):
            status = record.get("status")
        if record is None:
            raise _media_error(HTTPStatus.NOT_FOUND, "media_not_found")
        if status != "active":
            raise _media_error(HTTPStatus.CONFLICT, "session_closed")

    def _pending_locked(self, client_id: str, session_id: str) -> list[_MediaRecord]:
        return [
            record
            for record in self._records.values()
            if record.client_id == client_id
            and record.session_id == session_id
            and record.state == "unbound"
        ]

    def _new_media_id_locked(self) -> str:
        while (media_id := secrets.token_hex(24)) in self._records:
            continue
        return media_id

    def _purge_locked(self) -> None:
        now = self._now()
        self._records = {
            key: record
            for key, record in self._records.items()
            if not (
                record.state == "unbound"
                and record.expires_at <= now
                or record.state == "bound_terminal"
                and record.terminal_at is not None
                and record.terminal_at + _TERMINAL_TTL <= now
            )
        }
        terminal = sorted(
            (
                record
                for record in self._records.values()
                if record.state == "bound_terminal"
            ),
            key=lambda record: record.terminal_at or record.created_at,
        )
        for record in terminal[:-MAX_TERMINAL_RECORDS]:
            self._records.pop(record.media_id, None)


def install_media(
    handler_cls: type[Any], runtime: Any
) -> ClientMediaCoordinator | None:
    auth = getattr(handler_cls, "client_auth", None)
    if auth is None or runtime is None:
        handler_cls.client_media = None
        return None
    manager = runtime.config_manager
    artifactctl = ArtifactCtl(
        from_base_config(
            base_config=runtime.config,
            home_root=manager.home_root,
            data_root=manager.data_root,
        )
    )
    coordinator = ClientMediaCoordinator(
        client_auth=auth,
        runtime=runtime,
        artifactctl=artifactctl,
    )
    handler_cls.client_media = coordinator
    return coordinator


def close_media(coordinator: ClientMediaCoordinator | None) -> None:
    if coordinator is not None:
        coordinator.close()


def handle_media_http(
    handler: Any,
    *,
    method: str,
    path: str,
    query: str,
    request_id: str | None,
) -> bool:
    operation, session_id, media_id = _media_operation(method, path)
    if operation is None:
        return False
    identity = getattr(handler, "client_identity", None)
    coordinator = getattr(handler, "client_media", None)
    if identity is None or coordinator is None:
        _write_error(
            handler,
            method,
            path,
            request_id,
            _media_error(HTTPStatus.FORBIDDEN, "forbidden"),
        )
        return True
    if query:
        handler.close_connection = True
        _write_error(
            handler,
            method,
            path,
            request_id,
            _media_error(HTTPStatus.BAD_REQUEST, "invalid_request"),
        )
        return True
    try:
        _validate_request_headers(handler, operation)
        if operation == "upload":
            _handle_upload(handler, coordinator, identity, session_id, request_id)
        elif operation == "read":
            _handle_read(
                handler, coordinator, identity, session_id, media_id, request_id
            )
        else:
            _require_empty_body(handler)
            if _MEDIA_ID.fullmatch(media_id) is None:
                raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")
            coordinator.release(identity, session_id, media_id)
            _write_payload(
                handler,
                HTTPStatus.OK,
                {"ok": True, "released": True, "media_id": media_id},
                method=method,
                path=path,
                request_id=request_id,
                session_id=session_id,
            )
    except ClientMediaError as exc:
        _write_error(handler, method, path, request_id, exc, session_id=session_id)
    return True


def _media_operation(method: str, path: str) -> tuple[str | None, str, str]:
    if match := _MEDIA_COLLECTION.fullmatch(path):
        return ("upload" if method == "POST" else None), unquote(match.group(1)), ""
    if match := _MEDIA_ITEM.fullmatch(path):
        operation = {"GET": "read", "DELETE": "release"}.get(method)
        return operation, unquote(match.group(1)), unquote(match.group(2))
    return None, "", ""


def _handle_upload(
    handler: Any,
    coordinator: ClientMediaCoordinator,
    identity: ClientIdentity,
    session_id: str,
    request_id: str | None,
) -> None:
    handler.close_connection = True
    if request_id is None or len(_header_values(handler.headers, "X-Request-ID")) != 1:
        raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")
    if handler.headers.get("Transfer-Encoding"):
        raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")
    lengths = _header_values(handler.headers, "Content-Length")
    if not lengths:
        raise _media_error(HTTPStatus.LENGTH_REQUIRED, "length_required")
    if len(lengths) != 1 or not lengths[0].isdigit():
        raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")
    size_bytes = int(lengths[0])
    if size_bytes <= 0:
        raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")
    if size_bytes > MAX_UPLOAD_BYTES:
        raise _media_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "media_too_large")
    content_types = _header_values(handler.headers, "Content-Type")
    names = _header_values(handler.headers, "X-OpenMinion-Media-Name")
    if len(content_types) != 1 or len(names) != 1:
        raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")
    mime_type = content_types[0].strip().lower()
    if mime_type not in _FORMATS:
        raise _media_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported_media_type")
    name = _decode_name(names[0])
    reservation = coordinator.begin_upload(identity, session_id, size_bytes)
    try:
        data = _read_upload_body(handler, size_bytes)
        record = coordinator.commit_upload(
            reservation,
            data=data,
            name=name,
            mime_type=mime_type,
            request_id=normalize_request_id(request_id),
        )
        _write_payload(
            handler,
            HTTPStatus.CREATED,
            {"ok": True, "media": record.payload()},
            method="POST",
            path=handler.client_request_path,
            request_id=request_id,
            session_id=session_id,
        )
    finally:
        coordinator.abort_upload(reservation)


def _read_upload_body(handler: Any, size_bytes: int) -> bytes:
    deadline = monotonic() + UPLOAD_DEADLINE_SECONDS
    connection = handler.connection
    previous_timeout = connection.gettimeout()
    chunks: list[bytes] = []
    remaining = size_bytes
    try:
        while remaining:
            timeout = deadline - monotonic()
            if timeout <= 0:
                raise _media_error(HTTPStatus.REQUEST_TIMEOUT, "media_upload_timeout")
            connection.settimeout(timeout)
            try:
                chunk = handler.rfile.read(min(_UPLOAD_CHUNK_BYTES, remaining))
            except (TimeoutError, socket.timeout) as exc:
                raise _media_error(
                    HTTPStatus.REQUEST_TIMEOUT, "media_upload_timeout"
                ) from exc
            if not chunk:
                raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        connection.settimeout(previous_timeout)


def _handle_read(
    handler: Any,
    coordinator: ClientMediaCoordinator,
    identity: ClientIdentity,
    session_id: str,
    media_id: str,
    request_id: str | None,
) -> None:
    if _MEDIA_ID.fullmatch(media_id) is None:
        raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")
    record, stream = coordinator.open_media(identity, session_id, media_id)
    with stream:
        handler.send_response(int(HTTPStatus.OK))
        handler.send_header("Content-Type", record.mime_type)
        handler.send_header("Content-Length", str(record.size_bytes))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("X-Request-ID", normalize_request_id(request_id))
        handler.end_headers()
        try:
            while chunk := stream.read(_UPLOAD_CHUNK_BYTES):
                handler.wfile.write(chunk)
        except OSError:
            handler.close_connection = True
            logging.getLogger("openminion.api").warning(
                "client media read interrupted request_id=%s media_id=%s",
                normalize_request_id(request_id),
                media_id,
            )


def _validate_request_headers(handler: Any, operation: str) -> None:
    for name in (
        "Content-Type",
        "Content-Length",
        "X-Request-ID",
        "X-OpenMinion-Media-Name",
    ):
        if len(_header_values(handler.headers, name)) > 1:
            handler.close_connection = True
            raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")
    if operation != "upload" and (
        handler.headers.get("Content-Type") is not None
        or handler.headers.get("X-OpenMinion-Media-Name") is not None
    ):
        handler.close_connection = True
        raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")


def _require_empty_body(handler: Any) -> None:
    if handler.headers.get("Transfer-Encoding"):
        handler.close_connection = True
        raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")
    lengths = _header_values(handler.headers, "Content-Length")
    if len(lengths) > 1 or (lengths and lengths[0] != "0"):
        handler.close_connection = True
        raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")


def _write_error(
    handler: Any,
    method: str,
    path: str,
    request_id: str | None,
    exc: ClientMediaError,
    *,
    session_id: str | None = None,
) -> None:
    status, payload = error_response(
        exc.status,
        code=exc.code,
        message=str(exc),
        details={},
        retryable=exc.retryable,
        retry_after_ms=exc.retry_after_ms,
    )
    _write_payload(
        handler,
        status,
        payload,
        method=method,
        path=path,
        request_id=request_id,
        session_id=session_id,
    )


def _write_payload(
    handler: Any,
    status: HTTPStatus,
    payload: dict[str, Any],
    *,
    method: str,
    path: str,
    request_id: str | None,
    session_id: str | None,
) -> None:
    response = finalize_api_response(
        payload=payload,
        status=status,
        method=method,
        path=path,
        request_id=normalize_request_id(request_id),
        started_at=monotonic(),
        logger=logging.getLogger("openminion.api"),
        session_id=session_id,
    )
    status, encoded = handler._bounded_json_response(status, response)
    handler.send_response(int(status))
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.send_header("X-Request-ID", response["meta"]["request_id"])
    if retry_after_ms := response.get("error", {}).get("retry_after_ms"):
        handler.send_header("Retry-After", str(max(1, int(retry_after_ms) // 1000)))
    if handler.close_connection:
        handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(encoded)


def _decode_name(value: str) -> str:
    index = 0
    while index < len(value):
        if value[index] == "%":
            if _PERCENT_ESCAPE.fullmatch(value[index : index + 3]) is None:
                raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")
            index += 3
        elif value[index] not in _UNRESERVED_NAME:
            raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")
        else:
            index += 1
    try:
        name = unquote_to_bytes(value).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request") from exc
    if (
        not name
        or len(name.encode("utf-8")) > 128
        or name in {".", ".."}
        or any(char in name for char in ("/", "\\", "\0"))
        or any(category(char) == "Cc" for char in name)
    ):
        raise _media_error(HTTPStatus.BAD_REQUEST, "invalid_request")
    return name


def _validate_media(mime_type: str, data: bytes) -> tuple[str, bool]:
    matched = (
        mime_type == "image/png"
        and data.startswith(b"\x89PNG\r\n\x1a\n")
        or mime_type == "image/jpeg"
        and data.startswith(b"\xff\xd8\xff")
        or mime_type == "image/webp"
        and len(data) >= 12
        and data[:4] == b"RIFF"
        and data[8:12] == b"WEBP"
        or mime_type == "application/pdf"
        and data.startswith(b"%PDF-")
        or mime_type == "text/plain"
        and _valid_text(data)
        or mime_type == "application/json"
        and _valid_json(data)
    )
    if not matched:
        raise _media_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported_media_type")
    return _FORMATS[mime_type]


def _valid_text(data: bytes) -> bool:
    if b"\0" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _valid_json(data: bytes) -> bool:
    try:
        json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return True


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _backpressure() -> ClientMediaError:
    return ClientMediaError(
        HTTPStatus.TOO_MANY_REQUESTS,
        "media_backpressure",
        "Media admission is temporarily full.",
        retryable=True,
        retry_after_ms=1000,
    )


def _media_error(status: HTTPStatus, code: str) -> ClientMediaError:
    messages = {
        "forbidden": "Request is not authorized.",
        "invalid_request": "Media request is invalid.",
        "length_required": "Content-Length is required.",
        "media_upload_timeout": "Media upload did not complete before its deadline.",
        "media_too_large": "Media exceeds its size limit.",
        "unsupported_media_type": "Media type or content is unsupported.",
        "media_expired": "Media has expired.",
        "session_closed": "Session is closed.",
        "media_already_attached": "Media is already attached to another turn.",
        "media_not_found": "Media was not found.",
        "media_storage_failed": "Media storage failed.",
    }
    return ClientMediaError(status, code, messages[code])


__all__ = [
    "ClientMediaCoordinator",
    "ClientMediaError",
    "close_media",
    "handle_media_http",
    "install_media",
]
