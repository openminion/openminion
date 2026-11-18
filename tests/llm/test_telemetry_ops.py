from __future__ import annotations

import asyncio
import threading
from contextvars import copy_context
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict

import pytest

from openminion.modules.llm import LLMCTL
from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.runtime import flow
from openminion.modules.runtime.sync import await_with_cancel
from openminion.modules.llm.schemas import (
    LLMRequest,
    LLMResponse,
    Message,
    UsageInfo,
)
from openminion.modules.llm.diagnostics.events import (
    emit_llm_operation,
    emit_tool_envelope_recovery_event,
)
from openminion.modules.telemetry.trace.phase_timing import (
    ChatPhaseTimer,
    use_chat_phase_timer,
)
from openminion.modules.telemetry.events.catalog import (
    TOOL_ENVELOPE_REPAIR_EXHAUSTED,
    TOOL_ENVELOPE_REPAIR_RETRY,
)
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture
def temp_db(tmp_path: Path) -> str:
    return str(tmp_path / ".openminion" / "telemetry.db")


class _SequenceProvider:
    name = "telemetry_provider"
    contract_version = "v1"

    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)
        self.calls = 0

    def complete(self, request: LLMRequest, config: Dict[str, Any]) -> LLMResponse:
        del config, request
        self.calls += 1
        outcome = self._results[min(self.calls - 1, len(self._results) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def list_models(self, config: Dict[str, Any]) -> list[str]:
        del config
        return ["telemetry-model"]

    def healthcheck(self, config: Dict[str, Any]) -> Dict[str, Any]:
        del config
        return {"ok": True}


def _make_response(
    *,
    ok: bool = True,
    provider: str = "telemetry_provider",
    model: str = "telemetry-model",
    output_text: str = "ok",
    latency_ms: int = 7,
    telemetry: Dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str = "failed",
) -> LLMResponse:
    from openminion.modules.llm.schemas import ResponseError

    return LLMResponse(
        ok=ok,
        provider=provider,
        model=model,
        output_text=output_text if ok else "",
        assistant_messages=[Message(role="assistant", content=output_text)]
        if ok
        else [],
        tool_calls=[],
        usage=UsageInfo(input_tokens=11, output_tokens=5, total_tokens=16),
        latency_ms=latency_ms,
        finish_reason="stop" if ok else "error",
        provider_raw=None,
        error=(
            None
            if ok
            else ResponseError(
                code=str(error_code or "PROVIDER_ERROR"), message=error_message
            )
        ),
        telemetry=telemetry or {},
    )


def _make_runtime(
    *,
    telemetryctl: TelemetryCtl | None,
    provider: _SequenceProvider,
    max_retries: int = 1,
    backoff_ms: int = 0,
) -> LLMCTL:
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {
                "default_provider": provider.name,
                "default_model": "telemetry-model",
                "retries": {"max_retries": max_retries, "backoff_ms": backoff_ms},
            },
            "providers": {provider.name: {}},
            "agents": {
                "default": {
                    "default_provider": provider.name,
                    "default_model": "telemetry-model",
                    "tool_policy": {"enable_tools": True},
                }
            },
        },
        telemetryctl=telemetryctl,
    )
    runtime.registry.add(provider)
    return runtime


def _make_fallback_runtime(
    *,
    telemetryctl: TelemetryCtl | None,
    primary: _SequenceProvider,
    fallback: _SequenceProvider,
    fallback_on: tuple[str, ...] = ("PROVIDER_ERROR",),
) -> LLMCTL:
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {
                "default_provider": primary.name,
                "default_model": "primary-model",
                "retries": {"max_retries": 0, "backoff_ms": 0},
                "routing_defaults": {
                    "primary": {"provider": primary.name, "model": "primary-model"},
                    "fallbacks": [
                        {
                            "provider": fallback.name,
                            "model": "fallback-model",
                            "on": list(fallback_on),
                        }
                    ],
                },
            },
            "providers": {primary.name: {}, fallback.name: {}},
            "agents": {
                "default": {
                    "tool_policy": {"enable_tools": True},
                }
            },
        },
        telemetryctl=telemetryctl,
    )
    runtime.registry.add(primary)
    runtime.registry.add(fallback)
    return runtime


def _request_payload(session_id: str, turn_id: str) -> Dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": "hello telemetry"}],
        "metadata": {
            "session_id": session_id,
            "turn_id": turn_id,
            "trace_id": "trace-1",
            "llm_call_id": "call-entry-1",
            "purpose": "entry",
            "mode_name": "plan",
        },
    }


async def _call_and_stats(
    temp_db: str,
    *,
    session_id: str,
    outcomes: list[Any],
) -> tuple[LLMResponse, _SequenceProvider, dict[str, Any]]:
    service = TelemetryService(temp_db)
    provider = _SequenceProvider(outcomes)
    runtime = _make_runtime(
        telemetryctl=TelemetryCtl(service),
        provider=provider,
    )
    try:
        response = await runtime.client(agent_name="default").call(
            _request_payload(session_id, "turn-1")
        )
        module_summary = await service.get_module_summary(session_id)
        return response, provider, module_summary["openminion-llm"]
    finally:
        await service.close()


async def _call_and_summary(
    temp_db: str,
    *,
    session_id: str,
    request_payload: dict[str, Any],
    outcomes: list[Any],
):
    service = TelemetryService(temp_db)
    provider = _SequenceProvider(outcomes)
    runtime = _make_runtime(
        telemetryctl=TelemetryCtl(service),
        provider=provider,
    )
    try:
        response = await runtime.client(agent_name="default").call(request_payload)
        summary = await service.get_session_summary(session_id)
        return response, provider, summary
    finally:
        await service.close()


def test_llm_module_emits_request_response_and_cache_hit(temp_db: str) -> None:
    response, _, stats = _run(
        _call_and_stats(
            temp_db,
            session_id="sess-llm-cache",
            outcomes=[
                _make_response(telemetry={"cache_hit": True, "cached_tokens": 9})
            ],
        )
    )
    assert response.ok is True
    assert stats["operation_counts"]["request"] == 1
    assert stats["operation_counts"]["response"] == 1
    assert stats["operation_counts"]["cache_hit"] == 1


def test_llm_module_emits_retry_and_error_for_retryable_failure(temp_db: str) -> None:
    response, provider, stats = _run(
        _call_and_stats(
            temp_db,
            session_id="sess-llm-retry",
            outcomes=[
                LLMCtlError("RATE_LIMITED", "retry later"),
                _make_response(output_text="recovered"),
            ],
        )
    )
    assert response.ok is True
    assert provider.calls == 2
    assert stats["operation_counts"]["request"] == 2
    assert stats["operation_counts"]["error"] == 1
    assert stats["operation_counts"]["retry"] == 1
    assert stats["operation_counts"]["response"] == 1


def test_llm_runtime_records_physical_retry_attempts_on_active_timer(
    temp_db: str,
) -> None:
    async def _case() -> list[dict[str, object]]:
        service = TelemetryService(temp_db)
        provider = _SequenceProvider(
            [
                LLMCtlError("RATE_LIMITED", "retry later"),
                _make_response(output_text="recovered", latency_ms=13),
            ]
        )
        runtime = _make_runtime(
            telemetryctl=TelemetryCtl(service),
            provider=provider,
        )
        timer = ChatPhaseTimer()
        try:
            with use_chat_phase_timer(timer):
                response = await runtime.client(agent_name="default").call(
                    _request_payload("sess-llm-physical-retry", "turn-1")
                )
            assert response.ok is True
            return list(timer.build_payload().provider_attempts)
        finally:
            await service.close()

    attempts = _run(_case())

    assert [item["semantic_purpose"] for item in attempts] == ["entry", "entry"]
    assert [item["logical_call_id"] for item in attempts] == [
        "call-entry-1",
        "call-entry-1",
    ]
    assert [item["route_posture"] for item in attempts] == ["primary", "primary"]
    assert [item["attempt_posture"] for item in attempts] == ["initial", "retry"]
    assert [item["outcome"] for item in attempts] == ["error", "ok"]


def test_llm_runtime_records_physical_fallback_attempt_on_active_timer(
    temp_db: str,
) -> None:
    async def _case() -> list[dict[str, object]]:
        service = TelemetryService(temp_db)
        primary = _SequenceProvider([LLMCtlError("PROVIDER_ERROR", "try fallback")])
        fallback = _SequenceProvider([_make_response(output_text="fallback ok")])
        fallback.name = "fallback_provider"
        runtime = _make_fallback_runtime(
            telemetryctl=TelemetryCtl(service),
            primary=primary,
            fallback=fallback,
        )
        timer = ChatPhaseTimer()
        try:
            with use_chat_phase_timer(timer):
                response = await runtime.client(agent_name="default").call(
                    _request_payload("sess-llm-physical-fallback", "turn-1")
                )
            assert response.ok is True
            return list(timer.build_payload().provider_attempts)
        finally:
            await service.close()

    attempts = _run(_case())

    assert [item["semantic_purpose"] for item in attempts] == ["entry", "entry"]
    assert [item["route_posture"] for item in attempts] == ["primary", "fallback"]
    assert [item["attempt_posture"] for item in attempts] == ["initial", "initial"]
    assert [item["outcome"] for item in attempts] == ["error", "ok"]


def test_llm_module_emits_terminal_error_without_retry(temp_db: str) -> None:
    response, _, stats = _run(
        _call_and_stats(
            temp_db,
            session_id="sess-llm-error",
            outcomes=[LLMCtlError("AUTH_ERROR", "bad key")],
        )
    )
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "AUTH_ERROR"
    assert stats["operation_counts"]["request"] == 1
    assert stats["operation_counts"]["error"] == 1
    assert "retry" not in stats["operation_counts"]
    assert "response" not in stats["operation_counts"]


def test_llm_module_emits_safe_tool_envelope_exhausted_event(temp_db: str) -> None:
    response, _, summary = _run(
        _call_and_summary(
            temp_db,
            session_id="sess-llm-envelope",
            request_payload={
                **_request_payload("sess-llm-envelope", "turn-1"),
                "tools": [
                    {
                        "name": "web.search",
                        "description": "Search the web.",
                        "input_schema": {"type": "object", "properties": {}},
                    }
                ],
            },
            outcomes=[
                _make_response(
                    output_text=(
                        '<tool name="not.allowed">'
                        '<parameter name="q">secret prompt text</parameter>'
                        "</tool>"
                    )
                )
            ],
        )
    )

    assert response.output_text.startswith("[system: UNEXECUTABLE_TOOL_ENVELOPE]")
    events = [
        event
        for event in summary.events
        if event.event_type == TOOL_ENVELOPE_REPAIR_EXHAUSTED
    ]
    assert len(events) == 1
    payload = events[0].data
    assert payload["module_id"] == "openminion-llm"
    assert payload["status"] == "error"
    assert payload["recovery_outcome"] == "exhausted"
    assert payload["provider"] == "telemetry_provider"
    assert payload["source"] == "llm_response_normalization"
    assert "secret prompt text" not in str(payload)
    assert "<tool" not in str(payload)


def test_llm_tool_envelope_recovery_helper_emits_retry_event(temp_db: str) -> None:
    async def _case() -> None:
        service = TelemetryService(temp_db)
        ctl = TelemetryCtl(service)
        try:
            emitted = emit_tool_envelope_recovery_event(
                telemetryctl=ctl,
                session_id="sess-llm-envelope-retry",
                turn_id="turn-1",
                outcome="retry",
                provider="telemetry_provider",
                model="telemetry-model",
                parse_strategy="none",
                parse_mode="minimax_xml",
                attempt=1,
                source="required_lane_follow_up",
                error_code="UNPARSEABLE_TOOL_ENVELOPE",
            )
            assert emitted is True
            assert (
                emit_tool_envelope_recovery_event(
                    telemetryctl=ctl,
                    session_id="sess-llm-envelope-retry",
                    turn_id="turn-1",
                    outcome="guess",
                )
                is False
            )

            await asyncio.sleep(0)
            summary = await service.get_session_summary("sess-llm-envelope-retry")
        finally:
            await service.close()

        events = [
            event
            for event in summary.events
            if event.event_type == TOOL_ENVELOPE_REPAIR_RETRY
        ]
        assert len(events) == 1
        payload = events[0].data
        assert payload["status"] == "retry"
        assert payload["recovery_outcome"] == "retry"
        assert payload["parse_mode"] == "minimax_xml"
        assert payload["source"] == "required_lane_follow_up"

    _run(_case())


def test_llm_telemetry_helper_rejects_invalid_name_and_absent_adapter(
    temp_db: str,
) -> None:
    async def _case() -> None:
        service = TelemetryService(temp_db)
        ctl = TelemetryCtl(service)

        assert (
            emit_llm_operation(
                telemetryctl=ctl,
                session_id="sess-invalid",
                turn_id="turn-1",
                operation="not-real",
                provider="telemetry_provider",
                model="telemetry-model",
            )
            is False
        )
        assert (
            emit_llm_operation(
                telemetryctl=None,
                session_id="sess-invalid",
                turn_id="turn-1",
                operation="request",
                provider="telemetry_provider",
                model="telemetry-model",
            )
            is False
        )

        with pytest.raises(ValueError, match="operation must be non-empty"):
            await ctl.emit_module_operation(
                session_id="sess-invalid",
                turn_id="turn-1",
                module_id="openminion-llm",
                operation="",
            )

        module_summary = await service.get_module_summary("sess-invalid")
        assert module_summary == {}
        await service.close()

    _run(_case())


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.wall = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc).timestamp()
        self.sleeps: list[float] = []
        self.on_sleep: Callable[[], None] | None = None

    def monotonic(self) -> float:
        return self.now

    perf_counter = monotonic

    def time(self) -> float:
        return self.wall

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        self.wall += seconds
        if self.on_sleep is not None:
            self.on_sleep()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    fake = _FakeClock()
    monkeypatch.setattr(flow, "time", fake)
    return fake


class _OrderedProvider(_SequenceProvider):
    def __init__(
        self,
        name: str,
        results: list[Any],
        order: list[str],
        *,
        purpose: str | None = None,
    ) -> None:
        super().__init__(results)
        self.name = name
        self._order = order
        self._purpose = purpose

    def complete(self, request: LLMRequest, config: Dict[str, Any]) -> LLMResponse:
        if self._purpose in (None, (request.metadata or {}).get("purpose")):
            self._order.append(self.name)
        return super().complete(request, config)


def _rate_limited(retry_after: str | None = None) -> LLMCtlError:
    details: dict[str, Any] = {"status_code": 429}
    if retry_after is not None:
        details["retry_after"] = retry_after
    return LLMCtlError("RATE_LIMITED", "slow down", details)


def _call(
    runtime: LLMCTL,
    *,
    timeout_s: float | None,
    cancel_event: threading.Event | None = None,
    timer: ChatPhaseTimer | None = None,
) -> LLMResponse:
    async def _go() -> LLMResponse:
        call = runtime.client(agent_name="default").call(
            _request_payload("sess-retry-after", "turn-1")
        )
        if timeout_s is None:
            return await call
        return await await_with_cancel(
            call, timeout_s=timeout_s, cancel_event=cancel_event
        )

    with use_chat_phase_timer(timer or ChatPhaseTimer()):
        return _run(_go())


def _attempts(timer: ChatPhaseTimer) -> list[tuple[object, object, object]]:
    return [
        (item["route_posture"], item["attempt_posture"], item["outcome"])
        for item in timer.build_payload().provider_attempts
    ]


def _routed(
    order: list[str],
    primary: list[Any],
    fallback: list[Any],
    *,
    fallback_on: tuple[str, ...] = ("RATE_LIMITED",),
) -> tuple[LLMCTL, _OrderedProvider, _OrderedProvider]:
    a = _OrderedProvider("provider_a", primary, order)
    b = _OrderedProvider("provider_b", fallback, order)
    runtime = _make_fallback_runtime(
        telemetryctl=None, primary=a, fallback=b, fallback_on=fallback_on
    )
    return runtime, a, b


def test_inner_retry_waits_for_retry_after_that_fits_deadline(
    clock: _FakeClock,
) -> None:
    provider = _SequenceProvider([_rate_limited("30"), _make_response()])
    runtime = _make_runtime(
        telemetryctl=None, provider=provider, max_retries=1, backoff_ms=100
    )

    response = _call(runtime, timeout_s=120)

    assert response.ok is True
    assert provider.calls == 2
    assert sum(clock.sleeps) == pytest.approx(30.0)
    assert max(clock.sleeps) <= 0.05


@pytest.mark.parametrize("timeout_s", [5.0, None], ids=["no-fit", "no-deadline"])
def test_inner_retry_returns_error_when_retry_after_cannot_wait(
    clock: _FakeClock, timeout_s: float | None
) -> None:
    provider = _SequenceProvider([_rate_limited("30"), _make_response()])
    runtime = _make_runtime(
        telemetryctl=None, provider=provider, max_retries=2, backoff_ms=100
    )
    timer = ChatPhaseTimer()

    response = _call(runtime, timeout_s=timeout_s, timer=timer)

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "RATE_LIMITED"
    assert response.error.details == {"status_code": 429, "retry_after": "30"}
    assert provider.calls == 1
    assert clock.sleeps == []
    assert _attempts(timer) == [("primary", "initial", "error")]


@pytest.mark.parametrize(
    "retry_after",
    ["0", "Wed, 18 Nov 2026 11:59:00 GMT", "1.5", "soon", None],
    ids=["zero", "past", "fractional", "malformed", "missing"],
)
def test_retry_after_without_future_delay_keeps_ordinary_backoff(
    clock: _FakeClock, retry_after: str | None
) -> None:
    provider = _SequenceProvider([_rate_limited(retry_after), _make_response()])
    runtime = _make_runtime(
        telemetryctl=None, provider=provider, max_retries=1, backoff_ms=100
    )

    response = _call(runtime, timeout_s=None)

    assert response.ok is True
    assert provider.calls == 2
    assert clock.sleeps == [0.1]


def test_retry_without_hint_keeps_exponential_backoff(clock: _FakeClock) -> None:
    provider = _SequenceProvider(
        [_rate_limited(), _rate_limited(), _rate_limited(), _make_response()]
    )
    runtime = _make_runtime(
        telemetryctl=None, provider=provider, max_retries=3, backoff_ms=100
    )

    response = _call(runtime, timeout_s=120)

    assert response.ok is True
    assert provider.calls == 4
    assert clock.sleeps == pytest.approx([0.1, 0.2, 0.4])


def test_zero_retries_make_one_attempt_and_reentry_respects_hint(
    clock: _FakeClock,
) -> None:
    provider = _SequenceProvider([_rate_limited("30"), _make_response()])
    runtime = _make_runtime(telemetryctl=None, provider=provider, max_retries=0)

    with flow.route_admission_scope():
        first = _call(runtime, timeout_s=None)
        assert provider.calls == 1
        assert clock.sleeps == []

        refused = _call(runtime, timeout_s=None)
        assert provider.calls == 1
        assert clock.sleeps == []
        assert refused.error is not None and first.error is not None
        assert refused.error.details == first.error.details

        admitted = _call(runtime, timeout_s=120)

    assert admitted.ok is True
    assert provider.calls == 2
    assert sum(clock.sleeps) == pytest.approx(30.0)


def test_hinted_wait_stops_within_one_slice_when_turn_is_cancelled(
    clock: _FakeClock,
) -> None:
    provider = _SequenceProvider([_rate_limited("30"), _make_response()])
    runtime = _make_runtime(telemetryctl=None, provider=provider, max_retries=1)
    cancelled = threading.Event()
    clock.on_sleep = cancelled.set

    with pytest.raises((RuntimeError, asyncio.CancelledError)):
        _call(runtime, timeout_s=120, cancel_event=cancelled)

    assert provider.calls == 1
    assert len(clock.sleeps) == 1


def test_wall_clock_jump_does_not_shorten_http_date_hint(clock: _FakeClock) -> None:
    provider = _SequenceProvider(
        [_rate_limited("Wed, 18 Nov 2026 12:00:30 GMT"), _make_response()]
    )
    runtime = _make_runtime(telemetryctl=None, provider=provider, max_retries=0)

    with flow.route_admission_scope():
        _call(runtime, timeout_s=None)
        clock.wall += 3600
        response = _call(runtime, timeout_s=120)

    assert response.ok is True
    assert provider.calls == 2
    assert sum(clock.sleeps) == pytest.approx(30.0)


def test_route_wait_that_fits_retries_primary_before_fallback(
    clock: _FakeClock,
) -> None:
    order: list[str] = []
    runtime, _, _ = _routed(
        order, [_rate_limited("30"), _make_response()], [_rate_limited()]
    )
    timer = ChatPhaseTimer()

    with flow.route_admission_scope():
        first = _call(runtime, timeout_s=120)
        second = _call(runtime, timeout_s=120, timer=timer)

    assert first.error is not None and first.error.code == "RATE_LIMITED"
    assert second.ok is True
    assert order == ["provider_a", "provider_b", "provider_a"]
    assert sum(clock.sleeps) == pytest.approx(30.0)
    assert _attempts(timer) == [("primary", "initial", "ok")]


@pytest.mark.parametrize("timeout_s", [5.0, None], ids=["no-fit", "no-deadline"])
def test_route_that_cannot_wait_is_skipped_for_matching_fallback(
    clock: _FakeClock, timeout_s: float | None
) -> None:
    order: list[str] = []
    runtime, _, _ = _routed(
        order, [_rate_limited("30")], [_rate_limited(), _make_response()]
    )
    timer = ChatPhaseTimer()

    with flow.route_admission_scope():
        _call(runtime, timeout_s=timeout_s)
        second = _call(runtime, timeout_s=timeout_s, timer=timer)

    assert second.ok is True
    assert order == ["provider_a", "provider_b", "provider_b"]
    assert clock.sleeps == []
    assert _attempts(timer) == [("fallback", "initial", "ok")]


def test_skipped_route_keeps_fallback_error_code_filter(clock: _FakeClock) -> None:
    order: list[str] = []
    runtime, _, fallback = _routed(
        order,
        [_rate_limited("30")],
        [_make_response()],
        fallback_on=("PROVIDER_ERROR",),
    )

    with flow.route_admission_scope():
        first = _call(runtime, timeout_s=None)
        second = _call(runtime, timeout_s=None)

    assert order == ["provider_a"]
    assert fallback.calls == 0
    assert second.error is not None and first.error is not None
    assert second.error.code == "RATE_LIMITED"
    assert second.error.details == first.error.details
    assert clock.sleeps == []


def test_route_without_hint_keeps_todays_order(clock: _FakeClock) -> None:
    order: list[str] = []
    runtime, _, _ = _routed(order, [_rate_limited()], [_rate_limited()])

    with flow.route_admission_scope():
        _call(runtime, timeout_s=120)
        _call(runtime, timeout_s=120)

    assert order == ["provider_a", "provider_b", "provider_a", "provider_b"]
    assert clock.sleeps == []


def test_route_admission_scope_resets_and_isolates_scopes() -> None:
    assert flow._route_admission.get() is None
    for error in (RuntimeError("boom"), asyncio.CancelledError()):
        with pytest.raises(type(error)):
            with flow.route_admission_scope():
                raise error
        assert flow._route_admission.get() is None

    with flow.route_admission_scope():
        outer = flow._route_admission.get()
        assert outer == {}
        outer[("a", "m")] = (1.0, _make_response())
        with flow.route_admission_scope():
            assert flow._route_admission.get() == {}
        assert flow._route_admission.get() is outer

        seen: dict[str, Any] = {}

        def _own_scope() -> None:
            seen["inherited"] = flow._route_admission.get()
            with flow.route_admission_scope():
                own = flow._route_admission.get()
                assert own is not None
                own[("b", "m")] = (2.0, _make_response())

        def _fresh_thread() -> None:
            seen["fresh"] = flow._route_admission.get()

        for thread in (
            threading.Thread(target=copy_context().run, args=(_own_scope,)),
            threading.Thread(target=_fresh_thread),
        ):
            thread.start()
            thread.join()

        assert seen["inherited"] is outer
        assert seen["fresh"] is None
        assert list(outer) == [("a", "m")]
    assert flow._route_admission.get() is None
