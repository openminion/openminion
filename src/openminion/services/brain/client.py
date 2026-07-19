import asyncio
import concurrent.futures
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, cast

from openminion.modules.llm.client_call import (
    extract_structured_response_fields as _extract_structured_response_fields,
    latest_prompt_and_history as _latest_prompt_and_history,
    llm_response_kwargs as _llm_response_kwargs,
    normalized_messages as _normalized_request_messages,
    normalized_provider_response as _normalized_provider_response,
    provider_tool_choice as _provider_tool_choice,
    provider_tools_from_request as _provider_tools_from_request,
    raw_response_model_name as _raw_response_model_name,
    request_metadata as _request_metadata,
    request_mode_name as _request_mode_name,
    request_purpose as _request_purpose,
    split_system_and_conversation as _split_system_and_conversation,
    token_usage_values as _token_usage_values,
    trim_submit_output_history as _trim_submit_output_history,
    usage_payload_from_response_usage as _usage_payload_from_response_usage,
)
from openminion.modules.llm.providers.base import (
    ProviderRequest,
    ProviderResponse,
)
from openminion.modules.llm.runtime.sync import run_async_compat
from openminion.modules.llm.schemas import LLMResponse
from openminion.modules.telemetry.constants import TRACE_HOME_ROOT_METADATA_KEY
from openminion.modules.telemetry.trace.structured import trace_context_payload
from openminion.services.agent.telemetry import (
    trace_provider_request,
    trace_provider_response,
)

_LOG = logging.getLogger(__name__)


class OpenMinionLLMClient:
    def __init__(
        self,
        provider: Any,
        *,
        invoke_provider_request: Callable[[ProviderRequest], Awaitable[Any]]
        | None = None,
        runtime_tools: list[Any] | None = None,
        telemetryctl: Any | None = None,
        home_root: Path | None = None,
    ) -> None:
        self.provider = provider
        self.name = provider.name
        self._invoke_provider_request = invoke_provider_request
        self._runtime_tools = list(runtime_tools or [])
        self._telemetryctl = telemetryctl
        self._home_root = home_root
        self._turn_id: str | None = None
        self._session_id: str | None = None
        self._trace_step: int = 0

    def _set_context(self, session_id: str, turn_id: str) -> None:
        self._session_id = session_id
        self._turn_id = turn_id
        self._trace_step = 0

    def _next_trace_step(self) -> int:
        self._trace_step += 1
        return self._trace_step

    async def _invoke(self, provider_request: ProviderRequest) -> Any:
        if self._invoke_provider_request is not None:
            return await self._invoke_provider_request(provider_request)
        return await self.provider.generate(provider_request)

    @staticmethod
    def _is_function_tool_choice(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        if str(value.get("type", "")).strip().lower() != "function":
            return False
        function_payload = value.get("function")
        if not isinstance(function_payload, dict):
            return False
        return bool(str(function_payload.get("name", "")).strip())

    def generate(self, provider_request: ProviderRequest) -> Any:
        return run_async_compat(self._invoke(provider_request))

    async def agenerate(self, provider_request: ProviderRequest) -> Any:
        return await self._invoke(provider_request)

    def _seed_trace_metadata(
        self,
        *,
        provider_req: ProviderRequest,
        metadata_payload: dict[str, str],
        inference_step: int,
        trace_label: str,
        trace_turn_id: str,
    ) -> dict[str, str]:
        provider_req.metadata = dict(getattr(provider_req, "metadata", {}) or {})
        if self._session_id and not provider_req.metadata.get("session_id"):
            provider_req.metadata["session_id"] = str(self._session_id)
        if self._home_root is not None:
            provider_req.metadata[TRACE_HOME_ROOT_METADATA_KEY] = str(self._home_root)
        provider_req.metadata["turn_id"] = trace_turn_id
        provider_req.metadata["inference_step"] = str(inference_step)
        provider_req.metadata["trace_label"] = trace_label
        provider_req.metadata.setdefault(
            "run_id",
            str(
                metadata_payload.get("run_id")
                or metadata_payload.get("request_id")
                or metadata_payload.get("trace_id")
                or ""
            ),
        )
        return dict(provider_req.metadata)

    def _trace_provider_request(
        self,
        *,
        req: Any,
        provider_req: ProviderRequest,
        trace_metadata: dict[str, str],
        trace_label: str,
        trace_turn_id: str,
        inference_step: int,
    ) -> dict[str, Any]:
        trace_context = trace_context_payload(
            session_id=str(trace_metadata.get("session_id", "") or ""),
            turn_id=trace_turn_id,
            inference_step=inference_step,
            label=trace_label,
            trace_id=str(trace_metadata.get("trace_id", "") or ""),
            agent_id=str(trace_metadata.get("agent_id", "") or ""),
            run_id=str(trace_metadata.get("run_id", "") or ""),
            provider=str(getattr(self.provider, "name", "") or ""),
            model=str(getattr(req, "model", "") or ""),
            home_root=self._home_root,
        )
        trace_provider_request(
            provider_request=provider_req,
            label=trace_label,
            provider_name=str(getattr(self.provider, "name", "") or ""),
            home_root=self._home_root,
            inbound_metadata=trace_metadata,
            turn_id=trace_turn_id,
            inference_step=inference_step,
            logger=_LOG,
        )
        return trace_context

    def _invoke_traced_provider(
        self,
        *,
        provider_req: ProviderRequest,
        trace_metadata: dict[str, str],
        trace_label: str,
        trace_turn_id: str,
        inference_step: int,
    ) -> Any:
        try:
            return run_async_compat(self._invoke(provider_req))
        except Exception as exc:
            trace_provider_response(
                provider_response=cast(
                    ProviderResponse,
                    SimpleNamespace(
                        model=str(getattr(self.provider, "name", "") or ""),
                        ok=False,
                        finish_reason="error",
                        output_text="",
                        tool_calls=[],
                        error=str(exc),
                    ),
                ),
                label=trace_label,
                provider_name=str(getattr(self.provider, "name", "") or ""),
                home_root=self._home_root,
                inbound_metadata=trace_metadata,
                turn_id=trace_turn_id,
                inference_step=inference_step,
                logger=_LOG,
            )
            raise

    def _trace_provider_response(
        self,
        *,
        response: ProviderResponse,
        raw_response: Any,
        structured_fields: dict[str, Any],
        trace_metadata: dict[str, str],
        trace_label: str,
        trace_turn_id: str,
        inference_step: int,
    ) -> None:
        raw_model_name = _raw_response_model_name(raw_response)
        trace_provider_response(
            provider_response=cast(
                ProviderResponse,
                SimpleNamespace(
                    text=str(response.text or ""),
                    model=str(response.model or raw_model_name or ""),
                    usage=dict(response.usage or {}),
                    tool_calls=list(response.tool_calls or []),
                    thinking=list(response.thinking or []),
                    finish_reason=str(response.finish_reason or ""),
                    normalization=dict(response.normalization or {}),
                    **structured_fields,
                ),
            ),
            label=trace_label,
            provider_name=str(getattr(self.provider, "name", "") or ""),
            home_root=self._home_root,
            inbound_metadata=trace_metadata,
            turn_id=trace_turn_id,
            inference_step=inference_step,
            logger=_LOG,
        )

    def _emit_llm_usage(
        self,
        *,
        usage_payload: dict[str, Any],
        mode_name: str | None,
    ) -> None:
        if not (self._telemetryctl and self._turn_id and self._session_id):
            return
        _prompt, _completion, _total, input_tokens, output_tokens, cached_tokens = (
            _token_usage_values(usage_payload)
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            self._emit_llm_usage_in_background(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                mode_name=mode_name,
            )
            return
        asyncio.run(
            self._telemetryctl.emit_llm_call(
                self._session_id,
                self._turn_id,
                input_tokens,
                output_tokens,
                cached_tokens,
                mode_name,
            )
        )

    def _emit_llm_usage_in_background(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
        mode_name: str | None,
    ) -> None:
        telemetryctl = self._telemetryctl
        if telemetryctl is None:
            return

        def emit_in_background() -> None:
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                new_loop.run_until_complete(
                    telemetryctl.emit_llm_call(
                        self._session_id,
                        self._turn_id,
                        input_tokens,
                        output_tokens,
                        cached_tokens,
                        mode_name,
                    )
                )
            finally:
                new_loop.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(emit_in_background)

    def call(self, req: Any) -> LLMResponse:
        metadata_payload = _request_metadata(req)
        sys_prompt, conversational = _split_system_and_conversation(
            _normalized_request_messages(req)
        )
        latest_msg, history = _latest_prompt_and_history(
            conversational=conversational,
            metadata=metadata_payload,
        )
        tools = _provider_tools_from_request(req)
        tool_choice = _provider_tool_choice(req)
        purpose = _request_purpose(metadata_payload)
        if tools and all(str(spec.name).strip() == "submit_output" for spec in tools):
            if not self._is_function_tool_choice(tool_choice):
                tool_choice = "required"
            history = _trim_submit_output_history(
                tools=tools,
                history=history,
                purpose=purpose,
            )

        provider_req = ProviderRequest(
            user_message=latest_msg,
            system_prompt=sys_prompt,
            history=history,
            tools=tools,
            tool_choice=tool_choice,
            metadata=metadata_payload,
        )
        inference_step = self._next_trace_step()
        trace_label = f"call{inference_step:02d}"
        trace_turn_id = str(self._turn_id or "turn")
        trace_metadata = self._seed_trace_metadata(
            provider_req=provider_req,
            metadata_payload=metadata_payload,
            inference_step=inference_step,
            trace_label=trace_label,
            trace_turn_id=trace_turn_id,
        )
        trace_context = self._trace_provider_request(
            req=req,
            provider_req=provider_req,
            trace_metadata=trace_metadata,
            trace_label=trace_label,
            trace_turn_id=trace_turn_id,
            inference_step=inference_step,
        )
        raw_resp = self._invoke_traced_provider(
            provider_req=provider_req,
            trace_metadata=trace_metadata,
            trace_label=trace_label,
            trace_turn_id=trace_turn_id,
            inference_step=inference_step,
        )
        resp = _normalized_provider_response(
            raw_response=raw_resp,
            provider_name=str(getattr(self.provider, "name", "provider")),
            provider_request=provider_req,
        )
        structured_fields = _extract_structured_response_fields(raw_resp)
        self._trace_provider_response(
            response=resp,
            raw_response=raw_resp,
            structured_fields=structured_fields,
            trace_metadata=trace_metadata,
            trace_label=trace_label,
            trace_turn_id=trace_turn_id,
            inference_step=inference_step,
        )
        self._emit_llm_usage(
            usage_payload=_usage_payload_from_response_usage(resp.usage),
            mode_name=_request_mode_name(metadata_payload),
        )
        return LLMResponse(
            **_llm_response_kwargs(
                resp=resp,
                req=req,
                client_name=str(self.name),
                structured_fields=structured_fields,
                trace_context=trace_context,
            )
        )
