from __future__ import annotations

from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.client import HTTPConnection
from io import BytesIO
import json
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from typing import Iterator

import pytest

from openminion.api.server import client_artifacts
from openminion.api.routes.client_artifacts import handle_request
from openminion.api.routes.contracts import APIRouteContext
from openminion.api.queries.client_artifacts import redact_paths
from openminion.api.server import build_api_server
from openminion.api.server.client_artifacts import (
    ClientArtifactCoordinator,
    ClientArtifactError,
)
from openminion.api.server.client_auth import ClientIdentity
from openminion.base.config import OpenMinionConfig, save_config
from openminion.services.brain.session_artifacts import (
    SessionArtifactOperationError,
    SessionArtifactUnavailable,
)
from tests._csc_fixtures import _csc_install_default_agent


_REF = f"artifact://sha256/{'a' * 64}"


def _tool_event(
    *,
    seq: int = 3,
    event_id: str = "terminal-1",
    output: object = None,
    status: str = "success",
    error: dict | None = None,
) -> dict:
    event_type = "tool.call.completed" if error is None else "tool.call.blocked"
    payload = {
        "schema_version": 1,
        "turn_scope_id": "turn-1",
        "call_id": f"call-{seq}",
        "status": status,
    }
    payload["output" if error is None else "error"] = output if error is None else error
    return {
        "event_id": event_id,
        "session_id": "session-1",
        "seq": seq,
        "timestamp": f"2026-08-20T00:00:{seq:02d}+00:00",
        "event_type": event_type,
        "parent_event_id": f"request-{seq}",
        "payload": payload,
        "refs": {},
        "tool_parent": {
            "event_id": f"request-{seq}",
            "session_id": "session-1",
            "event_type": "tool.call.requested",
            "payload": {
                "schema_version": 1,
                "turn_scope_id": "turn-1",
                "call_id": f"call-{seq}",
                "canonical_name": "file.read",
                "sanitized_normalized_arguments": {
                    "path": "/Users/person/must-not-cross"
                },
            },
        },
    }


class _Auth:
    def __init__(self) -> None:
        self.active = True
        self.expires_at = datetime.now(UTC) + timedelta(minutes=5)

    def is_active(self, identity) -> bool:
        return self.active and identity.client_id == "client-1"

    def lease_expires_at(self, identity):
        assert self.is_active(identity)
        return self.expires_at


class _MultiAuth(_Auth):
    def is_active(self, identity) -> bool:
        return self.active and identity.client_id.startswith("client-")


class _Artifacts:
    payload = b"open /Users/person/secret"

    def get(self, artifact_ref):
        assert artifact_ref == _REF
        return SimpleNamespace(
            mime="text/plain",
            size_bytes=len(self.payload),
            created_at="2026-08-20T00:00:00+00:00",
            label="notes.txt",
            original_name="notes.txt",
            deleted_at=None,
        )

    def open(self, artifact_ref):
        assert artifact_ref == _REF
        return BytesIO(self.payload)

    def close(self):
        return None


class _CredentialArtifacts(_Artifacts):
    payload = b"api_key=secret-value"


class _JsonArtifacts(_Artifacts):
    payload = (
        b'{"count":2,"enabled":true,"/Users/person/key":"path-key",'
        b'"api_key=secret-value":"secret-key","nested":{"path":"/Users/person/x",'
        b'"secret":"api_key=secret-value"},"items":[null,"safe"]}'
    )

    def get(self, artifact_ref):
        result = super().get(artifact_ref)
        result.mime = "application/json"
        return result


class _ControlByteArtifacts(_Artifacts):
    payload = b"plain\x00binary"


class _BinarySignatureArtifacts(_Artifacts):
    payload = b"%PDF-1.7\nsynthetic"


class _SecretNameArtifacts(_Artifacts):
    def get(self, artifact_ref):
        result = super().get(artifact_ref)
        result.label = "api_key=secret-value"
        return result


class _UnavailableArtifacts(_Artifacts):
    def __init__(self, *, mime: str = "text/plain") -> None:
        self.mime = mime
        self.deleted = False

    def get(self, artifact_ref):
        result = super().get(artifact_ref)
        result.mime = self.mime
        result.deleted_at = "2026-08-20T00:01:00+00:00" if self.deleted else None
        return result


class _BlockingArtifacts(_Artifacts):
    payload = b"safe content"

    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()

    def open(self, artifact_ref):
        assert artifact_ref == _REF
        self.entered.set()
        assert self.release.wait(timeout=5)
        return BytesIO(self.payload)


class _Facade:
    def __init__(self) -> None:
        self.detached = False
        self.decisions = []

    def get_artifact_catalog_event_page(
        self, session_id, *, after_seq, high_water, limit
    ):
        return {
            "high_water": 4,
            "events": [
                {
                    "seq": 2,
                    "event_type": "turn.user",
                    "refs": {"artifact_refs": [_REF, _REF]},
                },
                {
                    "seq": 3,
                    "event_type": "tool.call.completed",
                    "refs": {"artifact_refs": [_REF]},
                },
            ],
            "next_after_seq": 4,
            "complete": True,
        }

    def get_detached_artifact_refs(self, session_id, *, limit=256):
        return [_REF] if self.detached else []

    def apply_artifact_decision(
        self,
        session_id,
        *,
        artifact_ref,
        detached,
        reason_code,
        request_id,
    ):
        self.detached = detached
        self.decisions.append((artifact_ref, detached, reason_code, request_id))
        return "applied"


class _IncompleteFacade(_Facade):
    def get_artifact_catalog_event_page(
        self, session_id, *, after_seq, high_water, limit
    ):
        return {
            "high_water": 2,
            "events": [
                {
                    "seq": 1,
                    "event_type": "turn.user",
                    "refs": {"artifact_refs": []},
                }
            ],
            "next_after_seq": 1,
            "complete": False,
        }


class _ToolFacade(_Facade):
    def __init__(self, events: list[dict]) -> None:
        super().__init__()
        self.events = events

    def get_artifact_catalog_event_page(
        self, session_id, *, after_seq, high_water, limit
    ):
        return {
            "high_water": max(event["seq"] for event in self.events),
            "events": self.events,
            "next_after_seq": max(event["seq"] for event in self.events),
            "complete": True,
        }


class _PagedToolFacade(_ToolFacade):
    def get_artifact_catalog_event_page(
        self, session_id, *, after_seq, high_water, limit
    ):
        high_water = high_water or max(event["seq"] for event in self.events)
        remaining = [event for event in self.events if event["seq"] > after_seq]
        page = remaining[:1]
        next_after_seq = page[-1]["seq"] if page else after_seq
        return {
            "high_water": high_water,
            "events": page,
            "next_after_seq": next_after_seq,
            "complete": next_after_seq >= high_water,
        }


class _ManyArtifacts(_Artifacts):
    def get(self, artifact_ref):
        return SimpleNamespace(
            mime="text/plain",
            size_bytes=len(self.payload),
            created_at="2026-08-20T00:00:00+00:00",
            label="notes.txt",
            original_name="notes.txt",
            deleted_at=None,
        )


class _ManyRefsFacade(_Facade):
    def __init__(self, count: int) -> None:
        super().__init__()
        self.refs = [f"artifact://sha256/{index:064x}" for index in range(1, count + 1)]

    def get_artifact_catalog_event_page(
        self, session_id, *, after_seq, high_water, limit
    ):
        refs = self.refs if session_id == "session-1" else [self.refs[-1]]
        return {
            "high_water": 1,
            "events": [
                {
                    "seq": 1,
                    "event_type": "turn.user",
                    "refs": {"artifact_refs": refs},
                }
            ],
            "next_after_seq": 1,
            "complete": True,
        }


class _DetachedFailureFacade(_Facade):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def get_detached_artifact_refs(self, session_id, *, limit=256):
        raise self.error


def test_catalog_content_and_decision_keep_internal_refs_opaque(monkeypatch) -> None:
    facade = _Facade()
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(),
        runtime=object(),
        artifactctl=_Artifacts(),
    )
    identity = ClientIdentity("client-1", "config-1", 1, ())

    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)
    assert catalog["complete"] is True
    assert catalog["next_cursor"] is None
    assert len(catalog["artifacts"]) == 1
    item = catalog["artifacts"][0]
    assert item["occurrence_count"] == 2
    assert item["actions"] == ["detach", "read"]
    assert len(item["artifact_id"]) == 48
    assert _REF not in repr(catalog)

    content = coordinator.read_artifact(
        identity,
        "session-1",
        item["artifact_id"],
        cursor=None,
        limit_bytes=64 * 1024,
    )
    assert content["text"] == "[PATH REDACTED]"
    assert _REF not in repr(content)

    decision = coordinator.decide(
        identity,
        "session-1",
        item["artifact_id"],
        detached=True,
        request_id="request-1",
    )
    assert decision == {
        "artifact_id": item["artifact_id"],
        "session_id": "session-1",
        "state": "detached",
        "outcome": "applied",
    }
    assert facade.decisions == [(_REF, True, "desktop_user_action", "request-1")]


def test_tool_diff_catalog_and_content_are_typed_redacted_and_read_only(
    monkeypatch,
) -> None:
    event = _tool_event(
        output={
            "summary": "changed /Users/person/private",
            "outputs": {
                "diff": (
                    "--- /Users/person/old.txt\n"
                    "+++ C:\\private\\new.txt\n"
                    "@@ -1 +1 @@\n"
                    "-api_key=secret-value\n"
                    "+safe\n"
                )
            },
        }
    )
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: _ToolFacade([event]),
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(),
        runtime=object(),
        artifactctl=_Artifacts(),
    )
    identity = ClientIdentity("client-1", "config-1", 1, ("tool_outputs.read.v1",))

    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)
    assert len(catalog["artifacts"]) == 1
    item = catalog["artifacts"][0]
    assert item["kind"] == "tool_output"
    assert item["tool_name"] == "file.read"
    assert item["tool_status"] == "success"
    assert item["summary"] == "[PATH REDACTED]"
    assert item["content_kind"] == "diff"
    assert item["actions"] == ["read"]
    assert item["occurrence_count"] == 1

    content = coordinator.read_artifact(
        identity,
        "session-1",
        item["artifact_id"],
        cursor=None,
        limit_bytes=64 * 1024,
    )
    assert content["content_kind"] == "diff"
    assert content["text"].startswith(
        "--- [PATH REDACTED]\n+++ [PATH REDACTED]\n@@ -1 +1 @@\n"
    )
    assert "secret-value" not in content["text"]
    with pytest.raises(ClientArtifactError) as raised:
        coordinator.decide(
            identity,
            "session-1",
            item["artifact_id"],
            detached=True,
            request_id="request-1",
        )
    assert raised.value.code == "invalid_request"
    visible = json.dumps((catalog, content))
    for hidden in (
        "terminal-1",
        "request-3",
        "call-3",
        "/Users/person",
        "C:\\private",
        "must-not-cross",
    ):
        assert hidden not in visible


def test_tool_json_and_blocked_outputs_drop_unknown_fields_and_redact_paths(
    monkeypatch,
) -> None:
    completed = _tool_event(
        output={
            "summary": "safe summary",
            "outputs": {
                "nested": {"path": "/Users/person/private"},
                "secret": "api_key=secret-value",
            },
            "unknown": "must-not-cross",
        }
    )
    blocked = _tool_event(
        seq=4,
        event_id="terminal-2",
        status="blocked",
        error={
            "code": "TOOL_BLOCKED",
            "message": "blocked /Users/person/private",
            "details": {"secret": "api_key=secret-value"},
            "unknown": "must-not-cross",
        },
    )
    facade = _ToolFacade([completed, blocked])
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(), runtime=object(), artifactctl=_Artifacts()
    )
    identity = ClientIdentity("client-1", "config-1", 1, ("tool_outputs.read.v1",))

    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)
    assert [item["content_kind"] for item in catalog["artifacts"]] == [
        "json",
        "json",
    ]
    completed_content = coordinator.read_artifact(
        identity,
        "session-1",
        catalog["artifacts"][0]["artifact_id"],
        cursor=None,
        limit_bytes=64 * 1024,
    )
    blocked_content = coordinator.read_artifact(
        identity,
        "session-1",
        catalog["artifacts"][1]["artifact_id"],
        cursor=None,
        limit_bytes=64 * 1024,
    )
    assert json.loads(completed_content["text"]) == {
        "nested": {"path": "[PATH REDACTED]"},
        "secret": "api_key=[REDACTED]",
    }
    assert json.loads(blocked_content["text"]) == {
        "code": "TOOL_BLOCKED",
        "details": {"secret": "api_key=[REDACTED]"},
        "message": "[PATH REDACTED]",
    }
    visible = json.dumps((catalog, completed_content, blocked_content))
    assert "must-not-cross" not in visible


@pytest.mark.parametrize(
    "legacy_output",
    ["legacy scalar", {"content": "legacy mapping"}],
)
def test_legacy_tool_outputs_are_typed_unavailable(monkeypatch, legacy_output) -> None:
    event = _tool_event(output=legacy_output)
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: _ToolFacade([event]),
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(), runtime=object(), artifactctl=_Artifacts()
    )
    identity = ClientIdentity("client-1", "config-1", 1, ("tool_outputs.read.v1",))

    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)
    item = catalog["artifacts"][0]
    assert item["summary"] == "Output unavailable"
    assert item["content_kind"] == "unavailable"
    assert item["actions"] == []
    assert coordinator.read_artifact(
        identity,
        "session-1",
        item["artifact_id"],
        cursor=None,
        limit_bytes=64 * 1024,
    ) == {
        "schema_version": 1,
        "artifact_id": item["artifact_id"],
        "session_id": "session-1",
        "content_kind": "unavailable",
        "text": None,
        "complete": True,
        "next_cursor": None,
    }


def test_current_and_legacy_tool_outputs_scan_across_separate_pages(
    monkeypatch,
) -> None:
    events = [
        _tool_event(
            seq=1,
            event_id="current",
            output={"summary": "current", "outputs": {"text": "safe"}},
        ),
        _tool_event(seq=2, event_id="legacy-scalar", output="legacy"),
        _tool_event(
            seq=3,
            event_id="legacy-mapping",
            output={"content": "legacy"},
        ),
    ]
    facade = _PagedToolFacade(events)
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(), runtime=object(), artifactctl=_Artifacts()
    )
    identity = ClientIdentity("client-1", "config-1", 1, ("tool_outputs.read.v1",))

    page = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)
    while not page["complete"]:
        assert page["artifacts"] == []
        page = coordinator.list_artifacts(
            identity,
            "session-1",
            cursor=page["next_cursor"],
            limit=25,
        )

    assert [item["content_kind"] for item in page["artifacts"]] == [
        "text",
        "unavailable",
        "unavailable",
    ]


def test_tool_outputs_remain_absent_without_the_capability(monkeypatch) -> None:
    event = _tool_event(output={"summary": "safe", "outputs": {"text": "safe"}})
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: _ToolFacade([event]),
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(), runtime=object(), artifactctl=_Artifacts()
    )
    identity = ClientIdentity("client-1", "config-1", 1, ())

    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)

    assert catalog == {"artifacts": [], "next_cursor": None, "complete": True}


def test_invalid_tool_parent_and_oversized_output_fail_closed(monkeypatch) -> None:
    invalid = _tool_event(output={"summary": "safe", "outputs": {"text": "safe"}})
    invalid["tool_parent"] = None
    facade = _ToolFacade([invalid])
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(), runtime=object(), artifactctl=_Artifacts()
    )
    identity = ClientIdentity("client-1", "config-1", 1, ("tool_outputs.read.v1",))

    with pytest.raises(ClientArtifactError) as invalid_error:
        coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)
    assert invalid_error.value.code == "event_invalid"

    facade.events = [
        _tool_event(
            output={
                "summary": "safe",
                "outputs": {"text": "x" * (256 * 1024 + 1)},
            }
        )
    ]
    with pytest.raises(ClientArtifactError) as oversized_error:
        coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)
    assert oversized_error.value.code == "content_too_large"


def test_malformed_diff_is_plain_text_and_control_output_is_unavailable(
    monkeypatch,
) -> None:
    malformed = _tool_event(
        output={
            "summary": "safe",
            "outputs": {"diff": "--- old\n+++ new\n-no hunk\n+still text"},
        }
    )
    control = _tool_event(
        seq=4,
        event_id="terminal-control",
        output={"summary": "safe", "outputs": {"text": "unsafe\x1b[31m"}},
    )
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: _ToolFacade([malformed, control]),
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(), runtime=object(), artifactctl=_Artifacts()
    )
    identity = ClientIdentity("client-1", "config-1", 1, ("tool_outputs.read.v1",))

    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)

    assert [item["content_kind"] for item in catalog["artifacts"]] == [
        "text",
        "unavailable",
    ]


def test_session_close_clears_opaque_mappings(monkeypatch) -> None:
    facade = _Facade()
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(),
        runtime=object(),
        artifactctl=_Artifacts(),
    )
    identity = ClientIdentity("client-1", "config-1", 1, ())
    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)
    artifact_id = catalog["artifacts"][0]["artifact_id"]

    finish = coordinator.cancel_session("session-1", "session_closed")

    try:
        coordinator.read_artifact(
            identity,
            "session-1",
            artifact_id,
            cursor=None,
            limit_bytes=64 * 1024,
        )
    except Exception as exc:
        assert getattr(exc, "code", None) == "session_closed"
    else:
        raise AssertionError("cleared mapping remained readable")
    finish()

    try:
        coordinator.read_artifact(
            identity,
            "session-1",
            artifact_id,
            cursor=None,
            limit_bytes=64 * 1024,
        )
    except Exception as exc:
        assert getattr(exc, "code", None) == "artifact_not_found"
    else:
        raise AssertionError("cleared mapping returned after close cleanup")


def test_content_pages_use_one_bound_opaque_cursor(monkeypatch) -> None:
    facade = _Facade()
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(),
        runtime=object(),
        artifactctl=_Artifacts(),
    )
    identity = ClientIdentity("client-1", "config-1", 1, ())
    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)
    artifact_id = catalog["artifacts"][0]["artifact_id"]

    first = coordinator.read_artifact(
        identity,
        "session-1",
        artifact_id,
        cursor=None,
        limit_bytes=5,
    )
    assert first["complete"] is False
    assert len(first["next_cursor"]) == 48
    pieces = [first["text"]]
    page = first
    while not page["complete"]:
        page = coordinator.read_artifact(
            identity,
            "session-1",
            artifact_id,
            cursor=page["next_cursor"],
            limit_bytes=5,
        )
        pieces.append(page["text"])
    assert "".join(pieces) == "[PATH REDACTED]"
    assert page["next_cursor"] is None


def test_catalog_first_page_never_reuses_a_content_cursor(monkeypatch) -> None:
    facade = _Facade()
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(),
        runtime=object(),
        artifactctl=_Artifacts(),
    )
    identity = ClientIdentity("client-1", "config-1", 1, ())
    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)
    artifact_id = catalog["artifacts"][0]["artifact_id"]
    content = coordinator.read_artifact(
        identity,
        "session-1",
        artifact_id,
        cursor=None,
        limit_bytes=5,
    )
    assert content["complete"] is False

    refreshed = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)

    assert len(refreshed["artifacts"]) == 1
    assert refreshed["artifacts"][0]["artifact_id"] == artifact_id


def test_opaque_record_expiry_is_bound_to_client_and_session(monkeypatch) -> None:
    facade = _Facade()
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    auth = _Auth()
    coordinator = ClientArtifactCoordinator(
        client_auth=auth,
        runtime=object(),
        artifactctl=_Artifacts(),
    )
    identity = ClientIdentity("client-1", "config-1", 1, ())
    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)
    artifact_id = catalog["artifacts"][0]["artifact_id"]
    auth.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    for record in coordinator._records.values():
        record.lease_expires_at = auth.expires_at

    with pytest.raises(ClientArtifactError) as expired:
        coordinator.read_artifact(
            identity,
            "session-1",
            artifact_id,
            cursor=None,
            limit_bytes=64 * 1024,
        )
    with pytest.raises(ClientArtifactError) as wrong_session:
        coordinator.read_artifact(
            identity,
            "session-2",
            artifact_id,
            cursor=None,
            limit_bytes=64 * 1024,
        )

    assert expired.value.code == "artifact_expired"
    assert wrong_session.value.code == "artifact_not_found"


def test_cursor_caps_expiry_and_tombstone_binding(monkeypatch) -> None:
    facade = _IncompleteFacade()
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    auth = _Auth()
    coordinator = ClientArtifactCoordinator(
        client_auth=auth,
        runtime=object(),
        artifactctl=_Artifacts(),
    )
    identity = ClientIdentity("client-1", "config-1", 1, ())
    cursors = [
        (
            session_id,
            coordinator.list_artifacts(identity, session_id, cursor=None, limit=25)[
                "next_cursor"
            ],
        )
        for session_id in (f"session-{index}" for index in range(64))
    ]

    with pytest.raises(ClientArtifactError) as full:
        coordinator.list_artifacts(identity, "session-64", cursor=None, limit=25)
    assert full.value.code == "artifact_backpressure"

    expired_at = datetime.now(UTC) - timedelta(seconds=1)
    for cursor in coordinator._cursors.values():
        cursor.lease_expires_at = expired_at
    coordinator.list_artifacts(identity, "session-fresh", cursor=None, limit=25)
    assert len(coordinator._cursor_tombstones) == 64
    expired_session, expired_cursor = cursors[0]
    with pytest.raises(ClientArtifactError) as expired:
        coordinator.list_artifacts(
            identity,
            expired_session,
            cursor=expired_cursor,
            limit=25,
        )
    with pytest.raises(ClientArtifactError) as wrong_session:
        coordinator.list_artifacts(
            identity,
            "session-other",
            cursor=expired_cursor,
            limit=25,
        )
    assert expired.value.code == "cursor_expired"
    assert wrong_session.value.code == "invalid_request"


def test_catalog_and_opaque_record_caps_fail_before_unbounded_state(
    monkeypatch,
) -> None:
    identity = ClientIdentity("client-1", "config-1", 1, ())
    too_many = _ManyRefsFacade(257)
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: too_many,
    )
    catalog_owner = ClientArtifactCoordinator(
        client_auth=_Auth(),
        runtime=object(),
        artifactctl=_ManyArtifacts(),
    )

    with pytest.raises(ClientArtifactError) as catalog_full:
        catalog_owner.list_artifacts(identity, "session-1", cursor=None, limit=100)
    assert catalog_full.value.code == "catalog_too_large"
    assert not catalog_owner._cursors
    assert not catalog_owner._records

    at_capacity = _ManyRefsFacade(256)
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: at_capacity,
    )
    record_owner = ClientArtifactCoordinator(
        client_auth=_Auth(),
        runtime=object(),
        artifactctl=_ManyArtifacts(),
    )
    page = record_owner.list_artifacts(identity, "session-1", cursor=None, limit=100)
    while not page["complete"]:
        page = record_owner.list_artifacts(
            identity,
            "session-1",
            cursor=page["next_cursor"],
            limit=100,
        )
    assert len(record_owner._records) == 256
    with pytest.raises(ClientArtifactError) as record_full:
        record_owner.list_artifacts(identity, "session-2", cursor=None, limit=100)
    assert record_full.value.code == "artifact_backpressure"
    assert len(record_owner._records) == 256


def test_global_cursor_and_record_caps_fail_before_unbounded_state(
    monkeypatch,
) -> None:
    auth = _MultiAuth()
    incomplete = _IncompleteFacade()
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: incomplete,
    )
    cursor_owner = ClientArtifactCoordinator(
        client_auth=auth,
        runtime=object(),
        artifactctl=_Artifacts(),
    )
    identities = [
        ClientIdentity(f"client-{index}", "config-1", 1, ()) for index in range(5)
    ]
    for client_index, identity in enumerate(identities[:4]):
        for session_index in range(64):
            page = cursor_owner.list_artifacts(
                identity,
                f"session-{client_index}-{session_index}",
                cursor=None,
                limit=25,
            )
            assert page["complete"] is False
    with pytest.raises(ClientArtifactError) as cursors_full:
        cursor_owner.list_artifacts(
            identities[4], "session-overflow", cursor=None, limit=25
        )
    assert cursors_full.value.code == "artifact_backpressure"
    assert len(cursor_owner._cursors) == 256

    at_capacity = _ManyRefsFacade(256)
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: at_capacity,
    )
    record_owner = ClientArtifactCoordinator(
        client_auth=auth,
        runtime=object(),
        artifactctl=_ManyArtifacts(),
    )
    for identity in identities[:4]:
        page = record_owner.list_artifacts(
            identity, "session-1", cursor=None, limit=100
        )
        while not page["complete"]:
            page = record_owner.list_artifacts(
                identity,
                "session-1",
                cursor=page["next_cursor"],
                limit=100,
            )
    assert len(record_owner._records) == 1024
    with pytest.raises(ClientArtifactError) as records_full:
        record_owner.list_artifacts(identities[4], "session-2", cursor=None, limit=100)
    assert records_full.value.code == "artifact_backpressure"
    assert len(record_owner._records) == 1024


def test_coordinator_restart_invalidates_opaque_records_and_cursors(
    monkeypatch,
) -> None:
    identity = ClientIdentity("client-1", "config-1", 1, ())
    auth = _Auth()
    facade = _Facade()
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    original = ClientArtifactCoordinator(
        client_auth=auth,
        runtime=object(),
        artifactctl=_Artifacts(),
    )
    catalog = original.list_artifacts(identity, "session-1", cursor=None, limit=25)
    artifact_id = catalog["artifacts"][0]["artifact_id"]
    restarted = ClientArtifactCoordinator(
        client_auth=auth,
        runtime=object(),
        artifactctl=_Artifacts(),
    )
    with pytest.raises(ClientArtifactError) as stale_record:
        restarted.read_artifact(
            identity,
            "session-1",
            artifact_id,
            cursor=None,
            limit_bytes=64 * 1024,
        )
    assert stale_record.value.code == "artifact_not_found"

    incomplete = _IncompleteFacade()
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: incomplete,
    )
    cursor_owner = ClientArtifactCoordinator(
        client_auth=auth,
        runtime=object(),
        artifactctl=_Artifacts(),
    )
    cursor = cursor_owner.list_artifacts(identity, "session-1", cursor=None, limit=25)[
        "next_cursor"
    ]
    restarted = ClientArtifactCoordinator(
        client_auth=auth,
        runtime=object(),
        artifactctl=_Artifacts(),
    )
    with pytest.raises(ClientArtifactError) as stale_cursor:
        restarted.list_artifacts(identity, "session-1", cursor=cursor, limit=25)
    assert stale_cursor.value.code == "invalid_request"


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            SessionArtifactOperationError(
                "artifact_state_too_large", "internal projection detail"
            ),
            "artifact_state_too_large",
        ),
        (SessionArtifactUnavailable("internal runtime detail"), "runtime_unavailable"),
        (RuntimeError("internal storage detail"), "runtime_unavailable"),
    ],
)
def test_catalog_normalizes_projection_failures(
    monkeypatch, error, expected_code
) -> None:
    facade = _DetachedFailureFacade(error)
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(),
        runtime=object(),
        artifactctl=_Artifacts(),
    )
    identity = ClientIdentity("client-1", "config-1", 1, ())

    with pytest.raises(ClientArtifactError) as raised:
        coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)

    assert raised.value.code == expected_code
    assert "internal" not in raised.value.message


def test_catalog_and_decision_normalize_unexpected_owner_failures(
    monkeypatch,
) -> None:
    facade = _Facade()
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(),
        runtime=object(),
        artifactctl=_Artifacts(),
    )
    identity = ClientIdentity("client-1", "config-1", 1, ())
    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)
    artifact_id = catalog["artifacts"][0]["artifact_id"]

    def fail(*args, **kwargs):
        raise RuntimeError("internal storage detail")

    facade.apply_artifact_decision = fail
    with pytest.raises(ClientArtifactError) as decision:
        coordinator.decide(
            identity,
            "session-1",
            artifact_id,
            detached=True,
            request_id="request-failure",
        )
    assert decision.value.code == "runtime_unavailable"
    assert "internal" not in decision.value.message

    facade.get_artifact_catalog_event_page = fail
    with pytest.raises(ClientArtifactError) as page:
        coordinator.list_artifacts(identity, "session-2", cursor=None, limit=25)
    assert page.value.code == "runtime_unavailable"
    assert "internal" not in page.value.message


def test_content_applies_existing_credential_redaction(monkeypatch) -> None:
    facade = _Facade()
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(),
        runtime=object(),
        artifactctl=_CredentialArtifacts(),
    )
    identity = ClientIdentity("client-1", "config-1", 1, ())
    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)

    content = coordinator.read_artifact(
        identity,
        "session-1",
        catalog["artifacts"][0]["artifact_id"],
        cursor=None,
        limit_bytes=64 * 1024,
    )

    assert content["text"] == "api_key=[REDACTED]"
    assert "secret-value" not in repr(content)


def test_json_content_preserves_shape_and_redacts_nested_scalars(monkeypatch) -> None:
    facade = _Facade()
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(),
        runtime=object(),
        artifactctl=_JsonArtifacts(),
    )
    identity = ClientIdentity("client-1", "config-1", 1, ())
    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)

    content = coordinator.read_artifact(
        identity,
        "session-1",
        catalog["artifacts"][0]["artifact_id"],
        cursor=None,
        limit_bytes=64 * 1024,
    )

    projected = json.loads(content["text"])
    assert projected == {
        "count": 2,
        "enabled": True,
        "[PATH REDACTED]": "path-key",
        "api_key=[REDACTED]": "secret-key",
        "nested": {
            "path": "[PATH REDACTED]",
            "secret": "api_key=[REDACTED]",
        },
        "items": [None, "safe"],
    }


def test_text_content_rejects_binary_control_bytes(monkeypatch) -> None:
    facade = _Facade()
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(),
        runtime=object(),
        artifactctl=_ControlByteArtifacts(),
    )
    identity = ClientIdentity("client-1", "config-1", 1, ())
    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)

    with pytest.raises(ClientArtifactError, match="unsupported") as raised:
        coordinator.read_artifact(
            identity,
            "session-1",
            catalog["artifacts"][0]["artifact_id"],
            cursor=None,
            limit_bytes=64 * 1024,
        )

    assert raised.value.code == "artifact_unsupported"


def test_text_content_rejects_binary_file_signatures(monkeypatch) -> None:
    facade = _Facade()
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(),
        runtime=object(),
        artifactctl=_BinarySignatureArtifacts(),
    )
    identity = ClientIdentity("client-1", "config-1", 1, ())
    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)

    with pytest.raises(ClientArtifactError) as raised:
        coordinator.read_artifact(
            identity,
            "session-1",
            catalog["artifacts"][0]["artifact_id"],
            cursor=None,
            limit_bytes=64 * 1024,
        )

    assert raised.value.code == "artifact_unsupported"


def test_catalog_display_name_uses_secret_redaction(monkeypatch) -> None:
    facade = _Facade()
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(),
        runtime=object(),
        artifactctl=_SecretNameArtifacts(),
    )
    identity = ClientIdentity("client-1", "config-1", 1, ())

    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)

    assert catalog["artifacts"][0]["display_name"] == "api_key=[REDACTED]"
    assert "secret-value" not in repr(catalog)


@pytest.mark.parametrize(
    ("mime", "delete_after_catalog", "expected_code"),
    [
        ("application/pdf", False, "artifact_unsupported"),
        ("text/plain", True, "artifact_missing"),
    ],
)
def test_content_fails_closed_when_unsupported_or_deleted_after_catalog(
    monkeypatch, mime, delete_after_catalog, expected_code
) -> None:
    facade = _Facade()
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    artifacts = _UnavailableArtifacts(mime=mime)
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(),
        runtime=object(),
        artifactctl=artifacts,
    )
    identity = ClientIdentity("client-1", "config-1", 1, ())
    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)
    artifact_id = catalog["artifacts"][0]["artifact_id"]
    artifacts.deleted = delete_after_catalog

    with pytest.raises(ClientArtifactError) as raised:
        coordinator.read_artifact(
            identity,
            "session-1",
            artifact_id,
            cursor=None,
            limit_bytes=64 * 1024,
        )

    assert raised.value.code == expected_code


def test_revoke_fences_an_admitted_content_read(monkeypatch) -> None:
    facade = _Facade()
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    auth = _Auth()
    artifacts = _BlockingArtifacts()
    coordinator = ClientArtifactCoordinator(
        client_auth=auth,
        runtime=object(),
        artifactctl=artifacts,
    )
    identity = ClientIdentity("client-1", "config-1", 1, ())
    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)
    artifact_id = catalog["artifacts"][0]["artifact_id"]
    observed = {}

    def read() -> None:
        try:
            observed["result"] = coordinator.read_artifact(
                identity,
                "session-1",
                artifact_id,
                cursor=None,
                limit_bytes=64 * 1024,
            )
        except ClientArtifactError as exc:
            observed["error"] = exc.code

    worker = Thread(target=read)
    worker.start()
    assert artifacts.entered.wait(timeout=5)
    auth.active = False
    coordinator.revoke_client(identity)
    artifacts.release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert observed == {"error": "forbidden"}
    assert not coordinator._cursors


def test_shutdown_fences_an_admitted_content_read(monkeypatch) -> None:
    facade = _Facade()
    monkeypatch.setattr(
        client_artifacts,
        "resolve_session_artifact_facade",
        lambda runtime, session_id: facade,
    )
    artifacts = _BlockingArtifacts()
    coordinator = ClientArtifactCoordinator(
        client_auth=_Auth(),
        runtime=object(),
        artifactctl=artifacts,
    )
    identity = ClientIdentity("client-1", "config-1", 1, ())
    catalog = coordinator.list_artifacts(identity, "session-1", cursor=None, limit=25)
    artifact_id = catalog["artifacts"][0]["artifact_id"]
    observed = {}

    def read() -> None:
        try:
            observed["result"] = coordinator.read_artifact(
                identity,
                "session-1",
                artifact_id,
                cursor=None,
                limit_bytes=64 * 1024,
            )
        except ClientArtifactError as exc:
            observed["error"] = exc.code

    worker = Thread(target=read)
    worker.start()
    assert artifacts.entered.wait(timeout=5)
    coordinator.close()
    artifacts.release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert observed == {"error": "forbidden"}
    assert not coordinator._cursors


def test_path_projection_redacts_posix_drive_unc_and_diff_lines() -> None:
    assert redact_paths("open /Users/person/private") == "[PATH REDACTED]"
    assert redact_paths(r"open C:\private\file.txt") == "[PATH REDACTED]"
    assert redact_paths(r"open \\server\share\file.txt") == "[PATH REDACTED]"
    diff = (
        "--- /Users/person/old.txt\n+++ C:\\private\\new.txt\n@@ -1 +1 @@\n-old\n+new\n"
    )
    projected = redact_paths(diff)
    assert projected.startswith("--- [PATH REDACTED]\n+++ [PATH REDACTED]\n")
    assert "/Users" not in projected
    assert "C:\\private" not in projected


class _RouteArtifacts:
    def list_artifacts(self, identity, session_id, *, cursor, limit):
        return {"artifacts": [], "next_cursor": None, "complete": True}

    def read_artifact(self, identity, session_id, artifact_id, *, cursor, limit_bytes):
        return {
            "schema_version": 1,
            "artifact_id": artifact_id,
            "session_id": session_id,
            "content_kind": "text",
            "text": "safe",
            "complete": True,
            "next_cursor": None,
        }

    def decide(self, identity, session_id, artifact_id, *, detached, request_id):
        return {
            "artifact_id": artifact_id,
            "session_id": session_id,
            "state": "detached" if detached else "active",
            "outcome": "applied",
        }


def _route_context() -> APIRouteContext:
    return APIRouteContext(
        config_path=None,
        runtime=None,
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="request-1",
        client_identity=ClientIdentity("client-1", "config-1", 1, ()),
        client_artifacts=_RouteArtifacts(),
    )


@pytest.mark.parametrize(
    "query",
    ["unknown=1", "limit=1&limit=2", "limit=", "cursor=   "],
)
def test_artifact_routes_reject_unknown_duplicate_or_blank_queries(query) -> None:
    result = handle_request(
        _route_context(),
        method_name="GET",
        path="/v1/client/sessions/session-1/artifacts",
        body=None,
        query=query,
    )

    assert result is not None
    assert result.status == HTTPStatus.BAD_REQUEST
    assert result.payload["error"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    ("body", "query"),
    [({"schema_version": 1, "extra": True}, None), ({"schema_version": 1}, "x=1")],
)
def test_artifact_decision_route_requires_exact_body_and_no_query(body, query) -> None:
    result = handle_request(
        _route_context(),
        method_name="POST",
        path="/v1/client/sessions/session-1/artifacts/artifact-1/detach",
        body=body,
        query=query,
    )

    assert result is not None
    assert result.status == HTTPStatus.BAD_REQUEST
    assert result.payload["error"]["code"] == "invalid_request"


def _http_json_request(
    connection: HTTPConnection,
    method: str,
    path: str,
    headers: dict[str, str],
    payload: dict | None,
) -> tuple[int, dict]:
    body = json.dumps(payload).encode() if payload is not None else None
    request_headers = dict(headers)
    if body is not None:
        request_headers.setdefault("Content-Length", str(len(body)))
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    return response.status, json.loads(response.read())


@pytest.fixture
def loopback_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[SimpleNamespace]:
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    config_path = home_root / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="openai")  # type: ignore[attr-defined]
    config.runtime.ipc_token = "master-token"
    config.storage.path = str(data_root / "state" / "api.db")
    config.providers.openai.api_key = "synthetic-test-key"
    config.providers.openai.base_url = "https://fixture.invalid/v1"
    home_root.mkdir(parents=True)
    monkeypatch.setenv("OPENMINION_HOME", str(home_root))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    monkeypatch.setenv("OPENMINION_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("OPENMINION_GENERATED_ROOT", str(data_root / "runtime"))
    save_config(config, str(config_path))
    server = build_api_server(
        str(config_path),
        "127.0.0.1",
        0,
        home_root=home_root,
        data_root=data_root,
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        status, minted = _http_json_request(
            connection,
            "POST",
            "/v1/client/leases",
            {"X-IPC-Token": "master-token", "Content-Type": "application/json"},
            {
                "schema_version": 1,
                "client": {
                    "kind": "desktop",
                    "version": "test",
                    "protocol_min": 1,
                    "protocol_max": 1,
                },
                "requested_ttl_seconds": 300,
            },
        )
        assert status == 200
        token = str(minted["lease"]["client_token"])
        status, created = _http_json_request(
            connection,
            "POST",
            "/v1/client/sessions",
            {
                "X-OpenMinion-Client-Token": token,
                "Content-Type": "application/json",
            },
            {"title": "Artifact test", "agent_id": "openminion"},
        )
        assert status == 200
        session_id = str(created["session"]["session_id"])
        artifact = server._client_state[2]._artifactctl.ingest_bytes(
            b"open /Users/person/private",
            mime="text/plain",
            original_name="private.txt",
            session_id=session_id,
        )
        session_api = server._runtime.gateway._agent._get_runner().session_api
        session_api.append_turn(
            session_id,
            "user",
            "attached",
            attachments=[artifact.ref],
        )
        requested_id = session_api.append_event(
            session_id,
            type="tool.call.requested",
            payload={
                "schema_version": 1,
                "turn_scope_id": "turn-1",
                "call_id": "call-1",
                "canonical_name": "file.read",
                "sanitized_normalized_arguments": {
                    "path": "/Users/person/must-not-cross"
                },
                "batch_index": 0,
                "depends_on": [],
            },
        )
        session_api.append_event(
            session_id,
            type="tool.call.completed",
            parent_event_id=requested_id,
            payload={
                "schema_version": 1,
                "turn_scope_id": "turn-1",
                "call_id": "call-1",
                "status": "success",
                "output": {
                    "summary": "read complete",
                    "outputs": {"text": "safe tool output"},
                },
            },
        )
        yield SimpleNamespace(
            connection=connection,
            port=server.server_address[1],
            server=server,
            session_id=session_id,
            token=token,
            artifact_ref=artifact.ref,
            capabilities=tuple(minted["lease"]["capabilities"]),
            home_root=home_root,
            data_root=data_root,
        )
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_loopback_artifact_routes_authenticate_and_keep_ids_opaque(
    loopback_artifacts: SimpleNamespace,
) -> None:
    fixture = loopback_artifacts
    headers = {"X-OpenMinion-Client-Token": fixture.token}
    status, catalog = _http_json_request(
        fixture.connection,
        "GET",
        f"/v1/client/sessions/{fixture.session_id}/artifacts?limit=25",
        headers,
        None,
    )
    assert status == 200
    assert "tool_outputs.read.v1" in fixture.capabilities
    assert len(catalog["artifacts"]) == 2
    artifact_item = next(
        item for item in catalog["artifacts"] if item["kind"] == "artifact"
    )
    tool_item = next(
        item for item in catalog["artifacts"] if item["kind"] == "tool_output"
    )
    artifact_id = artifact_item["artifact_id"]
    assert len(artifact_id) == 48

    status, content = _http_json_request(
        fixture.connection,
        "GET",
        f"/v1/client/sessions/{fixture.session_id}/artifacts/{artifact_id}",
        headers,
        None,
    )
    assert status == 200
    assert content["content"]["text"] == "[PATH REDACTED]"

    status, tool_content = _http_json_request(
        fixture.connection,
        "GET",
        f"/v1/client/sessions/{fixture.session_id}/artifacts/{tool_item['artifact_id']}",
        headers,
        None,
    )
    assert status == 200
    assert tool_content["content"]["content_kind"] == "text"
    assert tool_content["content"]["text"] == "safe tool output"

    status, invalid = _http_json_request(
        fixture.connection,
        "GET",
        f"/v1/client/sessions/{fixture.session_id}/artifacts?unexpected=1",
        headers,
        None,
    )
    assert (status, invalid["error"]["code"]) == (400, "invalid_request")

    status, missing_session = _http_json_request(
        fixture.connection,
        "GET",
        "/v1/client/sessions/missing/artifacts",
        headers,
        None,
    )
    assert (status, missing_session["error"]["code"]) == (
        404,
        "session_not_found",
    )

    status, second_mint = _http_json_request(
        fixture.connection,
        "POST",
        "/v1/client/leases",
        {"X-IPC-Token": "master-token", "Content-Type": "application/json"},
        {
            "schema_version": 1,
            "client": {
                "kind": "desktop",
                "version": "test",
                "protocol_min": 1,
                "protocol_max": 1,
            },
            "requested_ttl_seconds": 300,
        },
    )
    assert status == 200
    second_headers = {
        "X-OpenMinion-Client-Token": str(second_mint["lease"]["client_token"])
    }
    status, isolated = _http_json_request(
        fixture.connection,
        "GET",
        f"/v1/client/sessions/{fixture.session_id}/artifacts/{artifact_id}",
        second_headers,
        None,
    )
    assert (status, isolated["error"]["code"]) == (404, "artifact_not_found")

    status, revoked = _http_json_request(
        fixture.connection,
        "DELETE",
        "/v1/client/leases/current",
        headers,
        None,
    )
    assert (status, revoked["revoked"]) == (200, True)
    status, forbidden = _http_json_request(
        fixture.connection,
        "GET",
        f"/v1/client/sessions/{fixture.session_id}/artifacts",
        headers,
        None,
    )
    assert (status, forbidden["error"]["code"]) == (403, "forbidden")

    visible = json.dumps(
        (
            catalog,
            content,
            tool_content,
            invalid,
            missing_session,
            isolated,
            revoked,
            forbidden,
        )
    )
    assert fixture.artifact_ref not in visible
    assert str(fixture.home_root) not in visible
    assert str(fixture.data_root) not in visible
