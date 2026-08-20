from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.api.server.client_approvals import ClientApprovalCoordinator
from openminion.api.server.client_auth import ClientAuthService, ClientIdentity
from openminion.api.server.dispatch import dispatch_request
from openminion.base.version import OPENMINION_VERSION
from openminion.services.runtime.manager import DesktopApprovalRequest


class _Sessions:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.fail_event: str | None = None

    def append_event(self, **event: Any) -> str:
        if event["event_type"] == self.fail_event:
            raise RuntimeError("event write failed")
        self.events.append(event)
        return f"event-{len(self.events)}"


@pytest.fixture
def approval_owner(
    tmp_path: Path,
) -> tuple[
    ClientAuthService,
    ClientApprovalCoordinator,
    _Sessions,
    ClientIdentity,
]:
    service = ClientAuthService(
        master_token="master-token",
        config_path=tmp_path / "config.json",
        home_root=tmp_path / "home",
        data_root=tmp_path / "data",
        bind_host="127.0.0.1",
        daemon_version=OPENMINION_VERSION,
    )
    sessions = _Sessions()
    coordinator = ClientApprovalCoordinator(
        client_auth=service,
        runtime=SimpleNamespace(sessions=sessions),
    )
    identity = _identity(service, ttl_seconds=300)
    return service, coordinator, sessions, identity


def _identity(service: ClientAuthService, *, ttl_seconds: int) -> ClientIdentity:
    lease = service.mint(
        protocol_min=1,
        protocol_max=1,
        ttl_seconds=ttl_seconds,
    )
    identity = service.authorize(
        method="POST",
        path="/v1/client/sessions/session-1/turns/trace-1/approvals/approval-1",
        master_tokens=(),
        client_tokens=(str(lease["client_token"]),),
        peer_host="127.0.0.1",
    )
    assert identity is not None
    return identity


def _start_request(
    coordinator: ClientApprovalCoordinator,
    identity: ClientIdentity,
    *,
    session_id: str = "session-1",
    trace_id: str = "trace-1",
    call_id: str = "call-1",
    argument_keys: tuple[str, ...] = ("path", "query"),
) -> tuple[Thread, Event, list[Any], list[bool], str]:
    cancel = Event()
    chunks: list[Any] = []
    result: list[bool] = []
    request = DesktopApprovalRequest(
        session_id=session_id,
        trace_id=trace_id,
        tool_name="workspace.search",
        call_id=call_id,
        argument_keys=argument_keys,
        emit_chunk=chunks.append,
        cancel_event=cancel,
    )
    thread = Thread(
        target=lambda: result.append(coordinator.request(identity, request)),
        daemon=True,
    )
    thread.start()
    _wait_for(lambda: bool(chunks))
    approval_id = chunks[0].data["approval_id"]
    return thread, cancel, chunks, result, approval_id


def _wait_for(predicate: Any, timeout: float = 2.0) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.01)
    raise AssertionError("timed out waiting for approval state")


def _decide(
    coordinator: ClientApprovalCoordinator,
    service: ClientAuthService,
    identity: ClientIdentity,
    approval_id: str,
    decision: str,
) -> tuple[int, dict[str, Any]]:
    status, payload = dispatch_request(
        "POST",
        f"/v1/client/sessions/session-1/turns/trace-1/approvals/{approval_id}",
        None,
        body={"decision": decision},
        client_auth=service,
        client_identity=identity,
        client_approvals=coordinator,
        request_id="approval-request",
    )
    return int(status), payload


@pytest.mark.parametrize(
    ("decision", "outcome", "repeated", "allowed"),
    [
        ("allow_once", "applied", "already_applied", True),
        ("deny", "denied", "already_denied", False),
    ],
)
def test_decision_route_applies_once_and_repeats_deterministically(
    approval_owner: tuple[
        ClientAuthService,
        ClientApprovalCoordinator,
        _Sessions,
        ClientIdentity,
    ],
    decision: str,
    outcome: str,
    repeated: str,
    allowed: bool,
) -> None:
    service, coordinator, sessions, identity = approval_owner
    thread, _cancel, chunks, result, approval_id = _start_request(coordinator, identity)

    status, payload = _decide(coordinator, service, identity, approval_id, decision)
    assert status == 200
    assert payload["approval"]["outcome"] == outcome
    thread.join(timeout=2)
    assert result == [allowed]
    assert [chunk.kind for chunk in chunks] == [
        "approval_required",
        "approval_resolved",
    ]
    assert chunks[1].data["decision"] == decision
    assert [event["event_type"] for event in sessions.events] == [
        "desktop.approval.requested",
        "desktop.approval.resolved",
    ]

    status, payload = _decide(coordinator, service, identity, approval_id, decision)
    assert status == 200
    assert payload["approval"]["outcome"] == repeated
    other = "deny" if decision == "allow_once" else "allow_once"
    status, payload = _decide(coordinator, service, identity, approval_id, other)
    assert status == 409
    assert payload["error"]["code"] == "approval_already_resolved"


def test_wrong_identity_cancel_and_expiry_fail_closed(
    approval_owner: tuple[
        ClientAuthService,
        ClientApprovalCoordinator,
        _Sessions,
        ClientIdentity,
    ],
) -> None:
    service, coordinator, _sessions, identity = approval_owner
    other = _identity(service, ttl_seconds=300)
    thread, cancel, _chunks, result, approval_id = _start_request(coordinator, identity)
    status, payload = _decide(coordinator, service, other, approval_id, "allow_once")
    assert status == 404
    assert payload["error"]["code"] == "approval_not_found"
    cancel.set()
    thread.join(timeout=2)
    assert result == [False]
    status, payload = _decide(coordinator, service, identity, approval_id, "allow_once")
    assert status == 409
    assert payload["error"]["code"] == "approval_cancelled"

    thread, _cancel, _chunks, result, approval_id = _start_request(
        coordinator,
        identity,
        trace_id="trace-deadline",
        call_id="call-deadline",
    )
    with coordinator._lock:
        record = next(
            record
            for record in coordinator._active.values()
            if record.approval_id == approval_id
        )
        record.expires_at = (
            (datetime.now(UTC) - timedelta(seconds=1))
            .isoformat()
            .replace("+00:00", "Z")
        )
    thread.join(timeout=2)
    assert result == [False]
    status, payload = dispatch_request(
        "POST",
        f"/v1/client/sessions/session-1/turns/trace-deadline/approvals/{approval_id}",
        None,
        body={"decision": "allow_once"},
        client_auth=service,
        client_identity=identity,
        client_approvals=coordinator,
    )
    assert int(status) == 410, payload
    assert payload["error"]["code"] == "approval_expired"

    thread, _cancel, _chunks, result, approval_id = _start_request(
        coordinator,
        identity,
        trace_id="trace-lease",
        call_id="call-lease",
    )
    service._leases[identity.client_id].expires_at = datetime.now(UTC) - timedelta(
        seconds=1
    )
    thread.join(timeout=2)
    assert result == [False]
    status, payload = dispatch_request(
        "POST",
        f"/v1/client/sessions/session-1/turns/trace-lease/approvals/{approval_id}",
        None,
        body={"decision": "allow_once"},
        client_auth=service,
        client_identity=identity,
        client_approvals=coordinator,
    )
    assert int(status) == 410, payload
    assert payload["error"]["code"] == "approval_expired"


def test_shared_session_close_and_lease_revoke_release_waits(
    approval_owner: tuple[
        ClientAuthService,
        ClientApprovalCoordinator,
        _Sessions,
        ClientIdentity,
    ],
) -> None:
    service, coordinator, _sessions, identity = approval_owner
    other = _identity(service, ttl_seconds=300)
    first = _start_request(coordinator, identity, trace_id="trace-a", call_id="call-a")
    second = _start_request(coordinator, other, trace_id="trace-b", call_id="call-b")
    coordinator.cancel_session("session-1", "session_closed")
    first[0].join(timeout=2)
    second[0].join(timeout=2)
    assert first[3] == [False]
    assert second[3] == [False]

    third = _start_request(
        coordinator,
        identity,
        session_id="session-2",
        trace_id="trace-c",
        call_id="call-c",
    )
    service.revoke(identity)
    coordinator.cancel_client(identity.client_id, "revoked")
    third[0].join(timeout=2)
    assert third[3] == [False]


def test_event_failures_and_argument_bounds_never_allow(
    approval_owner: tuple[
        ClientAuthService,
        ClientApprovalCoordinator,
        _Sessions,
        ClientIdentity,
    ],
) -> None:
    service, coordinator, sessions, identity = approval_owner
    sessions.fail_event = "desktop.approval.requested"
    chunks: list[Any] = []
    result = coordinator.request(
        identity,
        DesktopApprovalRequest(
            session_id="session-1",
            trace_id="trace-request-fail",
            tool_name="workspace.search",
            call_id="call-request-fail",
            argument_keys=("query",),
            emit_chunk=chunks.append,
            cancel_event=Event(),
        ),
    )
    assert result is False
    assert chunks == []
    assert coordinator._active == {}

    sessions.fail_event = "desktop.approval.resolved"
    thread, _cancel, chunks, result, approval_id = _start_request(
        coordinator,
        identity,
        trace_id="trace-resolve-fail",
        call_id="call-resolve-fail",
    )
    status, payload = dispatch_request(
        "POST",
        f"/v1/client/sessions/session-1/turns/trace-resolve-fail/approvals/{approval_id}",
        None,
        body={"decision": "allow_once"},
        client_auth=service,
        client_identity=identity,
        client_approvals=coordinator,
    )
    assert int(status) == 500
    assert payload["error"]["code"] == "approval_event_failed"
    repeated_status, repeated_payload = dispatch_request(
        "POST",
        f"/v1/client/sessions/session-1/turns/trace-resolve-fail/approvals/{approval_id}",
        None,
        body={"decision": "allow_once"},
        client_auth=service,
        client_identity=identity,
        client_approvals=coordinator,
    )
    assert int(repeated_status) == 500
    assert repeated_payload["error"]["code"] == "approval_event_failed"
    thread.join(timeout=2)
    assert result == [False]
    assert [chunk.kind for chunk in chunks] == ["approval_required"]

    oversized = tuple(f"{index:02d}-{'x' * 195}" for index in range(64))
    assert (
        coordinator.request(
            identity,
            DesktopApprovalRequest(
                session_id="session-1",
                trace_id="trace-oversized",
                tool_name="workspace.search",
                call_id="call-oversized",
                argument_keys=oversized,
                emit_chunk=lambda _chunk: pytest.fail("unexpected live frame"),
                cancel_event=Event(),
            ),
        )
        is False
    )


def test_recovery_and_shutdown_release_are_deterministic(
    approval_owner: tuple[
        ClientAuthService,
        ClientApprovalCoordinator,
        _Sessions,
        ClientIdentity,
    ],
) -> None:
    _service, coordinator, _sessions, identity = approval_owner
    thread, _cancel, chunks, result, approval_id = _start_request(
        coordinator,
        identity,
        trace_id="trace-recovery",
        call_id="call-recovery",
    )
    expires_at = chunks[0].data["expires_at"]
    assert (
        coordinator.recovery_outcome(
            identity.client_id,
            "session-1",
            "trace-recovery",
            approval_id,
            expires_at,
        )
        == "pending"
    )
    assert (
        coordinator.recovery_outcome(
            "wrong-client",
            "session-1",
            "trace-recovery",
            approval_id,
            expires_at,
        )
        == "interrupted"
    )
    coordinator.close("shutdown")
    thread.join(timeout=2)
    assert result == [False]
    assert (
        coordinator.recovery_outcome(
            identity.client_id,
            "session-1",
            "trace-recovery",
            approval_id,
            expires_at,
        )
        == "interrupted"
    )
    coordinator.close("shutdown")


@pytest.mark.parametrize(
    ("path", "body", "query"),
    [
        (
            "/v1/client/sessions/session-1/turns/trace-1/approvals/bad%2Fid",
            {"decision": "deny"},
            None,
        ),
        (
            "/v1/client/sessions/session-1/turns/trace-1/approvals/missing",
            {"decision": "allow_forever"},
            None,
        ),
        (
            "/v1/client/sessions/session-1/turns/trace-1/approvals/missing",
            {"decision": "deny", "extra": True},
            None,
        ),
        (
            "/v1/client/sessions/session-1/turns/trace-1/approvals/missing",
            {"decision": "deny"},
            "unexpected=1",
        ),
    ],
)
def test_decision_route_rejects_malformed_requests(
    approval_owner: tuple[
        ClientAuthService,
        ClientApprovalCoordinator,
        _Sessions,
        ClientIdentity,
    ],
    path: str,
    body: dict[str, Any],
    query: str | None,
) -> None:
    service, coordinator, _sessions, identity = approval_owner
    status, payload = dispatch_request(
        "POST",
        path,
        None,
        body=body,
        query=query,
        client_auth=service,
        client_identity=identity,
        client_approvals=coordinator,
    )
    assert int(status) == 400
    assert payload["error"]["code"] == "invalid_request"


def test_desktop_approval_http_stream_session_event_matrix() -> None:
    pytest.skip("ODA-03 exact real-owner fixture opens after ODA-01 and ODA-02 review")
