from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
import re
import secrets
from threading import RLock
from typing import Any

from openminion.api.queries.client_artifacts import (
    ArtifactQueryError,
    artifact_event_refs,
    resolve_session_artifact_facade,
)
from openminion.api.server.client_auth import ClientAuthService, ClientIdentity
from openminion.modules.artifact.config import from_base_config
from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.artifact.errors import ArtifactCtlError
from openminion.modules.artifact.refs import is_canonical_artifact_ref
from openminion.services.brain.session_artifacts import (
    SessionArtifactOperationError,
    SessionArtifactUnavailable,
)


_CURSOR_IDLE_TTL = timedelta(minutes=5)
_CURSOR_ABSOLUTE_TTL = timedelta(minutes=10)
_RECORD_IDLE_TTL = timedelta(minutes=30)
_TOMBSTONE_TTL = timedelta(minutes=5)
_PATH_TOKEN = re.compile(
    r"(?:(?<=^)|(?<=[\s\"'=\(\[\{]))(?:/|[A-Za-z]:[\\/]|\\\\)[^\s\"',\)\]\}]+"
)
_TEXT_MIMES = frozenset({"text/plain", "application/json"})


class ClientArtifactError(RuntimeError):
    def __init__(self, status: HTTPStatus, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class _ScanEntry:
    artifact_ref: str
    first_seq: int
    occurrence_count: int = 1


@dataclass
class _Cursor:
    cursor_id: str
    client_id: str
    session_id: str
    created_at: datetime
    last_access: datetime
    phase: str = "scan"
    high_water: int = 0
    after_seq: int = 0
    emit_position: int = 0
    entries: dict[str, _ScanEntry] = field(default_factory=dict)
    artifact_id: str | None = None
    content_kind: str | None = None
    content_text: str | None = None


@dataclass
class _OpaqueRecord:
    artifact_id: str
    client_id: str
    session_id: str
    artifact_ref: str
    created_at: datetime
    last_access: datetime


class ClientArtifactCoordinator:
    def __init__(
        self,
        *,
        client_auth: ClientAuthService,
        runtime: Any,
        artifactctl: ArtifactCtl,
    ) -> None:
        self._client_auth = client_auth
        self._runtime = runtime
        self._artifactctl = artifactctl
        self._lock = RLock()
        self._cursors: dict[str, _Cursor] = {}
        self._cursor_tombstones: dict[str, tuple[str, datetime]] = {}
        self._records: dict[str, _OpaqueRecord] = {}
        self._record_by_source: dict[tuple[str, str, str], str] = {}
        self._record_tombstones: dict[str, tuple[str, datetime]] = {}
        self._session_generations: dict[str, int] = {}
        self._closing_sessions: dict[str, int] = {}
        self._closed = False

    def list_artifacts(
        self,
        identity: ClientIdentity,
        session_id: str,
        *,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        generation = self._admit(identity, session_id)
        facade = self._facade(identity, session_id)
        now = datetime.now(UTC)
        with self._lock:
            self._purge_locked(now)
            state = (
                self._cursor_locked(identity, session_id, cursor, now)
                if cursor
                else self._new_cursor_locked(identity, session_id, now)
            )
        if state.phase == "scan":
            try:
                page = facade.get_artifact_catalog_event_page(
                    session_id,
                    after_seq=state.after_seq,
                    high_water=state.high_water,
                    limit=500,
                )
            except SessionArtifactUnavailable as exc:
                raise _error("artifact_unavailable") from exc
            with self._lock:
                self._recheck_session_locked(session_id, generation)
                current = self._cursor_locked(
                    identity, session_id, state.cursor_id, datetime.now(UTC)
                )
                current.high_water = int(page["high_water"])
                for event in page["events"]:
                    seq = int(event["seq"])
                    for artifact_ref in artifact_event_refs(event):
                        if not is_canonical_artifact_ref(artifact_ref):
                            continue
                        entry = current.entries.get(artifact_ref)
                        if entry is None:
                            if len(current.entries) >= 256:
                                self._expire_cursor_locked(current, datetime.now(UTC))
                                raise _error("catalog_too_large")
                            current.entries[artifact_ref] = _ScanEntry(
                                artifact_ref=artifact_ref,
                                first_seq=seq,
                            )
                        else:
                            entry.occurrence_count += 1
                current.after_seq = int(page["next_after_seq"])
                if not bool(page["complete"]):
                    return {
                        "artifacts": [],
                        "next_cursor": current.cursor_id,
                        "complete": False,
                    }
                current.phase = "emit"
                state = current
        try:
            detached_refs = set(facade.get_detached_artifact_refs(session_id))
        except SessionArtifactUnavailable as exc:
            raise _error("artifact_unavailable") from exc
        with self._lock:
            self._recheck_session_locked(session_id, generation)
            ordered = sorted(
                state.entries.values(),
                key=lambda entry: (entry.first_seq, entry.artifact_ref),
            )
            page_entries = ordered[state.emit_position : state.emit_position + limit]
            state.emit_position += len(page_entries)
            complete = state.emit_position >= len(ordered)
            artifacts = [
                self._catalog_item_locked(
                    identity,
                    session_id,
                    entry,
                    now,
                    detached_refs=detached_refs,
                )
                for entry in page_entries
            ]
            next_cursor = None if complete else state.cursor_id
            if complete:
                self._cursors.pop(state.cursor_id, None)
            return {
                "artifacts": artifacts,
                "next_cursor": next_cursor,
                "complete": complete,
            }

    def read_artifact(
        self,
        identity: ClientIdentity,
        session_id: str,
        artifact_id: str,
        *,
        cursor: str | None,
        limit_bytes: int,
    ) -> dict[str, Any]:
        generation = self._admit(identity, session_id)
        self._facade(identity, session_id)
        with self._lock:
            record = self._record_locked(identity, session_id, artifact_id)
            if cursor:
                state = self._cursor_locked(
                    identity, session_id, cursor, datetime.now(UTC)
                )
                if state.phase != "content" or state.artifact_id != artifact_id:
                    raise _error("invalid_request")
                return self._content_page_locked(
                    state,
                    session_id=session_id,
                    artifact_id=artifact_id,
                    limit_bytes=limit_bytes,
                )
        try:
            meta = self._artifactctl.get(record.artifact_ref)
            mime = str(meta.mime or "").lower()
            if meta.deleted_at:
                raise _error("artifact_missing")
            if mime not in _TEXT_MIMES or int(meta.size_bytes) > 256 * 1024:
                raise _error("artifact_unsupported")
            with self._artifactctl.open(record.artifact_ref) as stream:
                data = stream.read(256 * 1024 + 1)
        except ClientArtifactError:
            raise
        except ArtifactCtlError as exc:
            raise _error("artifact_missing") from exc
        if len(data) > 256 * 1024:
            raise _error("content_too_large")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _error("artifact_unsupported") from exc
        projected = _redact_paths(text)
        if len(projected.encode("utf-8")) > 256 * 1024:
            raise _error("content_too_large")
        content_kind = "json" if mime == "application/json" else "text"
        with self._lock:
            self._recheck_session_locked(session_id, generation)
            now = datetime.now(UTC)
            state = _Cursor(
                cursor_id=secrets.token_urlsafe(36),
                client_id=identity.client_id,
                session_id=session_id,
                created_at=now,
                last_access=now,
                phase="content",
                artifact_id=artifact_id,
                content_kind=content_kind,
                content_text=projected,
            )
            self._admit_cursor_locked(identity.client_id)
            self._cursors[state.cursor_id] = state
            return self._content_page_locked(
                state,
                session_id=session_id,
                artifact_id=artifact_id,
                limit_bytes=limit_bytes,
            )

    def decide(
        self,
        identity: ClientIdentity,
        session_id: str,
        artifact_id: str,
        *,
        detached: bool,
        request_id: str,
    ) -> dict[str, Any]:
        generation = self._admit(identity, session_id)
        facade = self._facade(identity, session_id)
        with self._lock:
            self._recheck_session_locked(session_id, generation)
            record = self._record_locked(identity, session_id, artifact_id)
            try:
                outcome = facade.apply_artifact_decision(
                    session_id,
                    artifact_ref=record.artifact_ref,
                    detached=detached,
                    reason_code="desktop_user_action",
                    request_id=request_id,
                )
            except SessionArtifactOperationError as exc:
                code = exc.code
                if code in {
                    "artifact_state_backpressure",
                    "artifact_state_too_large",
                }:
                    raise _error(code) from exc
                if code == "session_turn_active":
                    raise _error("session_turn_active") from exc
                raise
            except SessionArtifactUnavailable as exc:
                raise _error("artifact_unavailable") from exc
        return {
            "artifact_id": artifact_id,
            "session_id": session_id,
            "state": "detached" if detached else "active",
            "outcome": outcome,
        }

    def revoke_client(self, identity: ClientIdentity) -> None:
        with self._lock:
            self._clear_client_locked(identity.client_id)

    def cancel_session(self, session_id: str, reason: str) -> Any:
        del reason
        with self._lock:
            self._closing_sessions[session_id] = (
                self._closing_sessions.get(session_id, 0) + 1
            )
            self._session_generations[session_id] = (
                self._session_generations.get(session_id, 0) + 1
            )
            self._clear_session_locked(session_id)

        def finish() -> None:
            with self._lock:
                count = self._closing_sessions.get(session_id, 0)
                if count <= 1:
                    self._closing_sessions.pop(session_id, None)
                else:
                    self._closing_sessions[session_id] = count - 1

        return finish

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cursors.clear()
            self._cursor_tombstones.clear()
            self._records.clear()
            self._record_by_source.clear()
            self._record_tombstones.clear()
        self._artifactctl.close()

    def _facade(self, identity: ClientIdentity, session_id: str) -> Any:
        if self._closed or not self._client_auth.is_active(identity):
            raise _error("forbidden")
        try:
            return resolve_session_artifact_facade(self._runtime, session_id)
        except ArtifactQueryError as exc:
            raise _error(exc.code) from exc

    def _admit(self, identity: ClientIdentity, session_id: str) -> int:
        with self._lock:
            if self._closed or not self._client_auth.is_active(identity):
                raise _error("forbidden")
            if self._closing_sessions.get(session_id, 0):
                raise _error("session_closed")
            return self._session_generations.get(session_id, 0)

    def _recheck_session_locked(self, session_id: str, generation: int) -> None:
        if (
            self._closing_sessions.get(session_id, 0)
            or self._session_generations.get(session_id, 0) != generation
        ):
            raise _error("session_closed")

    def _new_cursor_locked(
        self, identity: ClientIdentity, session_id: str, now: datetime
    ) -> _Cursor:
        for state in self._cursors.values():
            if (
                state.client_id == identity.client_id
                and state.session_id == session_id
                and (state.phase == "scan" or state.emit_position == 0)
            ):
                state.last_access = now
                return state
        self._admit_cursor_locked(identity.client_id)
        cursor_id = secrets.token_urlsafe(36)
        state = _Cursor(cursor_id, identity.client_id, session_id, now, now)
        self._cursors[cursor_id] = state
        return state

    def _admit_cursor_locked(self, client_id: str) -> None:
        client_count = sum(
            item.client_id == client_id for item in self._cursors.values()
        )
        if client_count >= 64 or len(self._cursors) >= 256:
            raise _error("artifact_backpressure")

    def _content_page_locked(
        self,
        state: _Cursor,
        *,
        session_id: str,
        artifact_id: str,
        limit_bytes: int,
    ) -> dict[str, Any]:
        text = state.content_text or ""
        start = state.emit_position
        end = _utf8_page_end(text, start, limit_bytes)
        page_text = text[start:end]
        state.emit_position = end
        complete = end >= len(text)
        next_cursor = None if complete else state.cursor_id
        if complete:
            self._cursors.pop(state.cursor_id, None)
        return {
            "schema_version": 1,
            "artifact_id": artifact_id,
            "session_id": session_id,
            "content_kind": state.content_kind,
            "text": page_text,
            "complete": complete,
            "next_cursor": next_cursor,
        }

    def _cursor_locked(
        self,
        identity: ClientIdentity,
        session_id: str,
        cursor_id: str,
        now: datetime,
    ) -> _Cursor:
        state = self._cursors.get(cursor_id)
        if state is None:
            tombstone = self._cursor_tombstones.get(cursor_id)
            if tombstone and tombstone[0] == identity.client_id:
                raise _error("cursor_expired")
            raise _error("invalid_request")
        if state.client_id != identity.client_id or state.session_id != session_id:
            raise _error("invalid_request")
        state.last_access = now
        return state

    def _catalog_item_locked(
        self,
        identity: ClientIdentity,
        session_id: str,
        entry: _ScanEntry,
        now: datetime,
        *,
        detached_refs: set[str],
    ) -> dict[str, Any]:
        record = self._opaque_record_locked(
            identity, session_id, entry.artifact_ref, now
        )
        state = "active"
        mime: str | None = None
        size: int | None = None
        created_at: str | None = None
        display_name = "Artifact"
        content_kind = "unavailable"
        try:
            meta = self._artifactctl.get(entry.artifact_ref)
            mime = str(meta.mime or "").lower() or None
            size = int(meta.size_bytes)
            created_at = str(meta.created_at)
            display_name = str(meta.label or meta.original_name or "Artifact")
            if meta.deleted_at:
                state = "missing"
            elif mime in _TEXT_MIMES:
                content_kind = "json" if mime == "application/json" else "text"
            else:
                state = "unsupported"
        except ArtifactCtlError:
            state = "missing"
        if entry.artifact_ref in detached_refs:
            state = "detached"
        actions = ["detach"] if state == "active" else []
        if state == "detached":
            actions = ["restore"]
        if content_kind != "unavailable" and state in {"active", "detached"}:
            actions.append("read")
        return {
            "schema_version": 1,
            "artifact_id": record.artifact_id,
            "session_id": session_id,
            "kind": "artifact",
            "display_name": _redact_paths(display_name),
            "mime_type": mime,
            "size_bytes": size,
            "created_at": created_at,
            "source": "attachment",
            "state": state,
            "content_kind": content_kind,
            "occurrence_count": entry.occurrence_count,
            "tool_name": None,
            "tool_status": None,
            "summary": None,
            "actions": sorted(actions),
        }

    def _opaque_record_locked(
        self,
        identity: ClientIdentity,
        session_id: str,
        artifact_ref: str,
        now: datetime,
    ) -> _OpaqueRecord:
        key = (identity.client_id, session_id, artifact_ref)
        existing_id = self._record_by_source.get(key)
        if existing_id and existing_id in self._records:
            record = self._records[existing_id]
            record.last_access = now
            return record
        client_count = sum(
            item.client_id == identity.client_id for item in self._records.values()
        )
        if client_count >= 256 or len(self._records) >= 1024:
            raise _error("artifact_backpressure")
        record = _OpaqueRecord(
            secrets.token_urlsafe(36),
            identity.client_id,
            session_id,
            artifact_ref,
            now,
            now,
        )
        self._records[record.artifact_id] = record
        self._record_by_source[key] = record.artifact_id
        return record

    def _record_locked(
        self, identity: ClientIdentity, session_id: str, artifact_id: str
    ) -> _OpaqueRecord:
        now = datetime.now(UTC)
        self._purge_locked(now)
        record = self._records.get(artifact_id)
        if record is None:
            tombstone = self._record_tombstones.get(artifact_id)
            if tombstone and tombstone[0] == identity.client_id:
                raise _error("artifact_expired")
            raise _error("artifact_not_found")
        if record.client_id != identity.client_id or record.session_id != session_id:
            raise _error("artifact_not_found")
        record.last_access = now
        return record

    def _purge_locked(self, now: datetime) -> None:
        for state in tuple(self._cursors.values()):
            if (
                state.last_access + _CURSOR_IDLE_TTL <= now
                or state.created_at + _CURSOR_ABSOLUTE_TTL <= now
            ):
                self._expire_cursor_locked(state, now)
        for record in tuple(self._records.values()):
            if record.last_access + _RECORD_IDLE_TTL <= now:
                self._expire_record_locked(record, now)
        self._cursor_tombstones = {
            key: value
            for key, value in self._cursor_tombstones.items()
            if value[1] + _TOMBSTONE_TTL > now
        }
        self._record_tombstones = {
            key: value
            for key, value in self._record_tombstones.items()
            if value[1] + _TOMBSTONE_TTL > now
        }

    def _expire_cursor_locked(self, state: _Cursor, now: datetime) -> None:
        self._cursors.pop(state.cursor_id, None)
        self._cursor_tombstones[state.cursor_id] = (state.client_id, now)
        _trim_tombstones(self._cursor_tombstones, client_cap=64, global_cap=256)

    def _expire_record_locked(self, record: _OpaqueRecord, now: datetime) -> None:
        self._records.pop(record.artifact_id, None)
        self._record_by_source.pop(
            (record.client_id, record.session_id, record.artifact_ref), None
        )
        self._record_tombstones[record.artifact_id] = (record.client_id, now)
        _trim_tombstones(self._record_tombstones, client_cap=256, global_cap=1024)

    def _clear_client_locked(self, client_id: str) -> None:
        for key, value in tuple(self._cursors.items()):
            if value.client_id == client_id:
                self._cursors.pop(key, None)
        for key, value in tuple(self._records.items()):
            if value.client_id == client_id:
                self._records.pop(key, None)
                self._record_by_source.pop(
                    (value.client_id, value.session_id, value.artifact_ref), None
                )

    def _clear_session_locked(self, session_id: str) -> None:
        for key, value in tuple(self._cursors.items()):
            if value.session_id == session_id:
                self._cursors.pop(key, None)
        for key, value in tuple(self._records.items()):
            if value.session_id == session_id:
                self._records.pop(key, None)
                self._record_by_source.pop(
                    (value.client_id, value.session_id, value.artifact_ref), None
                )


def _trim_tombstones(
    tombstones: dict[str, tuple[str, datetime]], *, client_cap: int, global_cap: int
) -> None:
    for client_id in {value[0] for value in tombstones.values()}:
        owned = sorted(
            (
                (key, value[1])
                for key, value in tombstones.items()
                if value[0] == client_id
            ),
            key=lambda item: item[1],
        )
        for key, _ in owned[:-client_cap]:
            tombstones.pop(key, None)
    ordered = sorted(tombstones.items(), key=lambda item: item[1][1])
    for key, _ in ordered[:-global_cap]:
        tombstones.pop(key, None)


def _redact_paths(value: str) -> str:
    lines = value.splitlines(keepends=True)
    is_diff = (
        any(line.startswith("--- ") for line in lines)
        and any(line.startswith("+++ ") for line in lines)
        and any(line.startswith("@@ ") for line in lines)
    )
    if not is_diff:
        return "[PATH REDACTED]" if _PATH_TOKEN.search(value) else value
    projected: list[str] = []
    for line in lines:
        ending = "\n" if line.endswith("\n") else ""
        content = line[:-1] if ending else line
        marker = next(
            (
                candidate
                for candidate in ("--- ", "+++ ", "@@ ", "+", "-", " ")
                if content.startswith(candidate)
            ),
            "",
        )
        if marker and _PATH_TOKEN.search(content[len(marker) :]):
            projected.append(f"{marker}[PATH REDACTED]{ending}")
        else:
            projected.append(line)
    return "".join(projected)


def _utf8_page_end(text: str, start: int, limit_bytes: int) -> int:
    used = 0
    end = start
    while end < len(text):
        width = len(text[end].encode("utf-8"))
        if used + width > limit_bytes:
            break
        used += width
        end += 1
    if end == start and start < len(text):
        raise _error("content_too_large")
    return end


def _error(code: str) -> ClientArtifactError:
    facts = {
        "invalid_request": (HTTPStatus.BAD_REQUEST, "Request is invalid."),
        "cursor_expired": (HTTPStatus.GONE, "Artifact cursor expired."),
        "forbidden": (HTTPStatus.FORBIDDEN, "Request is not authorized."),
        "artifact_not_found": (HTTPStatus.NOT_FOUND, "Artifact is unavailable."),
        "artifact_missing": (HTTPStatus.NOT_FOUND, "Artifact is unavailable."),
        "session_closed": (HTTPStatus.CONFLICT, "Session is not active."),
        "session_turn_active": (HTTPStatus.CONFLICT, "Session turn is active."),
        "artifact_expired": (HTTPStatus.GONE, "Artifact mapping expired."),
        "content_too_large": (
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            "Artifact content is too large.",
        ),
        "catalog_too_large": (
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            "Artifact catalog is too large.",
        ),
        "artifact_unsupported": (
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            "Artifact content is unsupported.",
        ),
        "artifact_backpressure": (
            HTTPStatus.TOO_MANY_REQUESTS,
            "Artifact service is busy.",
        ),
        "artifact_state_backpressure": (
            HTTPStatus.TOO_MANY_REQUESTS,
            "Artifact state is at capacity.",
        ),
        "artifact_state_too_large": (
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            "Artifact state is too large.",
        ),
        "artifact_unavailable": (
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Artifact service is unavailable.",
        ),
    }
    status, message = facts.get(
        code,
        (HTTPStatus.SERVICE_UNAVAILABLE, "Artifact service is unavailable."),
    )
    return ClientArtifactError(status, code, message)


def install_artifacts(
    handler_cls: type[Any], runtime: Any
) -> ClientArtifactCoordinator | None:
    auth = getattr(handler_cls, "client_auth", None)
    if auth is None or runtime is None:
        handler_cls.client_artifacts = None
        return None
    manager = runtime.config_manager
    artifactctl = ArtifactCtl(
        from_base_config(
            base_config=runtime.config,
            home_root=manager.home_root,
            data_root=manager.data_root,
        )
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=auth,
        runtime=runtime,
        artifactctl=artifactctl,
    )
    handler_cls.client_artifacts = coordinator
    return coordinator


def close_artifacts(coordinator: ClientArtifactCoordinator | None) -> None:
    if coordinator is not None:
        coordinator.close()
