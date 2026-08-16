from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from openminion.modules.llm.providers.base import ProviderRequest, ProviderResponse
from openminion.services.agent.telemetry import generate_with_provider_call_telemetry


class _TelemetryCtl:
    def __init__(self, failures: set[str]) -> None:
        self.failures = failures

    async def emit_canonical_event(
        self,
        _session_id: str,
        _turn_id: str,
        event_type: str,
        _payload: dict[str, object],
        **_kwargs: object,
    ) -> None:
        if event_type in self.failures:
            raise RuntimeError(f"exporter failed for {event_type}")


def _service_port(failures: set[str]) -> object:
    return SimpleNamespace(
        _service=SimpleNamespace(
            _telemetryctl=_TelemetryCtl(failures),
            _logger=logging.getLogger(__name__),
        )
    )


async def _call(*, service_port: object, generate):  # type: ignore[no-untyped-def]
    return await generate_with_provider_call_telemetry(
        service_port=service_port,
        request=ProviderRequest(
            user_message="secret prompt",
            system_prompt="system",
        ),
        session_id="session-1",
        turn_id="turn-1",
        provider_name="provider-1",
        generate=generate,
    )


@pytest.mark.asyncio
async def test_started_emit_failure_still_calls_provider_once() -> None:
    calls = 0

    async def _generate() -> ProviderResponse:
        nonlocal calls
        calls += 1
        return ProviderResponse(text="ok", model="model")

    response = await _call(
        service_port=_service_port({"llm.call.started"}),
        generate=_generate,
    )

    assert calls == 1
    assert response.text == "ok"


@pytest.mark.asyncio
async def test_completed_emit_failure_returns_provider_response() -> None:
    async def _generate() -> ProviderResponse:
        return ProviderResponse(text="ok", model="model")

    response = await _call(
        service_port=_service_port({"llm.call.completed"}),
        generate=_generate,
    )

    assert response.text == "ok"


@pytest.mark.asyncio
async def test_failed_emit_failure_preserves_provider_exception() -> None:
    provider_error = ValueError("provider outcome")

    async def _generate() -> ProviderResponse:
        raise provider_error

    with pytest.raises(ValueError) as raised:
        await _call(
            service_port=_service_port({"llm.call.failed"}),
            generate=_generate,
        )

    assert raised.value is provider_error


@pytest.mark.asyncio
async def test_cancellation_remains_cancellation() -> None:
    async def _generate() -> ProviderResponse:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _call(service_port=_service_port(set()), generate=_generate)


@pytest.mark.asyncio
async def test_warning_contains_no_prompt_or_provider_error(caplog) -> None:  # type: ignore[no-untyped-def]
    async def _generate() -> ProviderResponse:
        return ProviderResponse(text="provider secret", model="model")

    with caplog.at_level(logging.WARNING):
        await _call(
            service_port=_service_port({"llm.call.completed"}),
            generate=_generate,
        )

    assert "secret prompt" not in caplog.text
    assert "provider secret" not in caplog.text
    assert "exporter failed" not in caplog.text
