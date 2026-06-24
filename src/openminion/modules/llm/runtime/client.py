import asyncio
import ast
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Union

from pydantic import ValidationError

from ..constants import (
    LLM_TOOL_CALL_STATUS_CHOICES,
    LLM_TOOL_CALL_STATUS_PARSED,
    LLM_TOOL_CALL_STATUS_REQUESTED,
    LLM_TOOL_CHOICE_CHOICES,
)
from ..config import (
    AgentProfile,
    LLMCTLConfig,
    load_config,
    resolve_provider_config,
)
from ..contracts.adapter import ProviderAdapterResult, coerce_provider_output
from ..errors import LLMCtlError
from ..interfaces import LLM_RESPONSE_INTERFACE_VERSION
from ..providers.tool_calling.contracts import (
    detect_raw_envelope,
    detect_raw_xml_tool_wrapper,
    sanitize_envelope_leak,
)
from ..providers.tool_calling.normalizer import normalize_tool_calls
from ..providers.plugins import (
    ProviderRegistry,
    load_plugin_providers,
    register_builtin_providers,
)
from ..schemas import (
    LLMRequest,
    LLMResponse,
    LLMStreamEvent,
    Message,
    ResponseError,
    ToolCall,
    ToolSpec,
)
from .flow import (
    ToolPolicyContext as _ToolPolicyContext,
    _apply_budgets,
    _apply_cost_budget,
    _apply_tool_policy_post,
    _apply_tool_policy_pre,
    _cache_hit_payload,
    _call_with_retries,
    _emit_operation,
    _error_response,
    _execute_with_routing,
    _finalize_response,
    _merge_generation_params,
    _resolve_provider_and_model,
    _resolve_provider_cost_hint,
    _retry_config,
    parse_call_payload,
)
from .sync import run_async_compat

ToolPolicyContext = _ToolPolicyContext


class LLMCTL:
    contract_version = LLM_RESPONSE_INTERFACE_VERSION

    def __init__(
        self,
        config: LLMCTLConfig,
        registry: ProviderRegistry,
        provider_statuses: List[Dict[str, Any]],
        telemetryctl: Any | None = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.provider_statuses = provider_statuses
        self.telemetryctl = telemetryctl

    @classmethod
    def from_config(
        cls,
        path_or_dict: Union[str, Path, Dict[str, Any], LLMCTLConfig],
        telemetryctl: Any | None = None,
    ) -> "LLMCTL":
        config = load_config(path_or_dict)
        registry = ProviderRegistry()
        statuses: List[Dict[str, Any]] = []
        statuses.extend(register_builtin_providers(registry))
        statuses.extend(load_plugin_providers(registry))
        return cls(
            config=config,
            registry=registry,
            provider_statuses=statuses,
            telemetryctl=telemetryctl,
        )

    def client(
        self,
        agent_name: Optional[Union[str, AgentProfile]] = None,
        profile: Optional[AgentProfile] = None,
    ) -> "LLMClient":
        if profile is not None:
            resolved_profile = profile
        elif isinstance(agent_name, AgentProfile):
            resolved_profile = agent_name
        elif isinstance(agent_name, str):
            if agent_name not in self.config.agents:
                raise LLMCtlError(
                    "INVALID_ARGUMENT",
                    f"Unknown agent profile: {agent_name}",
                    {"agent": agent_name},
                )
            resolved_profile = self.config.agents[agent_name]
        else:
            resolved_profile = AgentProfile(name="default")

        if not resolved_profile.name:
            resolved_profile = resolved_profile.model_copy(update={"name": "inline"})
        return LLMClient(
            self,
            resolved_profile,
            telemetryctl=self.telemetryctl,
        )

    def list_models(self, provider: str) -> List[str]:
        provider_obj = self.registry.get(provider)
        if not hasattr(provider_obj, "list_models"):
            return []
        cfg = resolve_provider_config(self.config, provider)
        return list(provider_obj.list_models(cfg))


class LLMClient:
    contract_version = LLM_RESPONSE_INTERFACE_VERSION

    def __init__(
        self,
        llmctl: LLMCTL,
        profile: AgentProfile,
        telemetryctl: Any | None = None,
    ) -> None:
        self.llmctl = llmctl
        self.profile = profile
        self._telemetryctl = telemetryctl

    _resolve_provider_and_model = _resolve_provider_and_model
    _merge_generation_params = _merge_generation_params
    _apply_budgets = _apply_budgets
    _apply_tool_policy_pre = _apply_tool_policy_pre
    _retry_config = _retry_config
    _emit_operation = _emit_operation
    _cache_hit_payload = staticmethod(_cache_hit_payload)
    _execute_with_routing = _execute_with_routing
    _call_with_retries = _call_with_retries
    _apply_tool_policy_post = _apply_tool_policy_post
    _apply_cost_budget = _apply_cost_budget
    _resolve_provider_cost_hint = _resolve_provider_cost_hint
    _finalize_response = _finalize_response
    _error_response = _error_response

    def _build_request_data(
        self,
        *,
        messages: List[Union[Message, Dict[str, Any]]],
        tools: Optional[List[Union[ToolSpec, Dict[str, Any]]]],
        overrides: Dict[str, Any],
        stream: bool = False,
    ) -> Dict[str, Any]:
        req_data: Dict[str, Any] = {
            "messages": [
                msg.model_dump() if isinstance(msg, Message) else msg
                for msg in messages
            ],
            "tools": [
                tool.model_dump() if isinstance(tool, ToolSpec) else tool
                for tool in tools
            ]
            if tools
            else None,
        }
        if stream:
            req_data["stream"] = True
        override_keys = [
            "provider",
            "model",
            "tool_choice",
            "temperature",
            "top_p",
            "max_output_tokens",
            "stop",
            "metadata",
        ]
        if not stream:
            override_keys.insert(-1, "stream")
        for key in override_keys:
            if key not in overrides:
                continue
            req_data[key] = (
                self._normalize_tool_choice_input(overrides[key])
                if key == "tool_choice"
                else overrides[key]
            )
        return req_data

    def complete(
        self,
        messages: List[Union[Message, Dict[str, Any]]],
        tools: Optional[List[Union[ToolSpec, Dict[str, Any]]]] = None,
        **overrides: Any,
    ) -> LLMResponse:
        req_data = self._build_request_data(
            messages=messages,
            tools=tools,
            overrides=overrides,
        )
        request = LLMRequest.model_validate(req_data)
        return self.call_sync(request=request, overrides=overrides)

    def stream(
        self,
        messages: List[Union[Message, Dict[str, Any]]],
        tools: Optional[List[Union[ToolSpec, Dict[str, Any]]]] = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        """Stream helper."""
        req_data = self._build_request_data(
            messages=messages,
            tools=tools,
            overrides=overrides,
            stream=True,
        )

        try:
            request = LLMRequest.model_validate(req_data)
        except ValidationError as exc:
            yield LLMStreamEvent(
                type="error",
                error=ResponseError(
                    code="INVALID_ARGUMENT",
                    message="Request schema validation failed",
                    details={"errors": exc.errors()},
                ),
            )
            yield LLMStreamEvent(type="done")
            return

        provider_name, model_name, _routing = self._resolve_provider_and_model(
            request, dict(overrides or {})
        )
        if not provider_name or not model_name:
            yield LLMStreamEvent(
                type="error",
                error=ResponseError(
                    code="INVALID_ARGUMENT",
                    message=(
                        f"Unable to resolve provider/model "
                        f"(provider={provider_name!r}, model={model_name!r})"
                    ),
                ),
            )
            yield LLMStreamEvent(type="done")
            return

        try:
            provider = self.llmctl.registry.get(provider_name)
        except KeyError:
            yield LLMStreamEvent(
                type="error",
                error=ResponseError(
                    code="INVALID_ARGUMENT",
                    message=f"Unknown provider: {provider_name}",
                ),
            )
            yield LLMStreamEvent(type="done")
            return

        cfg = resolve_provider_config(self.llmctl.config, provider_name)
        cfg["timeouts"] = self.llmctl.config.llmctl.timeouts.model_dump()
        call_request = request.model_copy(
            update={"provider": provider_name, "model": model_name}
        )

        emitted_done = False
        try:
            for event in provider.stream(call_request, cfg):
                if not isinstance(event, LLMStreamEvent):
                    continue
                if event.type == "done":
                    emitted_done = True
                yield event
        except Exception as exc:  # noqa: BLE001 - provider may raise unstructured
            yield LLMStreamEvent(
                type="error",
                error=ResponseError(
                    code="PROVIDER_ERROR",
                    message=f"provider stream raised: {exc}",
                ),
            )
            if not emitted_done:
                yield LLMStreamEvent(type="done")
            return
        if not emitted_done:
            yield LLMStreamEvent(type="done")

    @staticmethod
    def _normalize_tool_choice_input(raw_value: Any) -> Any:
        if isinstance(raw_value, str):
            normalized = raw_value.strip().lower()
            if normalized in LLM_TOOL_CHOICE_CHOICES:
                return normalized
            candidate = raw_value.strip()
            if candidate.startswith("{") and candidate.endswith("}"):
                for parser in (json.loads, ast.literal_eval):
                    try:
                        parsed = parser(candidate)
                    except Exception:
                        continue
                    if isinstance(parsed, dict):
                        return parsed
            return raw_value
        if isinstance(raw_value, dict):
            return dict(raw_value)
        return raw_value

    async def call(
        self,
        request: Union[LLMRequest, Dict[str, Any]],
        overrides: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        return await asyncio.to_thread(self._call_sync_impl, request, overrides)

    def call_sync(
        self,
        request: Union[LLMRequest, Dict[str, Any]],
        overrides: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        return run_async_compat(self.call(request=request, overrides=overrides))

    def _call_sync_impl(
        self,
        request: Union[LLMRequest, Dict[str, Any]],
        overrides: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        override_map = dict(overrides or {})
        try:
            req = (
                request
                if isinstance(request, LLMRequest)
                else LLMRequest.model_validate(request)
            )
        except ValidationError as exc:
            return self._error_response(
                provider="",
                model="",
                code="INVALID_ARGUMENT",
                message="Request schema validation failed",
                details={"errors": exc.errors()},
            )

        if bool(req.stream):
            return self._error_response(
                provider=req.provider or "",
                model=req.model or "",
                code="INVALID_ARGUMENT",
                message=(
                    "LLMClient.complete() does not stream; use "
                    "LLMClient.stream() instead, which yields typed "
                    "`LLMStreamEvent` records."
                ),
                details={
                    "stream": True,
                    "contract_posture": "use_llmclient_stream_method",
                },
            )
        provider_name, model_name, routing = self._resolve_provider_and_model(
            req, override_map
        )
        if not provider_name or not model_name:
            return self._error_response(
                provider=provider_name or "",
                model=model_name or "",
                code="INVALID_ARGUMENT",
                message="Provider/model could not be resolved",
                details={"provider": provider_name, "model": model_name},
            )
        req = self._merge_generation_params(req, override_map)
        budget_err, req = self._apply_budgets(req)
        if budget_err is not None:
            return self._finalize_response(budget_err)
        req, tool_policy_ctx = self._apply_tool_policy_pre(req)
        response = self._execute_with_routing(
            req, provider_name, model_name, routing, override_map
        )
        response = self._normalize_response(
            response,
            provider_name,
            model_name,
            allowed_tool_names=tool_policy_ctx.allowed_tools,
        )
        response = self._apply_tool_policy_post(response, tool_policy_ctx)
        response = self._apply_cost_budget(response)

        return self._finalize_response(response)

    def _normalize_response(
        self,
        response: Union[LLMResponse, ProviderAdapterResult, Dict[str, Any]],
        provider: str,
        model: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> LLMResponse:
        try:
            resp = coerce_provider_output(response)
        except ValidationError as exc:
            return self._error_response(
                provider=provider,
                model=model,
                code="INTERNAL_ERROR",
                message="Provider response schema validation failed",
                details={"errors": exc.errors()},
            )

        normalized = resp.model_copy(
            update={"provider": resp.provider or provider, "model": resp.model or model}
        )

        if normalized.output_text and not normalized.assistant_messages:
            assistant = Message(role="assistant", content=normalized.output_text)
            normalized = normalized.model_copy(
                update={"assistant_messages": [assistant]}
            )

        fixed_calls: List[ToolCall] = []
        for call in normalized.tool_calls:
            call_obj = call
            if call_obj.status not in LLM_TOOL_CALL_STATUS_CHOICES:
                call_obj = call_obj.model_copy(
                    update={"status": LLM_TOOL_CALL_STATUS_REQUESTED}
                )
            fixed_calls.append(call_obj)

        if fixed_calls != normalized.tool_calls:
            normalized = normalized.model_copy(update={"tool_calls": fixed_calls})

        allowed_names = [
            str(name).strip() for name in allowed_tool_names or [] if str(name).strip()
        ]
        if not normalized.tool_calls and normalized.output_text and allowed_names:
            raw_tool_markup = detect_raw_envelope(
                normalized.output_text
            ) or detect_raw_xml_tool_wrapper(normalized.output_text)
            normalized_fallback = (
                normalize_tool_calls(
                    assistant_text=normalized.output_text,
                    provider_name=provider,
                    model_name=model,
                    allowed_tool_names=allowed_names,
                )
                if not raw_tool_markup
                else None
            )
            if normalized_fallback and normalized_fallback.calls:
                telemetry = dict(normalized.telemetry or {})
                normalization_meta = dict(
                    telemetry.get("normalization")
                    if isinstance(telemetry.get("normalization"), dict)
                    else {}
                )
                normalization_meta["tool_call_parse_metadata"] = dict(
                    normalized_fallback.metadata
                )
                telemetry["normalization"] = normalization_meta
                normalized = normalized.model_copy(
                    update={
                        "output_text": "",
                        "assistant_messages": [],
                        "tool_calls": [
                            ToolCall(
                                id=str(call.id).strip() or None,
                                name=str(call.name).strip(),
                                arguments=dict(call.arguments or {}),
                                status=LLM_TOOL_CALL_STATUS_PARSED,
                            )
                            for call in normalized_fallback.calls
                        ],
                        "telemetry": telemetry,
                    }
                )
            else:
                sanitized_text = sanitize_envelope_leak(
                    normalized.output_text,
                    metadata=normalized_fallback.metadata
                    if normalized_fallback
                    else None,
                )
                if sanitized_text != normalized.output_text:
                    normalized = normalized.model_copy(
                        update={
                            "output_text": sanitized_text,
                            "assistant_messages": [
                                Message(role="assistant", content=sanitized_text)
                            ],
                        }
                    )

        if (
            normalized.tool_calls
            and normalized.output_text
            and normalized.output_text.startswith(
                "[system: UNEXECUTABLE_TOOL_ENVELOPE]"
            )
        ):
            normalized = normalized.model_copy(
                update={"output_text": "", "assistant_messages": []}
            )

        usage = normalized.usage
        if usage.total_tokens is None:
            total = (usage.input_tokens or 0) + (usage.output_tokens or 0)
            usage = usage.model_copy(update={"total_tokens": total})
            normalized = normalized.model_copy(update={"usage": usage})

        return normalized


__all__ = [
    "LLMCTL",
    "LLMClient",
    "ToolPolicyContext",
    "parse_call_payload",
]
