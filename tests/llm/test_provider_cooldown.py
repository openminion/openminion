from __future__ import annotations

import asyncio
from typing import Any, Dict

from openminion.modules.llm import LLMCTL
from openminion.modules.llm.health import (
    ProviderHealthRegistry,
    parse_retry_after_seconds,
)
from openminion.modules.llm.schemas import (
    LLMResponse,
    Message,
    ResponseError,
    UsageInfo,
)


class _FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _response(
    *,
    ok: bool,
    provider: str,
    model: str,
    code: str = "",
    details: Dict[str, Any] | None = None,
) -> LLMResponse:
    return LLMResponse(
        ok=ok,
        provider=provider,
        model=model,
        output_text="ok" if ok else "",
        assistant_messages=[Message(role="assistant", content="ok")] if ok else [],
        tool_calls=[],
        usage=UsageInfo(input_tokens=1, output_tokens=1, total_tokens=2),
        latency_ms=1,
        finish_reason="stop" if ok else "error",
        provider_raw=None,
        error=None
        if ok
        else ResponseError(code=code, message=code, details=details or {}),
    )


class _Provider:
    contract_version = "v1"
    provider_interface_version = "v1"

    def __init__(self, name: str, model: str, results: list[Any]) -> None:
        self.name = name
        self.model = model
        self._results = list(results)
        self.calls = 0

    def complete(self, request: Any, config: Dict[str, Any]) -> LLMResponse:
        del request, config
        self.calls += 1
        outcome = self._results[min(self.calls - 1, len(self._results) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def list_models(self, config: Dict[str, Any]) -> list[str]:
        del config
        return [self.model]

    def healthcheck(self, config: Dict[str, Any]) -> Dict[str, Any]:
        del config
        return {"ok": True}


def _runtime(*, primary: _Provider, fallback: _Provider | None = None) -> Any:
    payload: Dict[str, Any] = {
        "version": 1,
        "llmctl": {
            "default_provider": primary.name,
            "default_model": primary.model,
            "retries": {"max_retries": 0, "backoff_ms": 0},
        },
        "providers": {primary.name: {}},
        "agents": {
            "default": {
                "default_provider": primary.name,
                "default_model": primary.model,
                "tool_policy": {"enable_tools": True},
            }
        },
    }
    payload["providers"][primary.name] = {}
    if fallback is not None:
        payload["providers"][fallback.name] = {}
        payload["llmctl"]["routing_defaults"] = {
            "primary": {"provider": primary.name, "model": primary.model},
            "fallbacks": [
                {
                    "provider": fallback.name,
                    "model": fallback.model,
                    "on": ["RATE_LIMITED"],
                }
            ],
        }
    runtime = LLMCTL.from_config(payload)
    runtime.registry.add(primary)
    if fallback is not None:
        runtime.registry.add(fallback)
    return runtime


def _call(runtime: Any, *, session: str) -> LLMResponse:
    request = {
        "messages": [{"role": "user", "content": "hello"}],
        "metadata": {
            "session_id": session,
            "turn_id": "turn-1",
            "trace_id": "trace-1",
            "llm_call_id": "call-1",
            "purpose": "entry",
        },
    }
    return asyncio.run(runtime.client(agent_name="default").call(request))


# ---------------------------------------------------------------- registry units


def test_registry_records_and_expires_a_cooldown() -> None:
    clock = _FakeClock()
    registry = ProviderHealthRegistry(clock=clock)

    cooldown = registry.record_failure(
        "p", "m", reason="RATE_LIMITED", retry_after_s=30
    )
    assert cooldown is not None and cooldown.remaining_s == 30.0
    active = registry.cooldown("p", "m")
    assert active is not None and active.remaining_s == 30.0

    clock.advance(30.0001)
    assert registry.cooldown("p", "m") is None
    assert registry.snapshot() == []


def test_registry_honours_retry_after_over_backoff_and_escalates() -> None:
    clock = _FakeClock()
    registry = ProviderHealthRegistry(clock=clock)

    first = registry.record_failure(
        "p", "m", reason="RATE_LIMITED", retry_after_s=30, backoff_ms=300
    )
    assert first is not None and first.remaining_s == 30.0
    clock.advance(31)
    second = registry.record_failure("p", "m", reason="RATE_LIMITED", backoff_ms=300)
    assert second is not None and second.remaining_s == 0.6
    clock.advance(1)
    third = registry.record_failure("p", "m", reason="RATE_LIMITED", backoff_ms=300)
    assert third is not None and third.remaining_s == 1.2


def test_registry_success_clears_and_zero_wait_is_not_recorded() -> None:
    registry = ProviderHealthRegistry(clock=_FakeClock())
    assert registry.record_failure("p", "m", reason="TIMEOUT") is None
    assert registry.cooldown("p", "m") is None
    registry.record_failure("p", "m", reason="RATE_LIMITED", retry_after_s=5)
    registry.record_success("p", "m")
    assert registry.cooldown("p", "m") is None
    assert registry.snapshot() == []


def test_registry_clamps_to_ceiling() -> None:
    registry = ProviderHealthRegistry(max_cooldown_s=10.0, clock=_FakeClock())
    cooldown = registry.record_failure(
        "p", "m", reason="RATE_LIMITED", retry_after_s=3600
    )
    assert cooldown is not None and cooldown.remaining_s == 10.0


def test_parse_retry_after_seconds_forms() -> None:
    assert parse_retry_after_seconds("30") == 30.0
    assert parse_retry_after_seconds("0") == 0.0
    assert parse_retry_after_seconds("") is None
    assert parse_retry_after_seconds("not-a-value") is None
    future = parse_retry_after_seconds("Wed, 21 Oct 2099 07:28:00 GMT")
    assert future is not None and future > 0


# ------------------------------------------------------------- runtime integration


def test_rate_limited_response_cools_the_provider_for_the_next_call() -> None:
    primary = _Provider(
        "primary",
        "primary-model",
        [
            _response(
                ok=False,
                provider="primary",
                model="primary-model",
                code="RATE_LIMITED",
                details={"retry_after": "30"},
            )
        ],
    )
    runtime = _runtime(primary=primary)

    first = _call(runtime, session="cooldown-1")
    assert first.ok is False and first.error is not None
    assert first.error.code == "RATE_LIMITED"
    assert primary.calls == 1

    second = _call(runtime, session="cooldown-2")
    assert second.ok is False and second.error is not None
    assert second.error.code == "RATE_LIMITED"
    assert second.error.details["retry_after_s"] == 30.0
    assert second.error.details["cooldown_reason"] == "RATE_LIMITED"
    assert primary.calls == 1, "a cooling provider must not be called again"


def test_cooling_primary_lets_routing_use_the_configured_fallback() -> None:
    primary = _Provider(
        "primary",
        "primary-model",
        [
            _response(
                ok=False,
                provider="primary",
                model="primary-model",
                code="RATE_LIMITED",
                details={"retry_after": "30"},
            )
        ],
    )
    fallback = _Provider(
        "fallback",
        "fallback-model",
        [_response(ok=True, provider="fallback", model="fallback-model")],
    )
    runtime = _runtime(primary=primary, fallback=fallback)

    first = _call(runtime, session="fallback-1")
    assert first.ok is True and first.provider == "fallback"
    assert (primary.calls, fallback.calls) == (1, 1)

    second = _call(runtime, session="fallback-2")
    assert second.ok is True and second.provider == "fallback"
    assert primary.calls == 1, "the cooling primary is skipped on the next turn"
    assert fallback.calls == 2


def test_success_leaves_no_cooldown_behind() -> None:
    primary = _Provider(
        "primary",
        "primary-model",
        [_response(ok=True, provider="primary", model="primary-model")],
    )
    runtime = _runtime(primary=primary)
    assert _call(runtime, session="healthy-1").ok is True
    assert runtime.client(agent_name="default") is not None
    assert primary.calls == 1
    assert _call(runtime, session="healthy-2").ok is True
    assert primary.calls == 2


def test_cooling_error_preserves_upstream_provider_facts() -> None:
    primary = _Provider(
        "primary",
        "primary-model",
        [
            _response(
                ok=False,
                provider="primary",
                model="primary-model",
                code="RATE_LIMITED",
                details={
                    "status_code": 429,
                    "reason_code": "provider_rate_limited",
                    "retry_after": "12",
                },
            )
        ],
    )
    runtime = _runtime(primary=primary)

    first = _call(runtime, session="facts-1")
    assert first.ok is False and first.error is not None

    second = _call(runtime, session="facts-2")
    assert second.ok is False and second.error is not None
    assert second.error.code == "RATE_LIMITED"
    assert second.error.details["status_code"] == 429
    assert second.error.details["reason_code"] == "provider_rate_limited"
    assert second.error.details["retry_after_s"] == 12.0
    assert second.error.details["cooldown_reason"] == "RATE_LIMITED"
    assert primary.calls == 1
