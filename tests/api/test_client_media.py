from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import pytest

from tests._csc_fixtures import _csc_install_default_agent

from openminion.api.server import build_api_server
from openminion.api.server.client_auth import ClientAuthService, ClientIdentity
from openminion.api.server.client_media import (
    ClientMediaCoordinator,
    ClientMediaError,
    MAX_PENDING_COUNT,
)
from openminion.api.server.streaming import handle_turn_stream_request
from openminion.base.config import OpenMinionConfig, save_config
from openminion.base.version import OPENMINION_VERSION
from openminion.modules.artifact.models import sha_to_ref
from openminion.services.runtime.ingress.requests import build_manager_turn_request
from openminion.services.runtime.manager import TurnResponse


_PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic-image"


class _ArtifactCtl:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.closed = False

    def ingest_bytes(self, data: bytes, **_kwargs: Any) -> Any:
        digest = f"{len(self.values) + 1:064x}"
        ref = sha_to_ref(digest)
        self.values[ref] = data
        return SimpleNamespace(ref=ref)

    def open(self, ref: str) -> Any:
        from io import BytesIO

        return BytesIO(self.values[ref])

    def close(self) -> None:
        self.closed = True


class _Sessions:
    def __init__(self) -> None:
        self.statuses = {"session-1": "active", "session-2": "active"}

    def get_session(self, session_id: str) -> Any:
        status = self.statuses.get(session_id)
        return SimpleNamespace(status=status) if status is not None else None


def _service(tmp_path: Path) -> ClientAuthService:
    return ClientAuthService(
        master_token="master-token",
        config_path=tmp_path / "config.json",
        home_root=tmp_path / "home",
        data_root=tmp_path / "data",
        bind_host="127.0.0.1",
        daemon_version=OPENMINION_VERSION,
    )


def _identity(service: ClientAuthService) -> ClientIdentity:
    lease = service.mint(protocol_min=1, protocol_max=1, ttl_seconds=300)
    identity = service.authorize(
        method="POST",
        path="/v1/client/sessions/session-1/media",
        master_tokens=(),
        client_tokens=(str(lease["client_token"]),),
        peer_host="127.0.0.1",
    )
    assert identity is not None
    return identity


@pytest.fixture
def coordinator(
    tmp_path: Path,
) -> tuple[ClientMediaCoordinator, ClientAuthService, ClientIdentity, _Sessions]:
    service = _service(tmp_path)
    identity = _identity(service)
    sessions = _Sessions()
    owner = ClientMediaCoordinator(
        client_auth=service,
        runtime=SimpleNamespace(sessions=sessions),
        artifactctl=_ArtifactCtl(),
    )
    return owner, service, identity, sessions


def _upload(
    owner: ClientMediaCoordinator,
    identity: ClientIdentity,
    *,
    data: bytes = _PNG,
    mime_type: str = "image/png",
    session_id: str = "session-1",
) -> Any:
    reservation = owner.begin_upload(identity, session_id, len(data))
    try:
        return owner.commit_upload(
            reservation,
            data=data,
            name="image.png",
            mime_type=mime_type,
            request_id="request-1",
        )
    finally:
        owner.abort_upload(reservation)


def test_media_coordinator_binds_same_trace_and_releases_without_ref_leak(
    coordinator: tuple[
        ClientMediaCoordinator,
        ClientAuthService,
        ClientIdentity,
        _Sessions,
    ],
) -> None:
    owner, service, identity, _sessions = coordinator
    record = _upload(owner, identity)
    assert set(record.payload()) == {
        "schema_version",
        "media_id",
        "session_id",
        "name",
        "mime_type",
        "size_bytes",
        "kind",
        "turn_compatible",
        "expires_at",
    }
    assert "artifact" not in json.dumps(record.payload())
    first = owner.resolve_for_turn(identity, "session-1", "trace-1", (record.media_id,))
    assert (
        owner.resolve_for_turn(identity, "session-1", "trace-1", (record.media_id,))
        == first
    )
    with pytest.raises(ClientMediaError, match="already attached") as conflict:
        owner.resolve_for_turn(identity, "session-1", "trace-2", (record.media_id,))
    assert conflict.value.code == "media_already_attached"
    owner.complete_trace(identity, "session-1", "trace-1")
    owner.release(identity, "session-1", record.media_id)
    with pytest.raises(ClientMediaError) as missing:
        owner.open_media(identity, "session-1", record.media_id)
    assert missing.value.code == "media_not_found"
    assert service.is_active(identity)


def test_media_coordinator_quotas_types_expiry_and_lifecycle_fail_closed(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 20, tzinfo=UTC)
    clock = [now]
    service = _service(tmp_path)
    identity = _identity(service)
    other = _identity(service)
    sessions = _Sessions()
    owner = ClientMediaCoordinator(
        client_auth=service,
        runtime=SimpleNamespace(sessions=sessions),
        artifactctl=_ArtifactCtl(),
        now=lambda: clock[0],
    )
    records = [_upload(owner, identity) for _ in range(MAX_PENDING_COUNT)]
    with pytest.raises(ClientMediaError) as full:
        owner.begin_upload(identity, "session-1", len(_PNG))
    assert full.value.code == "media_backpressure"
    owner.release(identity, "session-1", records.pop().media_id)
    text = _upload(owner, identity, data=b"hello", mime_type="text/plain")
    with pytest.raises(ClientMediaError) as unsupported:
        owner.resolve_for_turn(identity, "session-1", "trace-1", (text.media_id,))
    assert unsupported.value.code == "unsupported_media_type"
    with pytest.raises(ClientMediaError) as hidden:
        owner.open_media(other, "session-1", records[0].media_id)
    assert hidden.value.code == "media_not_found"
    clock[0] += timedelta(minutes=16)
    with pytest.raises(ClientMediaError) as expired:
        owner.open_media(identity, "session-1", records[0].media_id)
    assert expired.value.code == "media_expired"
    sessions.statuses["session-2"] = "closed"
    with pytest.raises(ClientMediaError) as closed:
        owner.begin_upload(identity, "session-2", len(_PNG))
    assert closed.value.code == "session_closed"
    owner.revoke_client(identity)
    with pytest.raises(ClientMediaError) as revoked:
        owner.open_media(identity, "session-1", records[1].media_id)
    assert revoked.value.code == "forbidden"


def test_media_coordinator_fences_close_restart_and_active_backpressure(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    identity = _identity(service)
    sessions = _Sessions()
    artifacts = _ArtifactCtl()
    owner = ClientMediaCoordinator(
        client_auth=service,
        runtime=SimpleNamespace(sessions=sessions),
        artifactctl=artifacts,
    )
    reservation = owner.begin_upload(identity, "session-1", len(_PNG))
    with pytest.raises(ClientMediaError) as concurrent:
        owner.begin_upload(identity, "session-2", len(_PNG))
    assert concurrent.value.code == "media_backpressure"
    owner.abort_upload(reservation)
    with pytest.raises(ClientMediaError) as spoofed:
        _upload(owner, identity, data=_PNG, mime_type="image/jpeg")
    assert spoofed.value.code == "unsupported_media_type"
    with pytest.raises(ClientMediaError) as oversized:
        owner.begin_upload(identity, "session-1", 10 * 1024 * 1024 + 1)
    assert oversized.value.code == "media_too_large"

    record = _upload(owner, identity)
    finish_close = owner.cancel_session("session-1", "session_closed")
    with pytest.raises(ClientMediaError) as hidden:
        owner.open_media(identity, "session-1", record.media_id)
    assert hidden.value.code == "media_not_found"
    with pytest.raises(ClientMediaError) as fenced:
        owner.begin_upload(identity, "session-1", len(_PNG))
    assert fenced.value.code == "session_closed"
    finish_close()
    replacement = _upload(owner, identity)
    restarted = ClientMediaCoordinator(
        client_auth=service,
        runtime=SimpleNamespace(sessions=sessions),
        artifactctl=artifacts,
    )
    with pytest.raises(ClientMediaError) as lost:
        restarted.open_media(identity, "session-1", replacement.media_id)
    assert lost.value.code == "media_not_found"

    for index in range(16):
        active = _upload(owner, identity)
        owner.resolve_for_turn(
            identity,
            "session-1",
            f"trace-{index}",
            (active.media_id,),
        )
    blocked = _upload(owner, identity)
    with pytest.raises(ClientMediaError) as active_full:
        owner.resolve_for_turn(
            identity, "session-1", "trace-overflow", (blocked.media_id,)
        )
    assert active_full.value.code == "media_backpressure"


def test_only_trusted_resolved_attachment_refs_reach_manager_request() -> None:
    public = build_manager_turn_request(
        {
            "session_id": "session-1",
            "input_text": "hello",
            "attachments": ["/private/image.png"],
        },
        default_agent_id="openminion",
    )
    assert public.attachments == []
    trusted = build_manager_turn_request(
        {"session_id": "session-1", "input_text": "hello"},
        default_agent_id="openminion",
        resolved_attachment_refs=(sha_to_ref("a" * 64),),
    )
    assert trusted.attachments == [sha_to_ref("a" * 64)]


def test_stream_binds_before_submit_and_completes_from_handle_callback() -> None:
    order: list[str] = []
    media_id = "a" * 48
    artifact_ref = sha_to_ref("b" * 64)

    class _Media:
        def resolve_for_turn(self, *_args: Any) -> tuple[str, ...]:
            order.append("resolve")
            return (artifact_ref,)

        def complete_trace(self, *_args: Any) -> None:
            order.append("complete")

    class _Handle:
        trace_id = "11111111-1111-4111-8111-111111111111"

        def __init__(self) -> None:
            self.callback: Any = None
            self.request = SimpleNamespace(
                session_id="session-1",
                trace_id="11111111-1111-4111-8111-111111111111",
            )
            self.timeout_s = 1.0

        def add_done_callback(self, callback: Any) -> None:
            order.append("callback")
            self.callback = callback

        def stream(self, timeout_s: float | None = None) -> Any:
            del timeout_s
            return iter(())

        def result(self, timeout_s: float | None = None) -> TurnResponse:
            del timeout_s
            assert self.callback is not None
            self.callback()
            return TurnResponse(final_text="ok")

        def cancel(self) -> bool:
            return True

    class _Runtime:
        def submit_turn(self, **kwargs: Any) -> Any:
            order.append("submit")
            assert kwargs["resolved_attachment_refs"] == (artifact_ref,)
            assert "attachments" not in kwargs["payload"]
            return _Handle()

    body = _desktop_turn_body([media_id])
    handle_turn_stream_request(
        body=body,
        request_id=body["trace_id"],
        config_path=None,
        runtime=_Runtime(),  # type: ignore[arg-type]
        start_sse_response=lambda: order.append("sse"),
        write_sse_event=lambda **_kwargs: None,
        write_json=lambda *_args: None,
        observe_request_metrics=lambda **_kwargs: 0,
        log_request_done=lambda **_kwargs: None,
        perf_counter=lambda: 0.0,
        desktop_client=True,
        client_media=_Media(),  # type: ignore[arg-type]
        client_identity=SimpleNamespace(client_id="client-1"),
    )
    assert order[:4] == ["resolve", "submit", "callback", "sse"]
    assert order.count("complete") == 1


def test_stream_unbinds_media_when_submission_fails_before_sse() -> None:
    calls: list[str] = []

    class _Media:
        def resolve_for_turn(self, *_args: Any) -> tuple[str, ...]:
            return (sha_to_ref("b" * 64),)

        def unbind_before_start(self, *_args: Any) -> None:
            calls.append("unbind")

    class _Runtime:
        def submit_turn(self, **_kwargs: Any) -> Any:
            raise ValueError("submission rejected")

    body = _desktop_turn_body(["a" * 48])
    handle_turn_stream_request(
        body=body,
        request_id=body["trace_id"],
        config_path=None,
        runtime=_Runtime(),  # type: ignore[arg-type]
        start_sse_response=lambda: calls.append("sse"),
        write_sse_event=lambda **_kwargs: None,
        write_json=lambda *_args: calls.append("json"),
        observe_request_metrics=lambda **_kwargs: 0,
        log_request_done=lambda **_kwargs: None,
        perf_counter=lambda: 0.0,
        desktop_client=True,
        client_media=_Media(),  # type: ignore[arg-type]
        client_identity=SimpleNamespace(client_id="client-1"),
    )
    assert calls == ["unbind", "json"]


def _desktop_turn_body(attachments: list[str]) -> dict[str, Any]:
    return {
        "trace_id": "11111111-1111-4111-8111-111111111111",
        "session_id": "session-1",
        "agent_id": "openminion",
        "input_text": "hello",
        "mode": "oneshot",
        "stream": True,
        "channel": "console",
        "user": "api-user",
        "idempotency_key": "22222222-2222-4222-8222-222222222222",
        "attachments": attachments,
    }


def test_loopback_media_upload_read_release_uses_opaque_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    config_path = home_root / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")  # type: ignore[attr-defined]
    config.runtime.ipc_token = "master-token"
    config.storage.path = str(data_root / "state" / "api.db")
    home_root.mkdir(parents=True)
    monkeypatch.setenv("OPENMINION_HOME", str(home_root))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
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
        status, minted = _json_request(
            connection,
            "POST",
            "/v1/client/leases",
            {
                "X-IPC-Token": "master-token",
                "Content-Type": "application/json",
            },
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
        status, created = _json_request(
            connection,
            "POST",
            "/v1/client/sessions",
            {
                "X-OpenMinion-Client-Token": token,
                "Content-Type": "application/json",
            },
            {"title": "Media test", "agent_id": "openminion"},
        )
        assert status == 200
        session_id = str(created["session"]["session_id"])
        connection.close()
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request(
            "POST",
            f"/v1/client/sessions/{session_id}/media",
            body=_PNG,
            headers={
                "X-OpenMinion-Client-Token": token,
                "X-Request-ID": "upload-1",
                "X-OpenMinion-Media-Name": quote("image.png"),
                "Content-Type": "image/png",
                "Content-Length": str(len(_PNG)),
            },
        )
        response = connection.getresponse()
        uploaded = json.loads(response.read())
        assert response.status == 201
        assert response.getheader("Connection") == "close"
        assert "artifact" not in json.dumps(uploaded)
        media_id = str(uploaded["media"]["media_id"])
        assert len(media_id) == 48
        connection.close()
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request(
            "GET",
            f"/v1/client/sessions/{session_id}/media/{media_id}",
            headers={"X-OpenMinion-Client-Token": token},
        )
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("Content-Type") == "image/png"
        assert response.read() == _PNG
        status, released = _json_request(
            connection,
            "DELETE",
            f"/v1/client/sessions/{session_id}/media/{media_id}",
            {"X-OpenMinion-Client-Token": token},
            None,
        )
        assert status == 200
        assert released == {
            "ok": True,
            "released": True,
            "media_id": media_id,
            "meta": released["meta"],
        }
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _json_request(
    connection: HTTPConnection,
    method: str,
    path: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode() if payload is not None else None
    request_headers = dict(headers)
    if body is not None:
        request_headers.setdefault("Content-Length", str(len(body)))
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    return response.status, json.loads(response.read())


@pytest.mark.skip(reason="ODM-04 owns the complete provider payload fixture")
def test_desktop_image_media_reaches_provider_payload() -> None:
    pass


@pytest.mark.skip(reason="ODM-04 owns the complete fail-closed integration matrix")
def test_desktop_media_fail_closed_matrix() -> None:
    pass
