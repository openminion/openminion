import time
from typing import Any, Dict, List

from ...contracts.adapter import (
    ProviderAdapterResult,
    adapter_result_to_llm_response,
)
from ...interfaces import LLM_RESPONSE_INTERFACE_VERSION
from ...schemas import LLMRequest, LLMResponse, Message, UsageInfo
from ..message_payloads import _last_user_text
from ..contract import PROVIDER_INTERFACE_VERSION


class EchoProvider:
    name = "echo"
    contract_version = LLM_RESPONSE_INTERFACE_VERSION
    provider_interface_version = PROVIDER_INTERFACE_VERSION

    def complete(self, request: LLMRequest, config: Dict[str, Any]) -> LLMResponse:
        del config
        started = time.perf_counter()
        text = _last_user_text(request.messages)
        thinking = str(request.metadata.get("thinking", "")).strip().lower()
        suffix = "" if not thinking or thinking == "off" else f" (thinking={thinking})"
        output = f"{text}{suffix}".strip()
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        return adapter_result_to_llm_response(
            ProviderAdapterResult(
                provider=self.name,
                model=request.model or "echo",
                output_text=output,
                assistant_messages=[Message(role="assistant", content=output)]
                if output
                else [],
                tool_calls=[],
                usage=UsageInfo(),
                latency_ms=elapsed_ms,
                finish_reason="stop",
                provider_raw={"provider": self.name},
                normalization_meta={"adapter": "echo"},
            )
        )

    def list_models(self, config: Dict[str, Any]) -> List[str]:
        del config
        return ["echo"]

    def healthcheck(self, config: Dict[str, Any]) -> Dict[str, Any]:
        del config
        return {"ok": True, "provider": self.name}


def echo_provider() -> EchoProvider:
    return EchoProvider()
