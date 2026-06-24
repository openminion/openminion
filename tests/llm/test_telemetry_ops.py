from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict

import pytest

from openminion.modules.llm import LLMCTL
from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.schemas import (
    LLMRequest,
    LLMResponse,
    Message,
    UsageInfo,
)
from openminion.modules.llm.diagnostics.events import emit_llm_operation
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
    telemetryctl: TelemetryCtl,
    provider: _SequenceProvider,
) -> LLMCTL:
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {
                "default_provider": provider.name,
                "default_model": "telemetry-model",
                "retries": {"max_retries": 1, "backoff_ms": 0},
            },
            "providers": {provider.name: {}},
            "agents": {
                "default": {
                    "default_provider": provider.name,
                    "default_model": "telemetry-model",
                }
            },
        },
        telemetryctl=telemetryctl,
    )
    runtime.registry.add(provider)
    return runtime


def _request_payload(session_id: str, turn_id: str) -> Dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": "hello telemetry"}],
        "metadata": {
            "session_id": session_id,
            "turn_id": turn_id,
            "trace_id": "trace-1",
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
