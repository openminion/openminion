import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openminion.base.config import OpenMinionConfig
from openminion.base.types import Message
from openminion.modules.context.input_boundaries import (
    route_and_ledger as _pidf_route_and_ledger,
)
from openminion.modules.brain.runtime.reasoning import (
    ThinkingCtl,
    ThinkingRequest,
    ThinkingResolutionInput,
)
from openminion.modules.llm.client_call import (
    usage_payload_from_response_usage as _provider_usage_payload,
)
from openminion.modules.llm.providers.base import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderToolSpec,
)
from openminion.modules.llm.providers.envelope_v2 import CONTRACT_VERSION_V2
from openminion.modules.llm.providers.normalization import normalize_provider_response
from openminion.modules.llm.providers.tool_choice import (
    complete_with_provider_override_retry,
)
from openminion.modules.tool import ToolRegistry

from openminion.services.runtime.plugins import PluginRegistry
from openminion.modules.policy.adapters.composition import (
    SEAM_AGENT_SERVICE,
    build_default_composition_boundary_adapter,
)
from openminion.modules.policy import SecurityPolicyEngine
from openminion.services.lifecycle.self_improvement import SelfImprovementEngine
from openminion.modules.tool.exposure import get_allowed_model_tool_names
from openminion.modules.tool.selection import ToolSelectionService

from .constants import (
    DEFAULT_TOOL_LOOP_CONTINUE_PROMPT as _DEFAULT_TOOL_LOOP_CONTINUE_PROMPT,
    TOOL_OUTPUT_FRAME_THRESHOLD_CHARS,
)
from .context.history import (
    _history_role,
    _looks_like_tool_call_envelope_text,
    _loop_tool_feedback,
    _map_history_to_provider,
    _provider_tool_call_strategy,
    _resolve_system_prompt,
)
from .identity_binding import bind_agent_identity_runtime_api
from .execution.fallbacks import AgentToolFallbacks
from .execution import AgentTurnFlowMixin
from .execution.finalization import normalize_provider_response_finalization_status
from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS


def _explicit_tool_artifact_refs(data: object) -> list[dict[str, str]]:
    if not isinstance(data, dict):
        return []
    refs: list[dict[str, str]] = []
    candidates = data.get("artifact_refs")
    if isinstance(candidates, list):
        for item in candidates:
            if isinstance(item, dict):
                ref = str(item.get("ref", "") or "").strip()
                role = str(item.get("role", "") or item.get("type", "") or "output")
            else:
                ref = str(item or "").strip()
                role = "output"
            if ref:
                refs.append({"ref": ref, "role": role})
    artifacts = data.get("artifacts")
    if isinstance(artifacts, dict):
        for role, value in artifacts.items():
            ref = str(value or "").strip()
            if ref:
                refs.append({"ref": ref, "role": str(role or "output")})
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for item in refs:
        ref = item["ref"]
        if ref in seen:
            continue
        seen.add(ref)
        deduped.append(item)
    return deduped


def _tool_output_content_and_frame(
    *,
    content: str,
    data: object,
    threshold_chars: int = TOOL_OUTPUT_FRAME_THRESHOLD_CHARS,
) -> tuple[str, dict[str, object] | None]:
    if len(content) <= max(1, int(threshold_chars)):
        return content, None
    artifact_refs = _explicit_tool_artifact_refs(data)
    if not artifact_refs:
        return content, None
    frame: dict[str, object] = {
        "kind": "artifact_backed_tool_output",
        "original_chars": len(content),
        "threshold_chars": max(1, int(threshold_chars)),
        "artifact_refs": artifact_refs,
    }
    return "[tool output omitted from inline context; see tool_output_frame]", frame


def _thinking_on_default_profile(cfg: OpenMinionConfig) -> str:
    """Return the default agent's `thinking` string, or empty if unavailable."""
    from openminion.base.config.core import resolve_default_agent_id as _rda

    if not getattr(cfg, "agents", None):
        return ""
    try:
        _id = _rda(cfg)
    except Exception:
        return ""
    profile = cfg.agents.get(_id)
    return str(getattr(profile, "thinking", "") or "")


def _provider_facade(
    provider: object | None, llm_runtime: object | None
) -> object | None:
    if provider is not None or llm_runtime is None:
        return provider
    return SimpleNamespace(
        name=str(getattr(llm_runtime, "name", "") or ""),
        model=str(getattr(llm_runtime, "model", "") or ""),
        tool_call_strategy=str(getattr(llm_runtime, "tool_call_strategy", "") or ""),
    )


def _default_identity_name(config: OpenMinionConfig) -> str:
    from openminion.base.config.core import resolve_default_agent_id

    try:
        default_agent_id = resolve_default_agent_id(config)
        return (
            str(config.agents[default_agent_id].name or "").strip() or default_agent_id
        )
    except Exception:  # noqa: BLE001
        return "openminion"


def _tool_result_resolution_metadata(data: dict[str, Any]) -> dict[str, str]:
    chain_value = data.get("runtime_fallback_chain", [])
    runtime_fallback_chain = (
        json.dumps(chain_value, sort_keys=True, default=str)
        if isinstance(chain_value, list)
        else json.dumps([], sort_keys=True)
    )
    return {
        "model_tool_name": str(data.get("model_tool_name", "") or ""),
        "runtime_binding_id": str(data.get("runtime_binding_id", "") or ""),
        "runtime_tool_name": str(data.get("runtime_tool_name", "") or ""),
        "runtime_fallback_chain": runtime_fallback_chain,
        "runtime_fallback_used": str(
            bool(data.get("runtime_fallback_used", False))
        ).lower(),
        "runtime_resolution_source": str(
            data.get("runtime_resolution_source", "") or ""
        ),
    }


def _tool_result_payload_entry(item: Any) -> dict[str, Any]:
    data = getattr(item, "data", {}) or {}
    entry_data = data if isinstance(data, dict) else {}
    entry_chain = entry_data.get("runtime_fallback_chain", []) or []
    if not isinstance(entry_chain, list):
        entry_chain = []
    status = str(entry_data.get("status", "") or "")
    if not status:
        status = "success" if bool(getattr(item, "ok", False)) else "error"
    content, output_frame = _tool_output_content_and_frame(
        content=str(getattr(item, "content", "") or ""),
        data=entry_data,
    )
    payload_entry = {
        "id": str(getattr(item, "call_id", "") or ""),
        "name": str(getattr(item, "tool_name", "") or ""),
        "status": status,
        "error_code": str(entry_data.get("error_code", "") or ""),
        "reason_code": str(entry_data.get("reason_code", "") or ""),
        "error_message": str(getattr(item, "error", "") or ""),
        "tool_name": str(getattr(item, "tool_name", "") or ""),
        "ok": bool(getattr(item, "ok", False)),
        "verified": bool(getattr(item, "verified", False)),
        "content": content,
        "error": str(getattr(item, "error", "") or ""),
        "data": data,
        "call_id": str(getattr(item, "call_id", "") or ""),
        "source": str(getattr(item, "source", "") or ""),
        "model_tool_name": str(entry_data.get("model_tool_name", "") or ""),
        "runtime_binding_id": str(entry_data.get("runtime_binding_id", "") or ""),
        "runtime_tool_name": str(entry_data.get("runtime_tool_name", "") or ""),
        "runtime_fallback_chain": list(entry_chain),
        "runtime_fallback_used": bool(entry_data.get("runtime_fallback_used", False)),
        "runtime_resolution_source": str(
            entry_data.get("runtime_resolution_source", "") or ""
        ),
        "fallback_index": int(getattr(item, "fallback_index", 0) or 0),
        "state": str(getattr(item, "state", "ok") or "ok"),
        "duration_ms": (
            int(getattr(item, "duration_ms", 0) or 0)
            if getattr(item, "duration_ms", None) is not None
            else None
        ),
    }
    if output_frame is not None:
        payload_entry["tool_output_frame"] = output_frame
    return payload_entry


@bind_agent_identity_runtime_api
class AgentService(AgentTurnFlowMixin):
    """Core service for managing agent interactions and tool loops."""

    def __init__(
        self,
        config: OpenMinionConfig,
        plugins: PluginRegistry,
        provider: object | None,
        logger: logging.Logger,
        *,
        llm_runtime: object | None = None,
        home_root: Path | None = None,
        tools: ToolRegistry | None = None,
        security_policy: SecurityPolicyEngine | None = None,
        self_improvement: SelfImprovementEngine | None = None,
    ) -> None:
        self._config = config
        self._plugins = plugins
        self._llm_runtime = llm_runtime
        self._provider = _provider_facade(provider, llm_runtime)
        self._logger = logger
        self._home_root = home_root
        self._tools = tools
        self._security_policy = security_policy
        self._self_improvement = self_improvement
        self._tool_selection = (
            ToolSelectionService(config.runtime.tool_selection, tools)
            if tools
            else None
        )
        self._tool_fallbacks = AgentToolFallbacks(self)
        self._thinking_ctl = ThinkingCtl()
        self._identityctl = None
        self._identity_agent_id = _default_identity_name(config)
        self._identity_tool_filter: dict | None = None
        self._last_identity_snippet = None
        self._init_identity_runtime()

    @staticmethod
    def _sanitize_arguments_for_spec(
        *,
        arguments: dict[str, Any],
        spec: ProviderToolSpec | None,
    ) -> dict[str, Any]:
        return AgentToolFallbacks._sanitize_arguments_for_spec(
            arguments=arguments,
            spec=spec,
        )

    def _build_direct_fallback_arguments(
        self,
        *,
        tool_name: str,
        spec: ProviderToolSpec | None,
        inbound: Message,
    ) -> dict[str, Any] | None:
        return self._tool_fallbacks._build_direct_fallback_arguments(
            tool_name=tool_name,
            spec=spec,
            inbound=inbound,
        )

    def _execute_direct_tool_fallback(
        self,
        *,
        tool_name: str,
        spec: ProviderToolSpec | None,
        inbound: Message,
    ):
        return self._tool_fallbacks._execute_direct_tool_fallback(
            tool_name=tool_name,
            spec=spec,
            inbound=inbound,
        )

    def _fallback_eligibility_reason(self, result: Any) -> str | None:
        return self._tool_fallbacks._fallback_eligibility_reason(result)

    def _should_retry_with_fallback(self, result: Any) -> bool:
        return self._tool_fallbacks._should_retry_with_fallback(result)

    def _augment_browser_fallback_chain(
        self, *, fallback_chain: list[str]
    ) -> list[str]:
        return self._tool_fallbacks._augment_browser_fallback_chain(
            fallback_chain=fallback_chain
        )

    def _normalize_required_tool_arguments(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        return self._tool_fallbacks._normalize_required_tool_arguments(
            tool_name=tool_name,
            arguments=arguments,
        )

    def _build_required_tool_retry_prompt(
        self,
        *,
        user_message: str,
        tool_name: str,
        spec: ProviderToolSpec,
    ) -> str:
        return self._tool_fallbacks._build_required_tool_retry_prompt(
            user_message=user_message,
            tool_name=tool_name,
            spec=spec,
        )

    def _provider_name(self) -> str:
        runtime_name = str(getattr(self._llm_runtime, "name", "") or "").strip()
        if runtime_name:
            return runtime_name
        return str(getattr(self._provider, "name", "") or "").strip()

    @staticmethod
    def _extract_llm_text(response: Any) -> str:
        output_text = str(getattr(response, "output_text", "") or "").strip()
        if output_text:
            return output_text
        chunks: list[str] = []
        for message in list(getattr(response, "assistant_messages", []) or []):
            role = str(getattr(message, "role", "") or "").strip().lower()
            if role and role != "assistant":
                continue
            content = str(getattr(message, "content", "") or "").strip()
            if content:
                chunks.append(content)
        return "\n".join(chunk for chunk in chunks if chunk).strip()

    def _provider_request_to_llm_payload(
        self, request: ProviderRequest
    ) -> tuple[list[dict[str, str]], list[dict[str, Any]], dict[str, str]]:
        messages: list[dict[str, str]] = []
        if str(request.system_prompt or "").strip():
            messages.append({"role": "system", "content": str(request.system_prompt)})

        for item in list(request.history or []):
            role = str(getattr(item, "role", "") or "").strip().lower()
            if role not in {"system", "user", "assistant", "tool"}:
                role = "user"
            content = str(getattr(item, "content", "") or "").strip()
            if not content:
                continue
            messages.append({"role": role, "content": content})

        # PIDF: route user_message through the typed boundary owner.
        _user_rendered, _ = _pidf_route_and_ledger(
            "user_message",
            str(request.user_message or ""),
            seam_id="services.agent.service.user_message",
        )
        messages.append({"role": "user", "content": _user_rendered})

        tools = [
            {
                "name": str(spec.name),
                "description": str(spec.description or ""),
                "input_schema": dict(spec.parameters or {}),
                "strict": bool(spec.strict),
            }
            for spec in list(request.tools or [])
            if str(spec.name or "").strip()
        ]
        metadata = {str(k): str(v) for k, v in dict(request.metadata or {}).items()}
        if str(request.tool_call_strategy or "").strip():
            metadata["tool_call_strategy"] = str(request.tool_call_strategy)
        return messages, tools, metadata

    def _resolve_provider_request_thinking(self, request: ProviderRequest) -> None:
        metadata = {
            str(key): str(value)
            for key, value in dict(getattr(request, "metadata", {}) or {}).items()
        }
        if metadata.get("thinking_reasoning_profile"):
            request.metadata = metadata
            return

        from openminion.base.config.core import resolve_default_agent_id

        runtime_thinking = getattr(
            getattr(self._config, "runtime", None), "thinking_policy", None
        )
        agent_runtime_thinking = None
        try:
            _default_id = resolve_default_agent_id(self._config)
            _default_profile = self._config.agents.get(_default_id)
            if _default_profile is not None:
                agent_runtime_thinking = getattr(
                    _default_profile, "thinking_policy", None
                )
        except Exception:  # noqa: BLE001
            agent_runtime_thinking = None
        provider_name = str(
            getattr(self._llm_runtime, "name", "")
            or getattr(self._provider, "name", "")
            or ""
        ).strip()
        model_name = str(
            getattr(self._llm_runtime, "model", "")
            or getattr(self._provider, "model", "")
            or ""
        ).strip()
        resolved = self._thinking_ctl.resolve(
            request=ThinkingRequest(
                purpose=str(metadata.get("purpose", "") or "").strip() or None,
                requested_profile=str(getattr(request, "thinking", "") or "").strip()
                or None,
                provider=provider_name or None,
                model=model_name or None,
                metadata=metadata,
            ),
            layers=ThinkingResolutionInput(
                code_default_profile="minimal",
                system_profile=str(
                    getattr(runtime_thinking, "reasoning_profile", "") or ""
                ).strip()
                or None,
                agent_profile=str(
                    getattr(agent_runtime_thinking, "reasoning_profile", "")
                    or _thinking_on_default_profile(self._config)
                    or ""
                ).strip()
                or None,
            ),
        )
        metadata.update(self._thinking_ctl.build_provider_metadata(resolved=resolved))
        request.metadata = metadata
        request.thinking = str(resolved.provider_effort or "")

    def _llm_response_to_provider_response(
        self,
        response: Any,
        *,
        retry_override_id: str = "",
        provider_name: str = "",
        model_name: str = "",
        allowed_tool_names: list[str] | None = None,
    ) -> ProviderResponse:
        if not bool(getattr(response, "ok", False)):
            error = getattr(response, "error", None)
            error_code = str(getattr(error, "code", "") or "").strip()
            error_message = str(getattr(error, "message", "") or "").strip()
            normalization = {
                "adapter": "llm_runtime_client",
                "upstream_error_code": error_code,
                "upstream_error_message": error_message,
            }
            if retry_override_id:
                normalization["provider_retry_override"] = retry_override_id
            if error_code == "EMPTY_PAYLOAD":
                normalized = normalize_provider_response(
                    ProviderResponse(
                        text=self._extract_llm_text(response),
                        model=str(
                            getattr(response, "model", "")
                            or model_name
                            or provider_name
                            or self._provider_name()
                            or "model"
                        ),
                        usage={},
                        tool_calls=[],
                        finish_reason=str(getattr(response, "finish_reason", "") or ""),
                        normalization=normalization,
                        thinking=list(getattr(response, "thinking", []) or []),
                    ),
                    provider_name=provider_name or self._provider_name(),
                    model_name=model_name or str(getattr(response, "model", "") or ""),
                    allowed_tool_names=allowed_tool_names,
                )
                return normalize_provider_response_finalization_status(normalized)
            if error is None:
                raise ProviderError("llm runtime call failed")
            raise ProviderError(
                f"{error_code}: {error_message}" if error_code else error_message
            )

        usage_payload = _provider_usage_payload(getattr(response, "usage", None))

        tool_calls: list[ProviderToolCall] = []
        for call in list(getattr(response, "tool_calls", []) or []):
            name = str(getattr(call, "name", "") or "").strip()
            if not name:
                continue
            tool_calls.append(
                ProviderToolCall(
                    id=str(getattr(call, "id", "") or ""),
                    name=name,
                    arguments=dict(getattr(call, "arguments", {}) or {}),
                    source=str(getattr(call, "status", "") or "native"),
                )
            )

        normalization = {
            "adapter": "llm_runtime_client",
            "llm_response_contract_version": str(
                getattr(response, "contract_version", "v1")
            ),
        }
        if retry_override_id:
            normalization["provider_retry_override"] = retry_override_id

        raw_thinking = list(getattr(response, "thinking", []) or [])
        provider_response = ProviderResponse(
            text=self._extract_llm_text(response),
            model=str(getattr(response, "model", "") or ""),
            usage=usage_payload,
            tool_calls=tool_calls,
            finish_reason=str(getattr(response, "finish_reason", "") or ""),
            normalization=normalization,
            thinking=raw_thinking,
        )
        raw_finalization_status = getattr(response, STATE_KEY_FINALIZATION_STATUS, None)
        normalized = normalize_provider_response(
            provider_response,
            provider_name=provider_name or self._provider_name(),
            model_name=model_name or str(getattr(response, "model", "") or ""),
            allowed_tool_names=allowed_tool_names,
        )
        if raw_finalization_status is not None:
            setattr(normalized, STATE_KEY_FINALIZATION_STATUS, raw_finalization_status)
        return normalize_provider_response_finalization_status(normalized)

    async def _invoke_provider_request(
        self, provider_request: ProviderRequest
    ) -> ProviderResponse:
        self._resolve_provider_request_thinking(provider_request)
        allowed_tool_names = [
            spec.name for spec in provider_request.tools if spec and spec.name
        ]
        if not allowed_tool_names and self._tools is not None:
            allowed_tool_names = sorted(get_allowed_model_tool_names(self._tools))
        if not allowed_tool_names:
            allowed_tool_names = None
        llm_client = getattr(self._llm_runtime, "client", None)
        if llm_client is not None:
            provider_name = str(
                getattr(self._llm_runtime, "name", "")
                or self._provider_name()
                or "echo"
            )
            model_name = str(
                getattr(self._llm_runtime, "model", "")
                or getattr(getattr(self, "_provider", object()), "model", "")
                or ""
            )
            messages, tools, metadata = self._provider_request_to_llm_payload(
                provider_request
            )
            completion_result = await asyncio.to_thread(
                complete_with_provider_override_retry,
                complete_fn=llm_client.complete,
                provider_name=provider_name,
                model_name=model_name,
                messages=messages,
                tools=tools or None,
                tool_choice=provider_request.tool_choice,
                metadata=metadata,
                thinking=provider_request.thinking,
            )
            return self._llm_response_to_provider_response(
                completion_result.response,
                retry_override_id=completion_result.retry_override_id,
                provider_name=provider_name,
                model_name=model_name,
                allowed_tool_names=allowed_tool_names,
            )

        if self._provider is None or not callable(
            getattr(self._provider, "generate", None)
        ):
            raise RuntimeError(
                "No runtime LLM client or legacy provider.generate() is available"
            )
        raw = await self._provider.generate(provider_request)
        if isinstance(raw, ProviderResponse):
            return normalize_provider_response_finalization_status(raw)
        provider_response = ProviderResponse(
            text=str(getattr(raw, "text", "") or ""),
            model=str(getattr(raw, "model", "") or ""),
            usage=dict(getattr(raw, "usage", {}) or {}),
            tool_calls=list(getattr(raw, "tool_calls", []) or []),
            finish_reason=str(getattr(raw, "finish_reason", "") or ""),
            normalization=dict(getattr(raw, "normalization", {}) or {}),
            thinking=list(getattr(raw, "thinking", []) or []),
        )
        raw_finalization_status = getattr(raw, STATE_KEY_FINALIZATION_STATUS, None)
        if raw_finalization_status is not None:
            setattr(
                provider_response,
                STATE_KEY_FINALIZATION_STATUS,
                raw_finalization_status,
            )
        return normalize_provider_response_finalization_status(provider_response)

    async def _generate_normalized(self, provider_request: ProviderRequest):
        raw_provider_response = await self._invoke_provider_request(provider_request)
        if self._llm_runtime is not None:
            # Live runtime path already uses llm-client normalized responses.
            return normalize_provider_response_finalization_status(
                raw_provider_response
            )
        allowed_tool_names = [
            spec.name for spec in provider_request.tools if spec and spec.name
        ]
        if not allowed_tool_names and self._tools is not None:
            allowed_tool_names = sorted(get_allowed_model_tool_names(self._tools))
        if not allowed_tool_names:
            allowed_tool_names = None
        raw_finalization_status = getattr(
            raw_provider_response,
            STATE_KEY_FINALIZATION_STATUS,
            None,
        )
        normalized = normalize_provider_response(
            raw_provider_response,
            provider_name=self._provider_name(),
            allowed_tool_names=allowed_tool_names,
            model_name=str(getattr(raw_provider_response, "model", "") or ""),
        )
        if raw_finalization_status is not None:
            setattr(normalized, STATE_KEY_FINALIZATION_STATUS, raw_finalization_status)
        return normalize_provider_response_finalization_status(normalized)

    # --- Tool loop helpers (inlined from AgentToolLoopMixin) ---

    @staticmethod
    def _empty_tool_resolution_metadata() -> dict[str, str]:
        return {
            "model_tool_name": "",
            "runtime_binding_id": "",
            "runtime_tool_name": "",
            "runtime_fallback_chain": "[]",
            "runtime_fallback_used": "false",
            "runtime_resolution_source": "",
        }

    def _execute_single_tool_call(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        context: Any,
        source: str,
    ):
        if self._tools is None:
            raise RuntimeError("tool registry is not available")
        if (
            context is not None
            and getattr(context, "blast_radius_adapter", None) is None
        ):
            try:
                context.blast_radius_adapter = (
                    build_default_composition_boundary_adapter(
                        seam_id=SEAM_AGENT_SERVICE,
                    )
                )
            except AttributeError:
                # Some callers pass non-dataclass contexts (test doubles);
                pass
        return self._tools.execute_calls(
            [
                ProviderToolCall(
                    name=tool_name,
                    arguments=dict(arguments),
                    source=source,
                )
            ],
            context=context,
        )

    def _is_retryable_batch(self, batch: Any) -> bool:
        for item in list(getattr(batch, "results", []) or []):
            if self._fallback_eligibility_reason(item):
                return True
        return False

    @staticmethod
    def _is_browser_backend_unavailable(batch: Any) -> bool:
        for item in list(getattr(batch, "results", []) or []):
            if bool(getattr(item, "ok", False)):
                continue
            error = str(getattr(item, "error", "") or "").lower()
            data = getattr(item, "data", {}) or {}
            error_code = str(data.get("error_code", "") or "").lower()
            details = data.get("details", {}) or {}
            detail_text = str(details).lower()
            signals = (
                "failed to reach pinchtab bridge",
                "connection refused",
                "temporarily unavailable",
                "service unavailable",
                "browser backend unavailable",
            )
            if (
                any(token in error for token in signals)
                or any(token in detail_text for token in signals)
                or (error_code == "exec_error" and "pinchtab" in error)
            ):
                return True
        return False

    def _tool_batch_metadata(
        self, *, batch: Any, tool_calls_count: int
    ) -> dict[str, str]:
        results = list(getattr(batch, "results", []) or [])
        tool_results_payload = [_tool_result_payload_entry(item) for item in results]
        resolution_metadata = self._empty_tool_resolution_metadata()
        for item in results:
            data = getattr(item, "data", {}) or {}
            if isinstance(data, dict) and str(data.get("model_tool_name", "") or ""):
                resolution_metadata = _tool_result_resolution_metadata(data)
                break
        return {
            "tool_contract_version": CONTRACT_VERSION_V2,
            "tool_calls_count": str(max(0, int(tool_calls_count))),
            "tool_execution_count": str(len(results)),
            "tool_verified": str(bool(getattr(batch, "all_verified", False))).lower(),
            "tool_results": json.dumps(
                tool_results_payload, sort_keys=True, default=str
            ),
            **resolution_metadata,
        }

    def _get_spec_for_tool(self, tool_name: str) -> ProviderToolSpec | None:
        """Helper to get provider spec for a specific tool, used for deterministic tool handling."""
        if not self._tools:
            return None

        if callable(getattr(self._tools, "provider_spec_for_name", None)):
            resolved = self._tools.provider_spec_for_name(tool_name)
            if resolved is not None:
                return resolved

        all_tools = self._tools.provider_specs()
        for spec in all_tools:
            if spec.name == tool_name:
                return spec
        return None


__all__ = [
    "AgentService",
    "_DEFAULT_TOOL_LOOP_CONTINUE_PROMPT",
    "_history_role",
    "_looks_like_tool_call_envelope_text",
    "_loop_tool_feedback",
    "_map_history_to_provider",
    "_provider_tool_call_strategy",
    "_resolve_system_prompt",
]
