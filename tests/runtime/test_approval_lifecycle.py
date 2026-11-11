from __future__ import annotations

import asyncio
from datetime import datetime
from threading import Barrier, Event, Thread
from time import sleep
from types import SimpleNamespace
from unittest import mock

import pytest

from openminion.modules.policy.models import build_consent_preview
from openminion.services.runtime.daemon import execute_turn
from openminion.services.runtime.ingress import TurnTimeoutError
from openminion.services.runtime.ingress.requests import build_manager_turn_request
from openminion.services.runtime.manager import (
    AgentRuntimeManager,
    TurnHandle,
    TurnRequest,
    TurnResponse,
)


def _handle(*, trace_id: str = "trace-approval") -> TurnHandle:
    return TurnHandle(
        trace_id=trace_id,
        session_id="session-approval",
        agent_id="main",
        on_cancel=lambda _trace_id: True,
    )


def _start_approval(
    handle: TurnHandle,
    results: list[bool],
    *,
    source_callback_id: str = "provider-call-1",
    timeout_s: float = 1.0,
    events: list[dict[str, object]] | None = None,
) -> tuple[Thread, object]:
    after_sequence = handle._history[-1].sequence if handle._history else 0
    thread = Thread(
        target=lambda: results.append(
            handle.request_approval(
                tool_name="exec.run",
                consent_preview='exec.run({"command":"pytest"})',
                source_callback_id=source_callback_id,
                timeout_s=timeout_s,
                on_event=(events.append if events is not None else None),
            )
        )
    )
    thread.start()
    requested = next(handle.subscribe(after_sequence=after_sequence, timeout_s=1.0))
    assert requested.kind == "approval"
    assert requested.data["phase"] == "requested"
    return thread, requested


def test_turn_handle_resolves_exact_approval_and_isolates_reused_source_id() -> None:
    handle = _handle()
    results: list[bool] = []
    events: list[dict[str, object]] = []

    first_thread, first = _start_approval(handle, results, events=events)
    replayed = next(handle.subscribe(timeout_s=1.0))
    assert replayed.event_id == first.event_id
    assert (
        handle.resolve_approval(approval_id="wrong-approval", decision="allow_once")
        is False
    )
    assert (
        handle.resolve_approval(
            approval_id=first.data["approval_id"], decision="allow_once"
        )
        is True
    )
    first_thread.join(timeout=1.0)

    second_thread, second = _start_approval(
        handle,
        results,
        source_callback_id="provider-call-1",
        events=events,
    )
    assert second.data["approval_id"] != first.data["approval_id"]
    assert (
        handle.resolve_approval(approval_id=second.data["approval_id"], decision="deny")
        is True
    )
    second_thread.join(timeout=1.0)

    assert results == [True, False]
    assert [event["phase"] for event in events] == [
        "requested",
        "resolved",
        "requested",
        "resolved",
    ]
    assert all("source_callback_id" not in event for event in events)


def test_turn_handle_expiry_and_completion_release_waiters() -> None:
    expired_handle = _handle(trace_id="trace-expiry")
    expired_results: list[bool] = []
    expired_thread, requested = _start_approval(
        expired_handle,
        expired_results,
        timeout_s=0.03,
    )
    expired_thread.join(timeout=1.0)
    assert expired_results == [False]
    expired_events = list(expired_handle._history)
    assert expired_events[-1].data["outcome"] == "expired"
    assert expired_events[-1].data["decision"] is None
    assert expired_events[-1].sequence > requested.sequence

    completed_handle = _handle(trace_id="trace-completion")
    completed_results: list[bool] = []
    completed_thread, _ = _start_approval(completed_handle, completed_results)
    completed_handle._set_result(TurnResponse(final_text="done"))
    completed_thread.join(timeout=1.0)
    assert completed_results == [False]
    assert list(completed_handle._history)[-1].data["outcome"] == "cancelled"
    assert list(completed_handle._history)[-1].data["decision"] is None


def test_turn_handle_decision_cancel_race_settles_once() -> None:
    handle = _handle(trace_id="trace-race")
    results: list[bool] = []
    approval_thread, requested = _start_approval(handle, results)
    barrier = Barrier(3)

    def _allow() -> None:
        barrier.wait()
        handle.resolve_approval(
            approval_id=requested.data["approval_id"], decision="allow_once"
        )

    def _cancel() -> None:
        barrier.wait()
        handle.cancel()

    allow_thread = Thread(target=_allow)
    cancel_thread = Thread(target=_cancel)
    allow_thread.start()
    cancel_thread.start()
    barrier.wait()
    allow_thread.join(timeout=1.0)
    cancel_thread.join(timeout=1.0)
    approval_thread.join(timeout=1.0)

    resolved = [
        chunk
        for chunk in handle._history
        if chunk.kind == "approval" and chunk.data["phase"] == "resolved"
    ]
    assert len(resolved) == 1
    assert results in ([True], [False])


def test_turn_handle_rechecks_cancellation_before_registering_approval() -> None:
    class _CancelAfterFirstCheck(Event):
        def is_set(self) -> bool:
            if not super().is_set():
                self.set()
                return False
            return True

    handle = _handle(trace_id="trace-registration-race")
    handle._cancel_event = _CancelAfterFirstCheck()

    approved = handle.request_approval(
        tool_name="exec.run",
        consent_preview='exec.run({"command":"pytest"})',
        source_callback_id="provider-call-1",
        timeout_s=1.0,
    )

    assert approved is False
    assert handle._pending_approval is None
    assert list(handle._history) == []


def test_runtime_manager_rejects_duplicate_active_trace() -> None:
    started = Event()
    release = Event()

    def _executor(_request, _emit_chunk, _cancel_event):  # noqa: ANN001
        started.set()
        release.wait(timeout=1.0)
        return TurnResponse(final_text="done")

    manager = AgentRuntimeManager(turn_executor=_executor)
    manager.start()
    request = TurnRequest("trace-duplicate", "main", "session", "run")
    try:
        first = manager.submit_turn(request)
        assert started.wait(timeout=1.0)
        with pytest.raises(ValueError, match="duplicate active trace_id"):
            manager.submit_turn(request)
        release.set()
        assert first.result(timeout_s=1.0).final_text == "done"
    finally:
        release.set()
        manager.shutdown()


def test_runtime_manager_shutdown_releases_pending_approval() -> None:
    manager_ref: dict[str, AgentRuntimeManager] = {}
    approval_results: list[bool] = []

    def _executor(request, _emit_chunk, _cancel_event):  # noqa: ANN001
        handle = manager_ref["manager"].get_turn_handle(request.trace_id)
        assert handle is not None
        approval_results.append(
            handle.request_approval(
                tool_name="exec.run",
                consent_preview='exec.run({"command":"pytest"})',
                source_callback_id="provider-call",
                timeout_s=10.0,
            )
        )
        return TurnResponse(final_text="done")

    manager = AgentRuntimeManager(turn_executor=_executor)
    manager_ref["manager"] = manager
    manager.start()
    handle = manager.submit_turn(
        TurnRequest("trace-shutdown", "main", "session", "run")
    )
    requested = next(
        chunk for chunk in handle.subscribe(timeout_s=1.0) if chunk.kind == "approval"
    )
    assert requested.data["phase"] == "requested"

    manager.shutdown(grace_s=1.0)

    assert approval_results == [False]
    assert handle.result(timeout_s=1.0).final_text == "done"
    assert list(handle._history)[-3].data["outcome"] == "cancelled"


def test_manager_request_lifts_explicit_approval_transport() -> None:
    request = build_manager_turn_request(
        {
            "agent_id": "main",
            "session_id": "session",
            "message": "run it",
            "approval_transport": "turn_stream",
        },
        default_agent_id="main",
    )
    assert request.meta["approval_transport"] == "turn_stream"


def test_consent_preview_redacts_recognized_credentials_and_is_bounded() -> None:
    preview = build_consent_preview(
        "exec.run",
        {
            "command": f"echo safe && {'x' * 500} && rm -rf /tmp/example",
            "nested": {"api_key": "sk-example-secret-value-123456789"},
            "authorization": "Bearer abcdefghijklmnop",
            "items": list(range(20)),
        },
    )
    assert len(preview) <= 4096
    assert "rm -rf /tmp/example" in preview
    assert "[REDACTED]" in preview
    assert "example-secret" not in preview
    assert "abcdefgh" not in preview


def test_consent_preview_rejects_an_executable_command_too_large_to_show() -> None:
    with pytest.raises(ValueError, match="too long to display"):
        build_consent_preview("exec.run", {"command": "x" * 5000})


def test_daemon_negotiated_approval_emits_stream_and_session_events() -> None:
    emitted = []
    session_events: list[dict[str, object]] = []
    handle = _handle(trace_id="trace-daemon")
    runtime = SimpleNamespace(
        runtime_manager=SimpleNamespace(
            get_turn_handle=lambda trace_id: (
                handle if trace_id == handle.trace_id else None
            )
        ),
        sessions=SimpleNamespace(
            append_event=lambda **kwargs: session_events.append(dict(kwargs))
        ),
    )
    request = TurnRequest(
        trace_id=handle.trace_id,
        agent_id="main",
        session_id=handle.session_id,
        input_text="run it",
        stream=True,
        meta={"approval_transport": "turn_stream"},
    )

    def _fake_execute_runtime_turn(**kwargs):  # noqa: ANN003
        approval_callback = kwargs["approval_callback"]

        def _allow() -> None:
            while not session_events:
                sleep(0.005)
            approval_id = session_events[0]["payload"]["approval_id"]
            assert handle.resolve_approval(
                approval_id=approval_id, decision="allow_once"
            )

        resolver = Thread(target=_allow)
        resolver.start()
        approved = asyncio.run(
            approval_callback(
                "exec.run",
                {"command": "pytest", "api_key": "sk-secret-value-123456789"},
                "provider-call-1",
                {
                    "confirmation_preview": {
                        "display_lines": [
                            "Recipient: fixture",
                            "Amount: 42",
                            "Authorization: Bearer abcdefghijklmnop",
                        ]
                    }
                },
            )
        )
        resolver.join(timeout=1.0)
        assert approved is True
        return SimpleNamespace(
            body="final",
            metadata={},
            stats=SimpleNamespace(has_any_data=False, as_payload=lambda: {}),
        )

    with (
        mock.patch(
            "openminion.services.runtime.daemon.runtime_turn_request_from_manager_request",
            return_value=SimpleNamespace(timeout_seconds=2.0),
        ),
        mock.patch(
            "openminion.services.runtime.daemon.execute_runtime_turn",
            side_effect=_fake_execute_runtime_turn,
        ),
        mock.patch(
            "openminion.services.runtime.daemon.monotonic",
            side_effect=[100.0, 100.5],
        ),
    ):
        response = execute_turn(
            runtime=runtime,
            request=request,
            emit_chunk=emitted.append,
            cancel_event=Event(),
        )

    assert response.final_text == "final"
    approval_chunks = [chunk for chunk in handle._history if chunk.kind == "approval"]
    assert [chunk.data["phase"] for chunk in approval_chunks] == [
        "requested",
        "resolved",
    ]
    assert [event["event_type"] for event in session_events] == [
        "approval.requested",
        "approval.resolved",
    ]
    requested_payload = session_events[0]["payload"]
    assert "Recipient: fixture" in requested_payload["consent_preview"]
    assert "Amount: 42" in requested_payload["consent_preview"]
    assert "abcdefghijklmnop" not in repr(session_events)
    requested_at = datetime.fromisoformat(requested_payload["requested_at"])
    expires_at = datetime.fromisoformat(requested_payload["expires_at"])
    assert 1.34 <= (expires_at - requested_at).total_seconds() <= 1.36
    assert "requested_at" not in session_events[1]["payload"]
    assert "expires_at" not in session_events[1]["payload"]
    assert "secret-value" not in repr(session_events)
    assert "provider-call-1" not in repr(session_events)


def test_daemon_without_negotiation_passes_no_approval_callback() -> None:
    captured: dict[str, object] = {}
    request = TurnRequest("trace", "main", "session", "hello")

    def _fake_execute_runtime_turn(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return SimpleNamespace(
            body="final",
            metadata={},
            stats=SimpleNamespace(has_any_data=False, as_payload=lambda: {}),
        )

    with (
        mock.patch(
            "openminion.services.runtime.daemon.runtime_turn_request_from_manager_request",
            return_value=SimpleNamespace(timeout_seconds=2.0),
        ),
        mock.patch(
            "openminion.services.runtime.daemon.execute_runtime_turn",
            side_effect=_fake_execute_runtime_turn,
        ),
    ):
        response = execute_turn(
            runtime=SimpleNamespace(),
            request=request,
            emit_chunk=lambda _chunk: None,
            cancel_event=Event(),
        )

    assert response.final_text == "final"
    assert captured["approval_callback"] is None


@pytest.mark.parametrize("decision", ["allow_session", "allow_forever", "approve", ""])
def test_invalid_approval_decision_is_explicit_and_request_remains_pending(decision):
    handle = _handle()
    results = []
    thread, request = _start_approval(handle, results)
    try:
        with pytest.raises(ValueError, match="allow_once.*deny"):
            handle.resolve_approval(
                approval_id=request.data["approval_id"], decision=decision
            )
        assert not results
        assert handle.resolve_approval(
            approval_id=request.data["approval_id"], decision=" ALLOW_ONCE "
        )
    finally:
        handle.cancel()
        thread.join(timeout=1)
    assert results == [True]


def test_consent_preview_refuses_to_truncate_exact_action():
    with pytest.raises(ValueError, match="too long to display"):
        build_consent_preview(
            "blockchain.send_transaction",
            {},
            confirmation_preview={"display_lines": ["Recipient: fixture", "x" * 5000]},
        )


def test_daemon_negotiated_approval_cancel_returns_cancelled_turn() -> None:
    session_events: list[dict[str, object]] = []
    requested_ready = Event()
    handle = _handle(trace_id="trace-daemon-cancel")
    runtime = SimpleNamespace(
        runtime_manager=SimpleNamespace(
            get_turn_handle=lambda trace_id: (
                handle if trace_id == handle.trace_id else None
            )
        ),
        sessions=SimpleNamespace(
            append_event=lambda **kwargs: (
                session_events.append(dict(kwargs)),
                requested_ready.set()
                if kwargs["event_type"] == "approval.requested"
                else None,
            )
        ),
    )
    request = TurnRequest(
        trace_id=handle.trace_id,
        agent_id="main",
        session_id=handle.session_id,
        input_text="run it",
        stream=True,
        meta={"approval_transport": "turn_stream"},
    )

    def _fake_execute_runtime_turn(**kwargs):  # noqa: ANN003
        approval_callback = kwargs["approval_callback"]

        def _cancel() -> None:
            assert requested_ready.wait(timeout=1.0)
            assert handle.cancel() is True

        canceller = Thread(target=_cancel)
        canceller.start()
        try:
            return asyncio.run(
                approval_callback("exec.run", {"command": "pytest"}, "call-1", {})
            )
        finally:
            canceller.join(timeout=1.0)

    with (
        mock.patch(
            "openminion.services.runtime.daemon.runtime_turn_request_from_manager_request",
            return_value=SimpleNamespace(timeout_seconds=2.0),
        ),
        mock.patch(
            "openminion.services.runtime.daemon.execute_runtime_turn",
            side_effect=_fake_execute_runtime_turn,
        ),
    ):
        response = execute_turn(
            runtime=runtime,
            request=request,
            emit_chunk=lambda _chunk: None,
            cancel_event=handle.cancel_event,
        )

    assert response.final_text == ""
    assert response.errors[0].code == "cancelled"
    assert response.errors[0].retryable is False
    assert [event["event_type"] for event in session_events] == [
        "approval.requested",
        "approval.resolved",
    ]
    assert session_events[1]["payload"]["outcome"] == "cancelled"


@pytest.mark.parametrize(
    ("raised_exception", "expected_code", "expected_retryable"),
    [
        (RuntimeError("provider boom"), "turn_failed", False),
        (TurnTimeoutError("turn timed out"), "turn_timeout", True),
    ],
)
def test_daemon_execute_turn_preserves_ordinary_error_and_timeout_neighbors(
    raised_exception: Exception, expected_code: str, expected_retryable: bool
) -> None:
    request = TurnRequest("trace-neighbor", "main", "session-neighbor", "run it")
    with (
        mock.patch(
            "openminion.services.runtime.daemon.runtime_turn_request_from_manager_request",
            return_value=SimpleNamespace(timeout_seconds=2.0),
        ),
        mock.patch(
            "openminion.services.runtime.daemon.execute_runtime_turn",
            side_effect=raised_exception,
        ),
    ):
        response = execute_turn(
            runtime=SimpleNamespace(),
            request=request,
            emit_chunk=lambda _chunk: None,
            cancel_event=Event(),
        )

    assert response.final_text == ""
    assert response.errors[0].code == expected_code
    assert response.errors[0].retryable is expected_retryable


def test_gateway_persistence_aborts_before_writing_completed_when_cancelled() -> None:
    from openminion.modules.runtime.sync import await_with_cancel
    from openminion.services.gateway.turn.flow_persistence import (
        GatewayTurnPersistenceDeliveryMixin,
    )

    finished_runs: list[tuple[str, dict[str, object]]] = []
    appended_messages: list[dict[str, object]] = []

    class _Harness(GatewayTurnPersistenceDeliveryMixin):
        def __init__(self) -> None:
            self._agent_id = "main"
            self._agent = SimpleNamespace(memory_capture_assurance_enabled=False)
            self._security = SimpleNamespace(
                evaluate_policy=lambda **_kw: None,
                enforce_policy=lambda **_kw: None,
            )
            self._lifecycle_ops = SimpleNamespace(
                emit_turn_event=lambda **_kw: None,
                emit_terminal_run_state=lambda **_kw: None,
                corr_payload=lambda **_kw: {},
            )
            self._sessions = SimpleNamespace(
                finish_run_record=lambda run_id, **kw: finished_runs.append(
                    (run_id, kw)
                ),
                append_message=lambda **kw: (
                    appended_messages.append(kw),
                    SimpleNamespace(id="msg-1"),
                )[1],
            )

    harness = _Harness()
    routing = SimpleNamespace(
        session=SimpleNamespace(id="session-cancel"),
        conversation_id="",
        thread_id="",
        attach_id="",
        normalized_request_id="req-1",
    )
    response = SimpleNamespace(
        id="resp-1",
        body="Tool execution denied by operator.",
        metadata={},
        channel="console",
        target="user",
    )
    cancel_event = Event()

    async def _persist_after_cancel() -> None:
        cancel_event.set()
        harness._complete_suppressed_idle_tick(
            routing=routing,
            run_id="run-idle",
            response=response,
            lifecycle_payload={},
            typed_terminal_resolver=None,
            session_turn_fence_token=None,
        )

    with pytest.raises(RuntimeError, match="cancelled"):
        asyncio.run(
            await_with_cancel(
                _persist_after_cancel(), timeout_s=2.0, cancel_event=cancel_event
            )
        )
    assert finished_runs == []

    cancel_outbound = Event()

    async def _outbound_after_cancel() -> None:
        cancel_outbound.set()
        harness._build_outbound_and_persist(
            routing,
            run_id="run-1",
            response=response,
            memory_context_meta={},
            memory_retrieval_meta={},
        )

    with pytest.raises(RuntimeError, match="cancelled"):
        asyncio.run(
            await_with_cancel(
                _outbound_after_cancel(), timeout_s=2.0, cancel_event=cancel_outbound
            )
        )
    assert finished_runs == []
    assert appended_messages == []


@pytest.mark.parametrize("callback_mode", ["deny", "allow"])
def test_sidecar_approval_aborts_on_turn_cancellation(
    callback_mode: str,
) -> None:
    from openminion.modules.runtime.sync import await_with_cancel
    from openminion.modules.tool.sidecars import (
        maybe_allow_denied_call_with_operator_approval,
    )

    cancel_sidecar = Event()
    sidecar_cancelled = False

    async def _sidecar_callback(*_args: object) -> bool:
        cancel_sidecar.set()
        return callback_mode == "allow"

    async def _run_sidecar() -> None:
        nonlocal sidecar_cancelled
        try:
            await maybe_allow_denied_call_with_operator_approval(
                call=SimpleNamespace(id="call-1", source="provider"),
                tool_name="lookup_order",
                tool_args={"order_id": "A-104"},
                decision=SimpleNamespace(
                    allowed=False,
                    requires_confirm=True,
                    code="REQUIRE_APPROVAL",
                    reason="ask",
                    details={},
                    modified_args=None,
                ),
                approval_callback=_sidecar_callback,
            )
        except asyncio.CancelledError:
            sidecar_cancelled = True
            raise

    with pytest.raises(RuntimeError, match="cancelled"):
        asyncio.run(
            await_with_cancel(
                _run_sidecar(), timeout_s=2.0, cancel_event=cancel_sidecar
            )
        )
    assert sidecar_cancelled is True
