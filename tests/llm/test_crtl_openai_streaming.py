from __future__ import annotations

import json
from typing import Any, Dict, Iterator
from unittest import mock

from openminion.modules.llm.providers.openai.adapter import OpenAIProvider
from openminion.modules.llm.schemas import LLMRequest, LLMResponse, LLMStreamEvent


def _make_request() -> LLMRequest:
    return LLMRequest(
        provider="openai",
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": "hi"}],
    )


def _sse_lines_from_chunks(chunks: list[dict]) -> list[str]:
    lines = ["data: " + json.dumps(chunk) for chunk in chunks]
    lines.append("data: [DONE]")
    return lines


def _delta_chunk(content: str) -> dict:
    return {"choices": [{"delta": {"content": content}}]}


def _config_for_provider() -> Dict[str, Any]:
    return {
        "api_key": "fake-test-key",
        "base_url": "https://api.example.com/v1",
        "timeouts": {"total_seconds": 10},
    }


def test_stream_yields_deltas_before_terminal_done():

    provider = OpenAIProvider()
    chunks = [_delta_chunk("Hey"), _delta_chunk("! "), _delta_chunk("How can I help?")]
    sse_lines = _sse_lines_from_chunks(chunks)

    with mock.patch(
        "openminion.modules.llm.providers.openai.adapter.iter_sse_post_lines",
        return_value=iter(sse_lines),
    ):
        events = list(provider.stream(_make_request(), _config_for_provider()))

    delta_indices = [i for i, e in enumerate(events) if e.type == "delta"]
    done_indices = [i for i, e in enumerate(events) if e.type == "done"]
    assert delta_indices, "at least one delta expected"
    assert done_indices, "terminal done expected"
    assert min(delta_indices) < min(done_indices), (
        "discipline #1: text-delta event index must be lower than final/done"
    )


def test_stream_deltas_carry_content_in_emission_order():
    provider = OpenAIProvider()
    sse_lines = _sse_lines_from_chunks(
        [_delta_chunk("A"), _delta_chunk("B"), _delta_chunk("C")]
    )
    with mock.patch(
        "openminion.modules.llm.providers.openai.adapter.iter_sse_post_lines",
        return_value=iter(sse_lines),
    ):
        events = list(provider.stream(_make_request(), _config_for_provider()))
    deltas = [e.delta_text for e in events if e.type == "delta"]
    assert deltas == ["A", "B", "C"]


def test_stream_terminates_with_done_even_when_provider_yields_no_deltas():

    provider = OpenAIProvider()
    with mock.patch(
        "openminion.modules.llm.providers.openai.adapter.iter_sse_post_lines",
        return_value=iter(["data: [DONE]"]),
    ):
        events = list(provider.stream(_make_request(), _config_for_provider()))
    assert any(e.type == "done" for e in events)


# Discipline 2: non-streaming fallback degrades cleanly to final-only


def test_protocol_default_stream_falls_back_to_complete_for_non_streaming_providers():

    from openminion.modules.llm.providers.contract import StubProvider

    stub = StubProvider()
    request = LLMRequest(
        provider="stub",
        model="stub-v1",
        messages=[{"role": "user", "content": "ping"}],
    )
    events = list(stub.stream(request, {}))
    types = [e.type for e in events]
    assert "done" in types, "fallback must terminate with done"


def test_llmclient_stream_propagates_terminal_done_when_provider_yields_one():

    fake_provider = mock.Mock()
    fake_provider.stream = mock.Mock(
        return_value=iter(
            [
                LLMStreamEvent(type="delta", delta_text="hello"),
                LLMStreamEvent(type="done"),
            ]
        )
    )
    events = _drive_client_stream(fake_provider)
    done_count = sum(1 for e in events if e.type == "done")
    assert done_count == 1, "expected exactly one terminal done"


def test_llmclient_stream_synthesizes_done_when_provider_forgets():

    fake_provider = mock.Mock()
    fake_provider.stream = mock.Mock(
        return_value=iter(
            [
                LLMStreamEvent(type="delta", delta_text="hello"),
            ]
        )
    )
    events = _drive_client_stream(fake_provider)
    assert events[-1].type == "done"


def test_stream_emits_typed_error_on_auth_failure():

    from openminion.modules.llm.errors import LLMCtlError

    provider = OpenAIProvider()
    with mock.patch(
        "openminion.modules.llm.providers.openai.adapter._resolve_api_key",
        side_effect=LLMCtlError("AUTH_ERROR", "missing api_key"),
    ):
        events = list(provider.stream(_make_request(), {}))
    types = [e.type for e in events]
    assert "error" in types
    assert types[-1] == "done"
    err_event = next(e for e in events if e.type == "error")
    assert err_event.error is not None
    assert err_event.error.code == "AUTH_ERROR"


def test_stream_emits_typed_error_on_mid_stream_provider_failure():

    from openminion.modules.llm.errors import LLMCtlError

    def _raising_lines():
        yield "data: " + json.dumps(_delta_chunk("Hey"))
        raise LLMCtlError("PROVIDER_ERROR", "stream broke")

    provider = OpenAIProvider()
    with mock.patch(
        "openminion.modules.llm.providers.openai.adapter.iter_sse_post_lines",
        return_value=_raising_lines(),
    ):
        events = list(provider.stream(_make_request(), _config_for_provider()))
    types = [e.type for e in events]
    assert types == ["delta", "error", "done"]


def test_llmclient_stream_wraps_unexpected_provider_exceptions_as_typed_error():

    def _raising_iter():
        yield LLMStreamEvent(type="delta", delta_text="boom")
        raise RuntimeError("provider exploded")

    fake_provider = mock.Mock()
    fake_provider.stream = mock.Mock(return_value=_raising_iter())
    events = _drive_client_stream(fake_provider)
    types = [e.type for e in events]
    assert "error" in types
    assert types[-1] == "done"


def test_llmclient_stream_emits_error_when_provider_unknown():

    from openminion.modules.llm.runtime.client import LLMClient

    fake_llmctl = mock.MagicMock()
    fake_llmctl.registry.get.side_effect = KeyError("no such provider")
    fake_profile = mock.MagicMock()

    client = LLMClient.__new__(LLMClient)
    client.llmctl = fake_llmctl
    client.profile = fake_profile
    client._telemetryctl = None

    with mock.patch.object(
        client,
        "_resolve_provider_and_model",
        return_value=("ghost", "ghost-v1", None),
    ):
        events = list(client.stream(messages=[{"role": "user", "content": "hi"}]))
    types = [e.type for e in events]
    assert types == ["error", "done"]


def test_openai_adapter_never_yields_thinking_chunks_in_v1():

    import typing

    from openminion.modules.llm.schemas import LLMStreamEvent as _Event

    type_field = _Event.model_fields["type"]
    type_args = typing.get_args(type_field.annotation)
    assert "thinking" not in type_args, (
        "discipline #4: thinking chunks must not be in the closed-set "
        "LLMStreamEvent.type Literal in v1"
    )
    assert set(type_args) == {"delta", "done", "error"}, (
        "v1 closed set must be exactly {delta, done, error}"
    )


def test_openai_stream_does_not_yield_unknown_event_types():

    provider = OpenAIProvider()
    chunks = [_delta_chunk("hello"), _delta_chunk(" world")]
    sse_lines = _sse_lines_from_chunks(chunks)
    with mock.patch(
        "openminion.modules.llm.providers.openai.adapter.iter_sse_post_lines",
        return_value=iter(sse_lines),
    ):
        events = list(provider.stream(_make_request(), _config_for_provider()))
    for e in events:
        assert e.type in {"delta", "done", "error"}


def test_complete_rejection_message_points_at_llmclient_stream_method():

    from openminion.modules.llm.runtime.client import LLMClient

    fake_llmctl = mock.MagicMock()
    fake_profile = mock.MagicMock()

    client = LLMClient.__new__(LLMClient)
    client.llmctl = fake_llmctl
    client.profile = fake_profile
    client._telemetryctl = None

    req = LLMRequest(
        provider="openai",
        model="x",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )
    response: LLMResponse = client._call_sync_impl(req)
    assert response.error is not None
    assert "LLMClient.stream()" in response.error.message
    assert response.error.details.get("contract_posture") == (
        "use_llmclient_stream_method"
    )


def _drive_client_stream(fake_provider: Any) -> list[LLMStreamEvent]:

    from openminion.modules.llm.runtime.client import LLMClient

    fake_llmctl = mock.MagicMock()
    fake_llmctl.registry.get.return_value = fake_provider
    fake_llmctl.config.llmctl.timeouts.model_dump.return_value = {"total_seconds": 10}

    fake_profile = mock.MagicMock()

    client = LLMClient.__new__(LLMClient)
    client.llmctl = fake_llmctl
    client.profile = fake_profile
    client._telemetryctl = None

    with (
        mock.patch.object(
            client,
            "_resolve_provider_and_model",
            return_value=("openai", "gpt-4.1-mini", None),
        ),
        mock.patch(
            "openminion.modules.llm.runtime.client.resolve_provider_config",
            return_value={"api_key": "x", "base_url": "https://x"},
        ),
    ):
        return list(client.stream(messages=[{"role": "user", "content": "hi"}]))


_ = Iterator
