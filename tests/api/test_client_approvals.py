from __future__ import annotations

import asyncio
from http.client import HTTPConnection
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread, Timer
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from tests._csc_fixtures import _csc_install_default_agent

from openminion.api.server import build_api_server
from openminion.api.server.client_approvals import ClientApprovalCoordinator
from openminion.api.server.client_auth import ClientAuthService, ClientIdentity
from openminion.api.server.dispatch import dispatch_request
from openminion.base.version import OPENMINION_VERSION
from openminion.base.config import OpenMinionConfig, save_config
from openminion.modules.storage.runtime.session_store.models import SessionRecord
from openminion.services.runtime.daemon import execute_turn
from openminion.services.runtime.ingress import TurnTimeoutError
from openminion.services.runtime.ingress.gateway_call import _run_coro_sync
from openminion.services.runtime.manager import (
    AgentRuntimeManager,
    DesktopApprovalRequest,
    TurnChunk,
    TurnResponse,
)


class _Sessions:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.fail_event: str | None = None
        self.statuses = {"session-1": "active", "session-2": "active"}
        self.append_started = Event()
        self.release_append: Event | None = None
        self.block_event: str | None = None
        self.block_release: Event | None = None

    def append_event(self, **event: Any) -> str:
        if event["event_type"] == self.fail_event:
            raise RuntimeError("event write failed")
        if (
            event["event_type"] == "desktop.approval.requested"
            and self.release_append is not None
        ):
            self.append_started.set()
            self.release_append.wait(timeout=2)
        if event["event_type"] == self.block_event:
            self.append_started.set()
            assert self.block_release is not None
            self.block_release.wait(timeout=2)
        self.events.append(event)
        return f"event-{len(self.events)}"

    def get_session(self, session_id: str) -> SessionRecord | None:
        status = self.statuses.get(session_id)
        if status is None:
            return None
        return SessionRecord(
            id=session_id,
            session_key="room|agent:openminion",
            channel="console",
            target="api-user",
            metadata={},
            created_at="2026-08-20T00:00:00Z",
            updated_at="2026-08-20T00:00:00Z",
            status=status,
            last_activity_at="2026-08-20T00:00:00Z",
            closed_at=None,
            expires_at=None,
            active_agent_id="openminion",
        )


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
    with coordinator._lock:
        cancel.set()
        status, payload = _decide(
            coordinator, service, identity, approval_id, "allow_once"
        )
    assert status == 409
    assert payload["error"]["code"] == "approval_cancelled"
    thread.join(timeout=2)
    assert result == [False]

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
    finish_close = coordinator.cancel_session("session-1", "session_closed")
    _sessions.statuses["session-1"] = "closed"
    finish_close()
    first[0].join(timeout=2)
    second[0].join(timeout=2)
    assert first[3] == [False]
    assert second[3] == [False]

    events_after_close = len(_sessions.events)
    assert (
        coordinator.request(
            identity,
            DesktopApprovalRequest(
                session_id="session-1",
                trace_id="trace-after-close",
                tool_name="workspace.search",
                call_id="call-after-close",
                argument_keys=("query",),
                emit_chunk=lambda _chunk: pytest.fail("unexpected live frame"),
                cancel_event=Event(),
            ),
        )
        is False
    )
    assert len(_sessions.events) == events_after_close

    _sessions.statuses["session-1"] = "active"
    resumed = _start_request(
        coordinator,
        identity,
        trace_id="trace-after-resume",
        call_id="call-after-resume",
    )
    resumed[1].set()
    resumed[0].join(timeout=2)
    assert resumed[3] == [False]

    third = _start_request(
        coordinator,
        identity,
        session_id="session-2",
        trace_id="trace-c",
        call_id="call-c",
    )
    coordinator.revoke_client(identity)
    third[0].join(timeout=2)
    assert third[3] == [False]


def test_revoke_during_requested_event_append_is_cancelled(
    approval_owner: tuple[
        ClientAuthService,
        ClientApprovalCoordinator,
        _Sessions,
        ClientIdentity,
    ],
) -> None:
    service, coordinator, sessions, identity = approval_owner
    sessions.release_append = Event()
    result: list[bool] = []
    thread = Thread(
        target=lambda: result.append(
            coordinator.request(
                identity,
                DesktopApprovalRequest(
                    session_id="session-1",
                    trace_id="trace-1",
                    tool_name="workspace.search",
                    call_id="call-1",
                    argument_keys=("query",),
                    emit_chunk=lambda _chunk: pytest.fail("unexpected live frame"),
                    cancel_event=Event(),
                ),
            )
        ),
        daemon=True,
    )
    thread.start()
    assert sessions.append_started.wait(timeout=2)
    coordinator.revoke_client(identity)
    sessions.append_started.clear()
    sessions.block_event = "desktop.approval.resolved"
    sessions.block_release = Event()
    sessions.release_append.set()
    assert sessions.append_started.wait(timeout=2)
    approval_id = sessions.events[0]["payload"]["approval_id"]
    decision: list[tuple[int, dict[str, Any]]] = []
    decision_thread = Thread(
        target=lambda: decision.append(
            _decide(coordinator, service, identity, approval_id, "allow_once")
        ),
        daemon=True,
    )
    decision_thread.start()
    sleep(0.02)
    assert decision_thread.is_alive()
    sessions.block_release.set()
    thread.join(timeout=2)
    decision_thread.join(timeout=2)

    assert result == [False]
    assert decision[0][0] == 409
    assert decision[0][1]["error"]["code"] == "approval_cancelled"
    assert [event["event_type"] for event in sessions.events] == [
        "desktop.approval.requested",
        "desktop.approval.resolved",
    ]
    assert sessions.events[-1]["payload"]["outcome"] == "cancelled"


def test_failed_session_close_cannot_release_captured_admission_fence(
    approval_owner: tuple[
        ClientAuthService,
        ClientApprovalCoordinator,
        _Sessions,
        ClientIdentity,
    ],
) -> None:
    service, coordinator, sessions, identity = approval_owner
    sessions.release_append = Event()
    result: list[bool] = []
    request_thread = Thread(
        target=lambda: result.append(
            coordinator.request(
                identity,
                DesktopApprovalRequest(
                    session_id="session-1",
                    trace_id="trace-1",
                    tool_name="workspace.search",
                    call_id="call-1",
                    argument_keys=("query",),
                    emit_chunk=lambda _chunk: pytest.fail("unexpected live frame"),
                    cancel_event=Event(),
                ),
            )
        ),
        daemon=True,
    )
    request_thread.start()
    assert sessions.append_started.wait(timeout=2)
    finish_failed_close = coordinator.cancel_session("session-1", "session_closed")
    sessions.append_started.clear()
    sessions.block_event = "desktop.approval.resolved"
    sessions.block_release = Event()
    sessions.release_append.set()
    assert sessions.append_started.wait(timeout=2)
    approval_id = sessions.events[0]["payload"]["approval_id"]

    cleanup_thread = Thread(target=finish_failed_close, daemon=True)
    decision: list[tuple[int, dict[str, Any]]] = []
    decision_thread = Thread(
        target=lambda: decision.append(
            _decide(coordinator, service, identity, approval_id, "allow_once")
        ),
        daemon=True,
    )
    cleanup_thread.start()
    decision_thread.start()
    sleep(0.02)
    assert cleanup_thread.is_alive()
    assert decision_thread.is_alive()
    sessions.block_release.set()
    request_thread.join(timeout=2)
    cleanup_thread.join(timeout=2)
    decision_thread.join(timeout=2)

    assert result == [False]
    assert decision[0][0] == 409
    assert decision[0][1]["error"]["code"] == "approval_cancelled"
    assert sessions.events[-1]["payload"]["outcome"] == "cancelled"


def test_overlapping_session_close_keeps_fence_until_every_close_finishes(
    approval_owner: tuple[
        ClientAuthService,
        ClientApprovalCoordinator,
        _Sessions,
        ClientIdentity,
    ],
) -> None:
    _service, coordinator, sessions, identity = approval_owner
    failed_close = coordinator.cancel_session("session-1", "session_closed")
    active_close = coordinator.cancel_session("session-1", "session_closed")
    failed_close()
    event_count = len(sessions.events)

    assert (
        coordinator.request(
            identity,
            DesktopApprovalRequest(
                session_id="session-1",
                trace_id="trace-between-closes",
                tool_name="workspace.search",
                call_id="call-between-closes",
                argument_keys=("query",),
                emit_chunk=lambda _chunk: pytest.fail("unexpected live frame"),
                cancel_event=Event(),
            ),
        )
        is False
    )
    assert len(sessions.events) == event_count

    sessions.statuses["session-1"] = "closed"
    active_close()
    assert (
        coordinator.request(
            identity,
            DesktopApprovalRequest(
                session_id="session-1",
                trace_id="trace-after-close",
                tool_name="workspace.search",
                call_id="call-after-close",
                argument_keys=("query",),
                emit_chunk=lambda _chunk: pytest.fail("unexpected live frame"),
                cancel_event=Event(),
            ),
        )
        is False
    )


def test_turn_timeout_releases_approval_and_rejects_late_allow(
    approval_owner: tuple[
        ClientAuthService,
        ClientApprovalCoordinator,
        _Sessions,
        ClientIdentity,
    ],
) -> None:
    service, coordinator, sessions, identity = approval_owner
    cancel = Event()
    chunks: list[Any] = []
    request = SimpleNamespace(
        meta={},
        session_id="session-1",
        trace_id="trace-timeout",
        desktop_approval_requester=coordinator.bind(identity),
    )

    def time_out(*, approval_callback: Any, **_kwargs: Any) -> None:
        async def await_approval() -> bool:
            return await asyncio.wait_for(
                approval_callback(
                    "workspace.search", {"query": "secret"}, "call-timeout"
                ),
                timeout=0.03,
            )

        try:
            _run_coro_sync(await_approval, timeout=0.03)
        except TimeoutError as exc:
            raise TurnTimeoutError("turn timed out") from exc
        raise AssertionError("approval unexpectedly completed before timeout")

    guard = Timer(1, cancel.set)
    guard.start()
    started = monotonic()
    with mock.patch(
        "openminion.services.runtime.daemon._execute_runtime_turn_with_timer",
        side_effect=time_out,
    ):
        response = execute_turn(
            runtime=SimpleNamespace(),
            request=request,
            emit_chunk=chunks.append,
            cancel_event=cancel,
        )
    elapsed = monotonic() - started
    guard.cancel()

    approval_id = sessions.events[0]["payload"]["approval_id"]
    status, payload = dispatch_request(
        "POST",
        f"/v1/client/sessions/session-1/turns/trace-timeout/approvals/{approval_id}",
        None,
        body={"decision": "allow_once"},
        client_auth=service,
        client_identity=identity,
        client_approvals=coordinator,
    )
    assert int(status) == 409
    assert payload["error"]["code"] == "approval_cancelled"
    _wait_for(
        lambda: (
            bool(sessions.events)
            and sessions.events[-1]["event_type"] == "desktop.approval.resolved"
        )
    )
    assert cancel.is_set()
    assert elapsed < 0.5
    assert response.errors[0].code == "turn_timeout"
    assert sessions.events[-1]["payload"]["outcome"] == "cancelled"


@pytest.mark.parametrize("cancel_owner", ["trace", "session", "shutdown"])
def test_cancellation_transition_serializes_before_decision(
    approval_owner: tuple[
        ClientAuthService,
        ClientApprovalCoordinator,
        _Sessions,
        ClientIdentity,
    ],
    cancel_owner: str,
) -> None:
    service, coordinator, sessions, identity = approval_owner
    request_thread, _cancel, _chunks, result, approval_id = _start_request(
        coordinator, identity
    )
    sessions.append_started.clear()
    sessions.block_event = "desktop.approval.resolved"
    sessions.block_release = Event()

    def cancel() -> None:
        if cancel_owner == "trace":
            coordinator.cancel_trace(
                identity.client_id, "session-1", "trace-1", "cancelled"
            )
        elif cancel_owner == "session":
            coordinator.cancel_session("session-1", "session_closed")
        else:
            coordinator.close("shutdown")

    cancel_thread = Thread(target=cancel, daemon=True)
    cancel_thread.start()
    assert sessions.append_started.wait(timeout=2)
    decision: list[tuple[int, dict[str, Any]]] = []
    decision_thread = Thread(
        target=lambda: decision.append(
            _decide(coordinator, service, identity, approval_id, "allow_once")
        ),
        daemon=True,
    )
    decision_thread.start()
    sleep(0.02)
    assert decision_thread.is_alive()
    sessions.block_release.set()
    cancel_thread.join(timeout=2)
    decision_thread.join(timeout=2)
    request_thread.join(timeout=2)

    assert result == [False]
    assert decision[0][0] == 409
    assert decision[0][1]["error"]["code"] == "approval_cancelled"
    assert sessions.events[-1]["payload"]["outcome"] == (
        "interrupted" if cancel_owner == "shutdown" else "cancelled"
    )


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


def test_desktop_approval_http_stream_session_event_matrix(tmp_path: Path) -> None:
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")  # type: ignore[attr-defined]
    config.runtime.log_level = "ERROR"
    config.runtime.ipc_token = "synthetic-master-token"
    config.storage.path = str(data_root / "state" / "api.db")
    save_config(config, str(config_path))
    server = build_api_server(
        str(config_path),
        "127.0.0.1",
        0,
        home_root=home_root,
        data_root=data_root,
    )
    executions: list[tuple[str, str]] = []

    def executor(request, emit_chunk, cancel_event):  # noqa: ANN001
        emit_chunk(
            TurnChunk(
                trace_id=request.trace_id,
                kind="tool_started",
                data={
                    "tool_name": "workspace.search",
                    "call_id": f"call-{request.trace_id}",
                    "state": "started",
                },
            )
        )
        requester = request.desktop_approval_requester
        allowed = bool(
            requester
            and requester(
                DesktopApprovalRequest(
                    session_id=request.session_id,
                    trace_id=request.trace_id,
                    tool_name="workspace.search",
                    call_id=f"call-{request.trace_id}",
                    argument_keys=("path", "query"),
                    emit_chunk=emit_chunk,
                    cancel_event=cancel_event,
                )
            )
        )
        if allowed:
            executions.append((request.trace_id, f"call-{request.trace_id}"))
        emit_chunk(
            TurnChunk(
                trace_id=request.trace_id,
                kind="tool_completed",
                data={
                    "tool_name": "workspace.search",
                    "call_id": f"call-{request.trace_id}",
                    "state": "completed" if allowed else "denied",
                    "ok": allowed,
                    "duration_ms": 1,
                    "exit_code": None,
                },
            )
        )
        return TurnResponse(final_text="allowed" if allowed else "denied")

    manager = AgentRuntimeManager(turn_executor=executor)
    manager.start()
    server._runtime.runtime_manager.shutdown()  # type: ignore[union-attr]
    server._runtime.runtime_manager = manager  # type: ignore[union-attr]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    base_headers = {"X-IPC-Token": "synthetic-master-token"}
    try:
        token = _mint_fixture_token(port, base_headers)
        client_headers = {"X-OpenMinion-Client-Token": token}
        status, created = _http_fixture_json(
            port,
            "POST",
            "/v1/client/sessions",
            {"title": "Approval matrix"},
            client_headers,
        )
        assert status == 200
        session_id = str(created["session"]["session_id"])
        cursor = str(created["session"]["event_cursor"])

        allow = _start_http_turn(
            port, token, session_id, "11111111-1111-4111-8111-111111111111"
        )
        approval_id = _wait_for_approval_frame(allow)
        decision_path = (
            f"/v1/client/sessions/{session_id}/turns/11111111-1111-4111-8111-111111111111/approvals/"
            f"{approval_id}"
        )
        status, first = _http_fixture_json(
            port,
            "POST",
            decision_path,
            {"decision": "allow_once"},
            client_headers,
        )
        assert status == 200
        assert first["approval"]["outcome"] == "applied"
        status, repeated = _http_fixture_json(
            port,
            "POST",
            decision_path,
            {"decision": "allow_once"},
            client_headers,
        )
        assert status == 200
        assert repeated["approval"]["outcome"] == "already_applied"
        status, different = _http_fixture_json(
            port,
            "POST",
            decision_path,
            {"decision": "deny"},
            client_headers,
        )
        assert status == 409
        assert different["error"]["code"] == "approval_already_resolved"
        _join_http_turn(allow)
        assert executions == [
            (
                "11111111-1111-4111-8111-111111111111",
                "call-11111111-1111-4111-8111-111111111111",
            )
        ]

        deny = _start_http_turn(
            port, token, session_id, "22222222-2222-4222-8222-222222222222"
        )
        deny_approval = _wait_for_approval_frame(deny)
        status, denied = _http_fixture_json(
            port,
            "POST",
            f"/v1/client/sessions/{session_id}/turns/22222222-2222-4222-8222-222222222222/approvals/{deny_approval}",
            {"decision": "deny"},
            client_headers,
        )
        assert status == 200
        assert denied["approval"]["outcome"] == "denied"
        _join_http_turn(deny)

        cancelled = _start_http_turn(
            port, token, session_id, "33333333-3333-4333-8333-333333333333"
        )
        cancel_approval = _wait_for_approval_frame(cancelled)
        status, cancellation = _http_fixture_json(
            port,
            "POST",
            "/v1/turn/33333333-3333-4333-8333-333333333333/cancel",
            {"session_id": session_id},
            client_headers,
        )
        assert status == 202
        assert cancellation["cancellation"]["state"] == "requested"
        _join_http_turn(cancelled)
        status, late = _http_fixture_json(
            port,
            "POST",
            f"/v1/client/sessions/{session_id}/turns/33333333-3333-4333-8333-333333333333/approvals/{cancel_approval}",
            {"decision": "allow_once"},
            client_headers,
        )
        assert status == 409
        assert late["error"]["code"] == "approval_cancelled"

        status, events = _http_fixture_json(
            port,
            "GET",
            f"/v1/client/sessions/{session_id}/events?after={cursor}&limit=200",
            None,
            client_headers,
        )
        assert status == 200
        approval_events = [
            event
            for event in events["events"]
            if event["event_type"].startswith("desktop.approval.")
        ]
        assert len(approval_events) == 6
        assert {
            (event["trace_id"], event["facts"]["approval_id"])
            for event in approval_events
        } == {
            ("11111111-1111-4111-8111-111111111111", approval_id),
            ("22222222-2222-4222-8222-222222222222", deny_approval),
            ("33333333-3333-4333-8333-333333333333", cancel_approval),
        }
        assert executions == [
            (
                "11111111-1111-4111-8111-111111111111",
                "call-11111111-1111-4111-8111-111111111111",
            )
        ]
        assert not any(
            forbidden
            in json.dumps([allow.frames, deny.frames, cancelled.frames, events])
            for forbidden in ("synthetic-master-token", token, "/private", "secret")
        )

        revoked = _start_http_turn(
            port, token, session_id, "44444444-4444-4444-8444-444444444444"
        )
        revoked_approval = _wait_for_approval_frame(revoked)
        status, revoked_payload = _http_fixture_json(
            port,
            "DELETE",
            "/v1/client/leases/current",
            {},
            client_headers,
        )
        assert status == 200
        assert revoked_payload["revoked"] is True
        _join_http_turn(revoked)
        assert any(
            name == "chunk"
            and payload.get("kind") == "approval_resolved"
            and payload["data"]["approval_id"] == revoked_approval
            and payload["data"]["outcome"] == "cancelled"
            for name, payload in revoked.frames
        )

        owner_token = _mint_fixture_token(port, base_headers)
        closer_token = _mint_fixture_token(port, base_headers)
        owner_headers = {"X-OpenMinion-Client-Token": owner_token}
        status, closing_session = _http_fixture_json(
            port,
            "POST",
            "/v1/client/sessions",
            {"title": "Cross-client close"},
            owner_headers,
        )
        assert status == 200
        closing_session_id = str(closing_session["session"]["session_id"])
        closing = _start_http_turn(
            port,
            owner_token,
            closing_session_id,
            "55555555-5555-4555-8555-555555555555",
        )
        closing_approval = _wait_for_approval_frame(closing)
        status, closed = _http_fixture_json(
            port,
            "DELETE",
            f"/v1/client/sessions/{closing_session_id}",
            {"reason": "fixture close"},
            {"X-OpenMinion-Client-Token": closer_token},
        )
        assert status == 200
        assert closed["session"]["status"] == "closed"
        _join_http_turn(closing)
        status, late_after_close = _http_fixture_json(
            port,
            "POST",
            f"/v1/client/sessions/{closing_session_id}/turns/55555555-5555-4555-8555-555555555555/approvals/{closing_approval}",
            {"decision": "allow_once"},
            owner_headers,
        )
        assert status == 409
        assert late_after_close["error"]["code"] == "approval_cancelled"
        assert len(executions) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class _HTTPFixtureTurn:
    def __init__(
        self, thread: Thread, frames: list[tuple[str, dict[str, Any]]]
    ) -> None:
        self.thread = thread
        self.frames = frames


def _http_fixture_json(
    port: int,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    headers: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    encoded = None if body is None else json.dumps(body, separators=(",", ":"))
    request_headers = dict(headers)
    if encoded is not None:
        request_headers["Content-Type"] = "application/json"
    connection.request(method, path, body=encoded, headers=request_headers)
    response = connection.getresponse()
    payload = json.loads(response.read().decode("utf-8"))
    status = response.status
    connection.close()
    return status, payload


def _mint_fixture_token(
    port: int,
    headers: dict[str, str],
    ttl_seconds: int = 300,
) -> str:
    status, payload = _http_fixture_json(
        port,
        "POST",
        "/v1/client/leases",
        {
            "schema_version": 1,
            "client": {
                "kind": "desktop",
                "version": "0.0.0",
                "protocol_min": 1,
                "protocol_max": 1,
            },
            "requested_ttl_seconds": ttl_seconds,
        },
        headers,
    )
    assert status == 200
    return str(payload["lease"]["client_token"])


def _start_http_turn(
    port: int,
    token: str,
    session_id: str,
    trace_id: str,
) -> _HTTPFixtureTurn:
    frames: list[tuple[str, dict[str, Any]]] = []

    def read_stream() -> None:
        connection = HTTPConnection("127.0.0.1", port, timeout=10)
        body = json.dumps(
            {
                "trace_id": trace_id,
                "session_id": session_id,
                "agent_id": "openminion",
                "input_text": trace_id,
                "mode": "oneshot",
                "stream": True,
                "channel": "console",
                "user": "api-user",
                "idempotency_key": trace_id,
            },
            separators=(",", ":"),
        )
        connection.request(
            "POST",
            "/v1/turn/stream",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "X-Request-ID": f"request-{trace_id}",
                "X-OpenMinion-Client-Token": token,
            },
        )
        response = connection.getresponse()
        assert response.status == 200
        event_name = ""
        while True:
            line = response.readline().decode("utf-8").rstrip("\n")
            if not line:
                continue
            if line.startswith("event: "):
                event_name = line[7:]
                continue
            if line.startswith("data: "):
                frames.append((event_name, json.loads(line[6:])))
                if event_name == "done":
                    break
        response.close()
        connection.close()

    thread = Thread(target=read_stream, daemon=True)
    thread.start()
    return _HTTPFixtureTurn(thread, frames)


def _wait_for_approval_frame(turn: _HTTPFixtureTurn) -> str:
    try:
        _wait_for(
            lambda: any(
                name == "chunk" and payload.get("kind") == "approval_required"
                for name, payload in turn.frames
            ),
            timeout=5,
        )
    except AssertionError as exc:
        raise AssertionError(
            f"approval frame missing; alive={turn.thread.is_alive()} frames={turn.frames!r}"
        ) from exc
    frame = next(
        payload
        for name, payload in turn.frames
        if name == "chunk" and payload.get("kind") == "approval_required"
    )
    return str(frame["data"]["approval_id"])


def _join_http_turn(turn: _HTTPFixtureTurn) -> None:
    turn.thread.join(timeout=5)
    assert not turn.thread.is_alive()
    assert turn.frames[-1][0] == "done"
