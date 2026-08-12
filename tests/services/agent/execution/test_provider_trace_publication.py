from types import SimpleNamespace

import pytest

from openminion.modules.llm.providers.base import ProviderRequest, ProviderResponse
from openminion.modules.telemetry.trace.structured import TraceArtifactPublication
from openminion.services.agent.telemetry import generate_with_provider_call_telemetry


class _TelemetryCtl:
    def __init__(self, order: list[str]) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []
        self.order = order

    async def emit_canonical_event(
        self,
        _session_id: str,
        _turn_id: str,
        event_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> None:
        self.order.append(event_type)
        self.events.append((event_type, payload))


def _request() -> ProviderRequest:
    return ProviderRequest(
        user_message="hello",
        system_prompt="system",
        metadata={"purpose": "act"},
    )


@pytest.mark.asyncio
async def test_response_artifacts_publish_before_completed_event() -> None:
    order: list[str] = []
    ctl = _TelemetryCtl(order)
    service_port = SimpleNamespace(_service=SimpleNamespace(_telemetryctl=ctl))
    publication = TraceArtifactPublication(("llm/request.json",), True)

    async def generate() -> ProviderResponse:
        nonlocal publication
        order.append("response_artifacts_published")
        publication = publication.merge(
            TraceArtifactPublication(
                ("llm/response.json", "llm/structured.json"),
                True,
            )
        )
        return ProviderResponse(
            text="ok",
            model="model-1",
            usage={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
        )

    await generate_with_provider_call_telemetry(
        service_port=service_port,
        request=_request(),
        session_id="session-1",
        turn_id="turn-1",
        provider_name="provider-1",
        generate=generate,
        trace_publication=lambda: publication,
    )

    assert order == [
        "llm.call.started",
        "response_artifacts_published",
        "llm.call.completed",
    ]
    started = ctl.events[0][1]
    completed = ctl.events[1][1]
    assert started["trace_artifact_paths"] == ["llm/request.json"]
    assert started["trace_artifacts_complete"] is False
    assert completed["trace_artifact_paths"] == [
        "llm/request.json",
        "llm/response.json",
        "llm/structured.json",
    ]
    assert completed["trace_artifacts_complete"] is True


@pytest.mark.asyncio
async def test_failed_call_retains_published_request_and_raw_artifacts() -> None:
    order: list[str] = []
    ctl = _TelemetryCtl(order)
    service_port = SimpleNamespace(_service=SimpleNamespace(_telemetryctl=ctl))
    publication = TraceArtifactPublication(
        ("llm/request.json", "llm/request-raw.txt"),
        True,
    )

    async def generate() -> ProviderResponse:
        order.append("provider_failed")
        raise RuntimeError("provider failed")

    with pytest.raises(RuntimeError, match="provider failed"):
        await generate_with_provider_call_telemetry(
            service_port=service_port,
            request=_request(),
            session_id="session-1",
            turn_id="turn-1",
            provider_name="provider-1",
            generate=generate,
            trace_publication=lambda: publication,
        )

    assert order == ["llm.call.started", "provider_failed", "llm.call.failed"]
    failed = ctl.events[-1][1]
    assert failed["trace_artifact_paths"] == [
        "llm/request.json",
        "llm/request-raw.txt",
    ]
    assert failed["trace_artifacts_complete"] is True


@pytest.mark.asyncio
async def test_disabled_tracing_records_a_complete_empty_terminal() -> None:
    order: list[str] = []
    ctl = _TelemetryCtl(order)
    service_port = SimpleNamespace(_service=SimpleNamespace(_telemetryctl=ctl))
    publication = TraceArtifactPublication()

    await generate_with_provider_call_telemetry(
        service_port=service_port,
        request=_request(),
        session_id="session-1",
        turn_id="turn-1",
        provider_name="provider-1",
        generate=lambda: _response(),
        trace_publication=lambda: publication,
    )

    completed = ctl.events[-1][1]
    assert completed["trace_artifact_paths"] == []
    assert completed["trace_artifacts_complete"] is True


async def _response() -> ProviderResponse:
    return ProviderResponse(text="ok", model="model-1")
