from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

from openminion.api.server import client_artifacts
from openminion.api.server.client_artifacts import ClientArtifactCoordinator
from openminion.api.server.client_auth import ClientIdentity


_REF = f"artifact://sha256/{'a' * 64}"


class _Auth:
    def is_active(self, identity) -> bool:
        return identity.client_id == "client-1"


class _Artifacts:
    def get(self, artifact_ref):
        assert artifact_ref == _REF
        return SimpleNamespace(
            mime="text/plain",
            size_bytes=24,
            created_at="2026-08-20T00:00:00+00:00",
            label="notes.txt",
            original_name="notes.txt",
            deleted_at=None,
        )

    def open(self, artifact_ref):
        assert artifact_ref == _REF
        return BytesIO(b"open /Users/person/secret")

    def close(self):
        return None


class _CredentialArtifacts(_Artifacts):
    def open(self, artifact_ref):
        assert artifact_ref == _REF
        return BytesIO(b"api_key=secret-value")


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


def test_path_projection_redacts_posix_drive_unc_and_diff_lines() -> None:
    assert client_artifacts._redact_paths("open /Users/person/private") == (
        "[PATH REDACTED]"
    )
    assert client_artifacts._redact_paths(r"open C:\private\file.txt") == (
        "[PATH REDACTED]"
    )
    assert client_artifacts._redact_paths(r"open \\server\share\file.txt") == (
        "[PATH REDACTED]"
    )
    diff = (
        "--- /Users/person/old.txt\n+++ C:\\private\\new.txt\n@@ -1 +1 @@\n-old\n+new\n"
    )
    projected = client_artifacts._redact_paths(diff)
    assert projected.startswith("--- [PATH REDACTED]\n+++ [PATH REDACTED]\n")
    assert "/Users" not in projected
    assert "C:\\private" not in projected
