from types import SimpleNamespace
import logging

import pytest

from openminion.modules.llm.providers.base import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from openminion.modules.telemetry.trace.layout import (
    resolve_trace_root,
    write_protected_trace_file,
)
from openminion.modules.telemetry.trace.structured import (
    TraceArtifactPublication,
    trace_context_payload,
)
from openminion.services.agent.telemetry import (
    generate_with_provider_call_telemetry,
    trace_provider_response,
)


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
        metadata={"purpose": "act", "request_id": "req-1", "trace_id": "trace-1"},
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
            cost_usd=0.001,
        )

    await generate_with_provider_call_telemetry(
        service_port=service_port,
        request=_request(),
        session_id="session-1",
        turn_id="turn-1",
        provider_name="provider-1",
        service_vendor="cortensor",
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
    assert started["provider_name"] == "provider-1"
    assert started["provider"] == "provider-1"
    assert started["service_vendor"] == "cortensor"
    assert started["request_id"] == "req-1"
    assert started["trace_id"] == "trace-1"
    assert completed["trace_artifact_paths"] == [
        "llm/request.json",
        "llm/response.json",
        "llm/structured.json",
    ]
    assert completed["trace_artifacts_complete"] is True
    assert completed["cost_usd"] == 0.001
    assert completed["cost_source"] == "provider"
    assert completed["provider"] == "provider-1"
    assert completed["model"] == "model-1"


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
        raise ProviderError(
            "capacity unavailable",
            code="RATE_LIMITED",
            details={"upstream_code": "capacity", "request_id": "req-1"},
        )

    with pytest.raises(ProviderError, match="capacity unavailable"):
        await generate_with_provider_call_telemetry(
            service_port=service_port,
            request=_request(),
            session_id="session-1",
            turn_id="turn-1",
            provider_name="provider-1",
            service_vendor="cortensor",
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
    assert failed["service_vendor"] == "cortensor"
    assert failed["error"] == {
        "type": "ProviderError",
        "code": "RATE_LIMITED",
        "message": "capacity unavailable",
        "details": {"upstream_code": "capacity", "request_id": "req-1"},
    }


@pytest.mark.asyncio
async def test_failed_call_does_not_publish_untyped_exception_text() -> None:
    order: list[str] = []
    ctl = _TelemetryCtl(order)
    service_port = SimpleNamespace(_service=SimpleNamespace(_telemetryctl=ctl))

    async def generate() -> ProviderResponse:
        raise RuntimeError("Bearer must-not-leak")

    with pytest.raises(RuntimeError, match="must-not-leak"):
        await generate_with_provider_call_telemetry(
            service_port=service_port,
            request=_request(),
            session_id="session-1",
            turn_id="turn-1",
            provider_name="provider-1",
            generate=generate,
        )

    assert ctl.events[-1][1]["error"] == {"type": "RuntimeError"}


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


def test_response_publication_includes_existing_sse_transport_trace(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    trace_context = trace_context_payload(
        session_id="session-1",
        turn_id="turn-1",
        inference_step=1,
        label="call01",
        home_root=tmp_path,
    )
    relative = trace_context["http_sse_response_trace_filename"]
    trace_path = resolve_trace_root(home_root=tmp_path) / relative
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    write_protected_trace_file(trace_path, "{}")

    publication = trace_provider_response(
        provider_response=ProviderResponse(text="ok", model="model-1"),
        label="call01",
        provider_name="provider-1",
        home_root=tmp_path,
        inbound_metadata={"session_id": "session-1"},
        turn_id="turn-1",
        inference_step=1,
        logger=logging.getLogger(__name__),
    )

    assert relative in publication.paths
