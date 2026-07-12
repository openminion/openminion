import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from openminion.base.config import resolve_data_root
from openminion.base.constants import OPENMINION_DATA_ROOT_ENV
from openminion.modules.llm.providers.base import (
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
)
from openminion.modules.telemetry.trace.phase_timing import active_chat_phase
from openminion.modules.storage.runtime.sqlite import resolve_database_path
from openminion.modules.tool.base import ToolExecutionContext, ToolExecutionResult
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.modules.tool.runtime.memory import MemoryToolRuntimeService
from openminion.services.agent.memory import resolve_memory_root
from openminion.modules.memory.smoke import (
    EphemeralMemorySmokeProvider,
)
from openminion.services.agent.memory.gateway_adapter import (
    DisabledMemoryGatewayAdapter,
    MemoryServiceGatewayAdapter,
)
from openminion.modules.tool.runtime.delegation import A2ADelegateApi
from openminion.services.runtime.a2a_delegate import build_a2a_delegate_api
from openminion.services.runtime.bootstrap import build_agent_memory_service
from openminion.services.security.policy import (
    DECISION_REQUIRE_APPROVAL,
    SecurityPolicyContext,
    ToolBudgetState,
    default_internal_actor,
)
from openminion.services.security.tool_execution import (
    build_execution_boundary_policy_adapter,
)
from openminion.modules.tool.runtime.routing import build_runtime_tool_routing_metadata

from ..telemetry import trace_provider_request, trace_provider_response
from .loop_quality import observe_tool_calls
from .ports import TurnFlowServicePort


def _blocked_tool_result(
    *,
    call: ProviderToolCall,
    tool_name: str,
    decision,
    event_kind: str,
    denial_source: str,
) -> ToolExecutionResult:
    reason_code = str(getattr(decision, "reason", "") or "").strip() or "policy_denied"
    decision_code = str(getattr(decision, "code", "") or "").strip() or reason_code
    details = dict(getattr(decision, "details", {}) or {})
    return ToolExecutionResult(
        tool_name=tool_name or "unknown",
        ok=False,
        verified=False,
        content="",
        error=reason_code if denial_source == "budget" else "security_deny",
        data={
            "status": "blocked",
            "error_code": decision_code,
            "reason_code": reason_code,
            "denial_source": denial_source,
            "blocked_kind": event_kind,
            "error_details": details,
            "tool_name": tool_name,
            "call_id": str(getattr(call, "id", "") or ""),
        },
        call_id=str(getattr(call, "id", "") or ""),
        source="policy",
    )


class ExecutorRuntime:
    def __init__(
        self,
        *,
        service_port: TurnFlowServicePort,
        runtime: Any,
    ) -> None:
        self._service_port = service_port
        self._runtime = runtime
        self._memory_tool_service: MemoryToolRuntimeService | None = None
        self._memory_tool_service_resolved = False
        self._a2a_delegate_api: A2ADelegateApi | None = None
        self._a2a_delegate_api_resolved = False

    def _resolve_memory_tool_service(self) -> MemoryToolRuntimeService | None:
        if self._memory_tool_service_resolved:
            return self._memory_tool_service
        self._memory_tool_service_resolved = True

        config = getattr(self._service_port, "config", None)
        runtime_cfg = getattr(config, "runtime", None)
        if config is None or runtime_cfg is None:
            return None
        if not bool(getattr(runtime_cfg, "memory_enabled", True)):
            return None

        provider = str(
            getattr(runtime_cfg, "memory_provider", "memory_v2") or ""
        ).strip()
        if provider != "memory_v2":
            return None

        runtime_env = getattr(runtime_cfg, "env", None)
        env_payload = dict(runtime_env) if isinstance(runtime_env, Mapping) else {}
        storage_cfg = getattr(getattr(config, "storage", None), "path", None)
        home_root_raw = getattr(self._service_port, "home_root", None)
        home_root = (
            Path(home_root_raw).expanduser().resolve(strict=False)
            if home_root_raw is not None
            else Path.cwd().resolve(strict=False)
        )
        data_root = resolve_data_root(
            home_root,
            data_root=str(env_payload.get(OPENMINION_DATA_ROOT_ENV, "") or ""),
        )
        storage_path = resolve_database_path(storage_cfg, env=env_payload)
        memory_root = resolve_memory_root(
            config=config,
            config_path=Path(),
            storage_path=storage_path,
            data_root=data_root,
        )
        try:
            built = build_agent_memory_service(
                config=config,
                agent_id=self._service_port.identity_agent_id,
                memory_root=memory_root,
                logger=self._service_port.logger.getChild("memory_tools"),
                home_root=home_root,
                data_root=data_root,
                storage_path=storage_path,
            )
        except Exception:
            return None

        if isinstance(built, MemoryServiceGatewayAdapter):
            service = getattr(built, "_service", None)
            if isinstance(service, MemoryToolRuntimeService):
                self._memory_tool_service = service
                return service
            return None
        if isinstance(
            built, (DisabledMemoryGatewayAdapter, EphemeralMemorySmokeProvider)
        ):
            return None
        if isinstance(built, MemoryToolRuntimeService):
            self._memory_tool_service = built
            return built
        return None

    def _resolve_a2a_delegate_api(self) -> A2ADelegateApi | None:
        """Lazily build the A2A delegation seam for task.delegate (MAO-01).

        Mirrors ``_resolve_memory_tool_service``: built once from the service
        port's config/home/agent identity. Returns ``None`` when A2A is
        unavailable, in which case the handler returns a typed
        "delegation unavailable" error rather than NOT_IMPLEMENTED.
        """
        if self._a2a_delegate_api_resolved:
            return self._a2a_delegate_api
        self._a2a_delegate_api_resolved = True

        config = getattr(self._service_port, "config", None)
        if config is None:
            return None
        runtime_cfg = getattr(config, "runtime", None)
        runtime_env = getattr(runtime_cfg, "env", None)
        env_payload = dict(runtime_env) if isinstance(runtime_env, Mapping) else {}
        home_root_raw = getattr(self._service_port, "home_root", None)
        home_root = (
            Path(home_root_raw).expanduser().resolve(strict=False)
            if home_root_raw is not None
            else Path.cwd().resolve(strict=False)
        )
        try:
            self._a2a_delegate_api = build_a2a_delegate_api(
                config=config,
                home_root=home_root,
                agent_id=self._service_port.identity_agent_id,
                env=env_payload or None,
            )
        except Exception:
            self._a2a_delegate_api = None
        return self._a2a_delegate_api

    async def call_provider(
        self, request: ProviderRequest, *, tool_call_strategy: str
    ) -> ProviderResponse:
        self._runtime.inference_steps += 1
        inference_steps = self._runtime.inference_steps
        inbound = self._runtime.inbound

        request.tool_call_strategy = tool_call_strategy
        label = f"call{inference_steps:02d}"
        request.metadata = dict(getattr(request, "metadata", {}) or {})
        inbound_meta = getattr(inbound, "metadata", {}) or {}
        session_id = str(inbound_meta.get("session_id", "") or "").strip()
        run_id = str(inbound_meta.get("run_id", "") or "").strip()
        if session_id and not request.metadata.get("session_id"):
            request.metadata["session_id"] = session_id
        if run_id and not request.metadata.get("run_id"):
            request.metadata["run_id"] = run_id
        request.metadata["turn_id"] = str(inbound.id)
        request.metadata["inference_step"] = str(inference_steps)
        request.metadata["trace_label"] = label

        trace_provider_request(
            provider_request=request,
            label=label,
            provider_name=str(getattr(self._service_port.provider, "name", "") or ""),
            home_root=self._service_port.home_root,
            inbound_metadata=getattr(inbound, "metadata", {}) or {},
            turn_id=str(inbound.id),
            inference_step=inference_steps,
            logger=self._service_port.logger,
        )
        response = await self._service_port.generate_normalized(request)
        trace_provider_response(
            provider_response=response,
            label=label,
            provider_name=str(getattr(self._service_port.provider, "name", "") or ""),
            home_root=self._service_port.home_root,
            inbound_metadata=getattr(inbound, "metadata", {}) or {},
            turn_id=str(inbound.id),
            inference_step=inference_steps,
            logger=self._service_port.logger,
        )
        return response

    def _build_tool_execution_context(self) -> ToolExecutionContext:
        inbound = self._runtime.inbound
        tool_metadata = dict(inbound.metadata or {})
        resolved_storage_path = getattr(self._runtime, "storage_path", None)
        if resolved_storage_path is None:
            raw_runtime_env = getattr(
                getattr(self._service_port.config, "runtime", None),
                "env",
                None,
            )
            env_payload = (
                dict(raw_runtime_env) if isinstance(raw_runtime_env, Mapping) else None
            )
            resolved_storage_path = resolve_database_path(
                getattr(
                    getattr(self._service_port.config, "storage", None), "path", None
                ),
                env=env_payload,
            )
        runtime_env = getattr(
            getattr(self._service_port.config, "runtime", None),
            "env",
            None,
        )
        if isinstance(runtime_env, Mapping):
            tool_metadata.setdefault("runtime_env", dict(runtime_env))
        runtime_tools = getattr(
            getattr(self._service_port.config, "runtime", None),
            "tools",
            None,
        )
        for key, value in build_runtime_tool_routing_metadata(runtime_tools).items():
            tool_metadata.setdefault(key, value)
        tool_metadata.setdefault("agent_id", self._service_port.identity_agent_id)
        tool_metadata.setdefault("tool_call_origin", "model")
        if self._service_port.tool_selection is not None:
            for (
                key,
                value,
            ) in self._service_port.tool_selection.runtime_binding_policy_metadata().items():
                tool_metadata.setdefault(key, value)
        tool_metadata.setdefault(
            "storage_path",
            str(resolved_storage_path or ""),
        )
        tool_metadata.setdefault(
            "memory_enabled",
            str(
                bool(
                    getattr(
                        getattr(self._service_port.config, "runtime", None),
                        "memory_enabled",
                        True,
                    )
                )
            ).lower(),
        )
        tool_metadata.setdefault(
            "memory_provider",
            str(
                getattr(
                    getattr(self._service_port.config, "runtime", None),
                    "memory_provider",
                    "memory_v2",
                )
                or ""
            ).strip(),
        )
        return ToolExecutionContext(
            channel=inbound.channel,
            target=inbound.target,
            session_id=inbound.metadata.get("session_id", ""),
            metadata=tool_metadata,
            memory_service=self._resolve_memory_tool_service(),
            sandbox_runner=getattr(self._runtime, "sandbox_runner", None),
            authored_tools_api=getattr(self._runtime, "authored_tools", None),
            a2a_delegate_api=self._resolve_a2a_delegate_api(),
        )

    @staticmethod
    def _collect_batch_output(batch: ToolExecutionBatch) -> str:
        outputs: list[str] = []
        for result in list(batch.results or []):
            if result.ok:
                outputs.append(result.content or str(result.data))
            else:
                outputs.append(f"Error: {result.error}")
        return "\n".join(outputs).strip() or "Tool executed."

    def _build_policy_adapter(
        self,
        *,
        tool_budget_state: ToolBudgetState | None,
        turn_boundary_adapter: Any,
    ) -> Any | None:
        """Build the policy adapter when both security_policy and tools are wired."""
        if (
            self._service_port.security_policy is None
            or self._service_port.tools is None
        ):
            return None

        def _adapter_policy_lookup(tool_name: str) -> Any:
            profile = self._service_port.tools.policy_for(tool_name)
            return SimpleNamespace(
                required_scopes_all=(),
                risk=getattr(profile, "risk", "medium"),
                budget_cost=getattr(profile, "budget_cost", 1),
            )

        inbound = self._runtime.inbound
        return build_execution_boundary_policy_adapter(
            policy=self._service_port.security_policy,
            actor=default_internal_actor(self._service_port.identity_agent_id),
            context=SecurityPolicyContext(
                channel=inbound.channel,
                target=inbound.target,
                session_id=inbound.metadata.get("session_id", ""),
                run_id=inbound.metadata.get("run_id", ""),
            ),
            tool_policy_lookup=_adapter_policy_lookup,
            budget_state=tool_budget_state,
            blast_radius_adapter=turn_boundary_adapter,
        )

    @staticmethod
    def _build_security_event(
        *,
        call: ProviderToolCall,
        tool_name: str,
        decision: Any,
    ) -> tuple[dict[str, str], str]:
        event_kind = (
            "approval_required"
            if decision.requires_confirm or decision.code == DECISION_REQUIRE_APPROVAL
            else "policy_denied"
        )
        reason_code = str(decision.reason or "policy_denied")
        source = "budget" if reason_code.startswith("tool_budget") else "policy"
        details = dict(decision.details or {})
        return {
            "event_kind": event_kind,
            "reason_code": reason_code,
            "policy_version": str(details.get("policy_version", "") or "v1"),
            "decision": str(details.get("decision", "") or decision.code),
            "tool_name": tool_name,
            "call_id": str(getattr(call, "id", "") or ""),
            "source": source,
        }, source

    async def _apply_policy_decisions(
        self,
        *,
        tool_calls: list[ProviderToolCall],
        policy_adapter: Any,
        approval_callback: Any,
        security_events: list[dict[str, str]],
        denied_results: list[ToolExecutionResult],
    ) -> list[ProviderToolCall]:
        """Walk the policy adapter over each call, returning the allowed-call list.
        Mutates `security_events` and `denied_results` for denied/approved cases."""
        allowed_calls: list[ProviderToolCall] = []
        for call in tool_calls:
            tool_name = str(getattr(call, "name", "") or "").strip()
            tool_args = dict(getattr(call, "arguments", {}) or {})
            policy_profile = self._service_port.tools.policy_for(tool_name)
            tool_spec = SimpleNamespace(
                name=tool_name,
                dangerous=str(policy_profile.risk or "").strip().lower()
                in {"high", "critical"},
            )
            decision = policy_adapter.evaluate(
                tool_name=tool_name,
                tool_spec=tool_spec,
                args=tool_args,
            )
            if not decision.allowed:
                if approval_callback is not None and (
                    decision.requires_confirm
                    or decision.code == DECISION_REQUIRE_APPROVAL
                ):
                    with active_chat_phase("approval_wait"):
                        approved = bool(
                            await approval_callback(
                                tool_name,
                                tool_args,
                                str(getattr(call, "id", "") or ""),
                            )
                        )
                    if approved:
                        allowed_calls.append(
                            ProviderToolCall(
                                name=tool_name,
                                arguments=decision.modified_args or tool_args,
                                id=str(getattr(call, "id", "") or ""),
                                source=str(getattr(call, "source", "") or ""),
                            )
                        )
                        continue
                event_dict, denial_source = self._build_security_event(
                    call=call,
                    tool_name=tool_name,
                    decision=decision,
                )
                security_events.append(event_dict)
                denied_results.append(
                    _blocked_tool_result(
                        call=call,
                        tool_name=tool_name,
                        decision=decision,
                        event_kind=event_dict["event_kind"],
                        denial_source=denial_source,
                    )
                )
                break
            next_args = decision.modified_args or dict(
                getattr(call, "arguments", {}) or {}
            )
            allowed_calls.append(
                ProviderToolCall(
                    name=tool_name,
                    arguments=next_args,
                    id=str(getattr(call, "id", "") or ""),
                    source=str(getattr(call, "source", "") or ""),
                )
            )
        return allowed_calls

    def _apply_context_metadata_overrides(
        self,
        *,
        ctx: Any,
        context_metadata_overrides: Mapping[str, Any] | None,
    ) -> None:
        if not (context_metadata_overrides and isinstance(ctx.metadata, dict)):
            return
        for key, value in context_metadata_overrides.items():
            token = str(key or "").strip()
            if not token:
                continue
            if token == "runtime_env" and isinstance(value, Mapping):
                ctx.metadata[token] = dict(value)
            else:
                ctx.metadata[token] = str(value)

    @staticmethod
    def _emit_tool_started(
        progress_callback: Any,
        allowed_calls: list[ProviderToolCall],
    ) -> None:
        """TESS-02: emit `tool_started` for each allowed call.
        Provenance fields are unknown until execution resolves the binding,
        so they're emitted empty/None and filled on tool_completed."""
        for call in allowed_calls:
            try:
                progress_callback(  # type: ignore[misc]
                    {
                        "kind": "tool_started",
                        "tool_name": str(getattr(call, "name", "") or ""),
                        "args": dict(getattr(call, "arguments", {}) or {}),
                        "call_id": str(getattr(call, "id", "") or ""),
                        "model_tool_name": "",
                        "runtime_tool_name": "",
                        "runtime_binding_id": "",
                        "runtime_fallback_used": False,
                        "runtime_fallback_chain": [],
                        "runtime_resolution_source": "",
                        "fallback_index": 0,
                        "state": "running",
                    }
                )
            except Exception:
                pass

    @staticmethod
    def _emit_tool_completed(
        progress_callback: Any,
        results: list[ToolExecutionResult],
        allowed_calls: list[ProviderToolCall],
        batch_duration_ms: int,
    ) -> None:
        """TESS-02: emit `tool_completed` per result. Per-call duration_ms
        comes from the result's own timing stamps when available; falls back
        to the batch-wide aggregate only for pre-TESS results."""
        for index, result in enumerate(results):
            call = allowed_calls[index] if index < len(allowed_calls) else None
            result_content = str(
                getattr(result, "content", "")
                or getattr(result, "data", "")
                or getattr(result, "error", "")
                or ""
            )
            per_call_duration_ms = getattr(result, "duration_ms", None)
            if per_call_duration_ms is None:
                per_call_duration_ms = batch_duration_ms
            result_data = getattr(result, "data", {}) or {}
            try:
                progress_callback(  # type: ignore[misc]
                    {
                        "kind": "tool_completed",
                        "tool_name": str(
                            getattr(result, "tool_name", "")
                            or getattr(call, "name", "")
                            or ""
                        ),
                        "args": dict(getattr(call, "arguments", {}) or {}),
                        "call_id": str(
                            getattr(result, "call_id", "")
                            or getattr(call, "id", "")
                            or ""
                        ),
                        "content": result_content,
                        "ok": bool(getattr(result, "ok", False)),
                        "duration_ms": int(per_call_duration_ms),
                        "batch_duration_ms": batch_duration_ms,
                        "exit_code": 0 if bool(getattr(result, "ok", False)) else 1,
                        "truncated": False,
                        "model_tool_name": str(
                            result_data.get("model_tool_name", "") or ""
                        ),
                        "runtime_tool_name": str(
                            result_data.get("runtime_tool_name", "") or ""
                        ),
                        "runtime_binding_id": str(
                            result_data.get("runtime_binding_id", "") or ""
                        ),
                        "runtime_fallback_used": bool(
                            result_data.get("runtime_fallback_used", False)
                        ),
                        "runtime_fallback_chain": list(
                            result_data.get("runtime_fallback_chain", []) or []
                        ),
                        "runtime_resolution_source": str(
                            result_data.get("runtime_resolution_source", "") or ""
                        ),
                        "fallback_index": int(
                            getattr(result, "fallback_index", 0) or 0
                        ),
                        "state": str(getattr(result, "state", "ok") or "ok"),
                    }
                )
            except Exception:
                pass

    def _build_policy_adapter(
        self, *, tool_budget_state: ToolBudgetState | None, turn_boundary_adapter: Any
    ) -> Any | None:
        if (
            self._service_port.security_policy is None
            or self._service_port.tools is None
        ):
            return None

        def _adapter_policy_lookup(tool_name: str) -> Any:
            profile = self._service_port.tools.policy_for(tool_name)
            return SimpleNamespace(
                required_scopes_all=(),
                risk=getattr(profile, "risk", "medium"),
                budget_cost=getattr(profile, "budget_cost", 1),
            )

        inbound = self._runtime.inbound
        return build_execution_boundary_policy_adapter(
            policy=self._service_port.security_policy,
            actor=default_internal_actor(self._service_port.identity_agent_id),
            context=SecurityPolicyContext(
                channel=inbound.channel,
                target=inbound.target,
                session_id=inbound.metadata.get("session_id", ""),
                run_id=inbound.metadata.get("run_id", ""),
            ),
            tool_policy_lookup=_adapter_policy_lookup,
            budget_state=tool_budget_state,
            blast_radius_adapter=turn_boundary_adapter,
        )

    async def _filter_allowed_tool_calls(
        self,
        tool_calls: list[ProviderToolCall],
        *,
        policy_adapter: Any | None,
    ) -> tuple[list[ProviderToolCall], list[dict[str, str]], list[ToolExecutionResult]]:
        if policy_adapter is None or self._service_port.tools is None:
            return list(tool_calls or []), [], []

        security_events: list[dict[str, str]] = []
        denied_results: list[ToolExecutionResult] = []
        allowed_calls: list[ProviderToolCall] = []
        approval_callback = getattr(self._runtime, "approval_callback", None)
        for call in tool_calls:
            tool_name = str(getattr(call, "name", "") or "").strip()
            tool_args = dict(getattr(call, "arguments", {}) or {})
            policy_profile = self._service_port.tools.policy_for(tool_name)
            tool_spec = SimpleNamespace(
                name=tool_name,
                dangerous=str(policy_profile.risk or "").strip().lower()
                in {"high", "critical"},
            )
            decision = policy_adapter.evaluate(
                tool_name=tool_name,
                tool_spec=tool_spec,
                args=tool_args,
            )
            if decision.allowed:
                allowed_calls.append(
                    ProviderToolCall(
                        name=tool_name,
                        arguments=decision.modified_args or tool_args,
                        id=str(getattr(call, "id", "") or ""),
                        source=str(getattr(call, "source", "") or ""),
                    )
                )
                continue
            if approval_callback is not None and (
                decision.requires_confirm or decision.code == DECISION_REQUIRE_APPROVAL
            ):
                with active_chat_phase("approval_wait"):
                    approved = bool(
                        await approval_callback(
                            tool_name,
                            tool_args,
                            str(getattr(call, "id", "") or ""),
                        )
                    )
                if approved:
                    allowed_calls.append(
                        ProviderToolCall(
                            name=tool_name,
                            arguments=decision.modified_args or tool_args,
                            id=str(getattr(call, "id", "") or ""),
                            source=str(getattr(call, "source", "") or ""),
                        )
                    )
                    continue
            event_kind = (
                "approval_required"
                if decision.requires_confirm
                or decision.code == DECISION_REQUIRE_APPROVAL
                else "policy_denied"
            )
            reason_code = str(decision.reason or "policy_denied")
            source = "budget" if reason_code.startswith("tool_budget") else "policy"
            details = dict(decision.details or {})
            security_events.append(
                {
                    "event_kind": event_kind,
                    "reason_code": reason_code,
                    "policy_version": str(details.get("policy_version", "") or "v1"),
                    "decision": str(details.get("decision", "") or decision.code),
                    "tool_name": tool_name,
                    "call_id": str(getattr(call, "id", "") or ""),
                    "source": source,
                }
            )
            denied_results.append(
                _blocked_tool_result(
                    call=call,
                    tool_name=tool_name,
                    decision=decision,
                    event_kind=event_kind,
                    denial_source=source,
                )
            )
            break
        return allowed_calls, security_events, denied_results

    def _build_execution_context_with_overrides(
        self,
        *,
        context_metadata_overrides: Mapping[str, Any] | None,
        turn_boundary_adapter: Any,
    ) -> ToolExecutionContext:
        ctx = self._build_tool_execution_context()
        if context_metadata_overrides and isinstance(ctx.metadata, dict):
            for key, value in context_metadata_overrides.items():
                token = str(key or "").strip()
                if not token:
                    continue
                ctx.metadata[token] = (
                    dict(value)
                    if token == "runtime_env" and isinstance(value, Mapping)
                    else str(value)
                )
        try:
            ctx.blast_radius_adapter = turn_boundary_adapter
        except AttributeError:
            pass
        return ctx

    def _emit_tool_started_progress(
        self, allowed_calls: list[ProviderToolCall]
    ) -> None:
        progress_callback = getattr(self._runtime, "progress_callback", None)
        if not callable(progress_callback):
            return
        for call in allowed_calls:
            try:
                progress_callback(
                    {
                        "kind": "tool_started",
                        "tool_name": str(getattr(call, "name", "") or ""),
                        "args": dict(getattr(call, "arguments", {}) or {}),
                        "call_id": str(getattr(call, "id", "") or ""),
                        "model_tool_name": "",
                        "runtime_tool_name": "",
                        "runtime_binding_id": "",
                        "runtime_fallback_used": False,
                        "runtime_fallback_chain": [],
                        "runtime_resolution_source": "",
                        "fallback_index": 0,
                        "state": "running",
                    }
                )
            except Exception:
                pass

    def _emit_tool_completed_progress(
        self,
        *,
        batch: ToolExecutionBatch,
        allowed_calls: list[ProviderToolCall],
        batch_duration_ms: int,
    ) -> None:
        progress_callback = getattr(self._runtime, "progress_callback", None)
        if not callable(progress_callback):
            return
        for index, result in enumerate(list(batch.results or [])):
            call = allowed_calls[index] if index < len(allowed_calls) else None
            result_content = str(
                getattr(result, "content", "")
                or getattr(result, "data", "")
                or getattr(result, "error", "")
                or ""
            )
            duration_ms = getattr(result, "duration_ms", None)
            result_data = getattr(result, "data", {}) or {}
            try:
                progress_callback(
                    {
                        "kind": "tool_completed",
                        "tool_name": str(
                            getattr(result, "tool_name", "")
                            or getattr(call, "name", "")
                            or ""
                        ),
                        "args": dict(getattr(call, "arguments", {}) or {}),
                        "call_id": str(
                            getattr(result, "call_id", "")
                            or getattr(call, "id", "")
                            or ""
                        ),
                        "content": result_content,
                        "ok": bool(getattr(result, "ok", False)),
                        "duration_ms": int(
                            batch_duration_ms if duration_ms is None else duration_ms
                        ),
                        "batch_duration_ms": batch_duration_ms,
                        "exit_code": 0 if bool(getattr(result, "ok", False)) else 1,
                        "truncated": False,
                        "model_tool_name": str(
                            result_data.get("model_tool_name", "") or ""
                        ),
                        "runtime_tool_name": str(
                            result_data.get("runtime_tool_name", "") or ""
                        ),
                        "runtime_binding_id": str(
                            result_data.get("runtime_binding_id", "") or ""
                        ),
                        "runtime_fallback_used": bool(
                            result_data.get("runtime_fallback_used", False)
                        ),
                        "runtime_fallback_chain": list(
                            result_data.get("runtime_fallback_chain", []) or []
                        ),
                        "runtime_resolution_source": str(
                            result_data.get("runtime_resolution_source", "") or ""
                        ),
                        "fallback_index": int(
                            getattr(result, "fallback_index", 0) or 0
                        ),
                        "state": str(getattr(result, "state", "ok") or "ok"),
                    }
                )
            except Exception:
                pass

    def _execute_allowed_tool_calls(
        self, *, allowed_calls: list[ProviderToolCall], ctx: ToolExecutionContext
    ) -> list[ToolExecutionResult]:
        if not allowed_calls:
            return []
        started_at = time.perf_counter()
        self._emit_tool_started_progress(allowed_calls)
        batch = self._service_port.tools.execute_calls(allowed_calls, context=ctx)
        self._emit_tool_completed_progress(
            batch=batch,
            allowed_calls=allowed_calls,
            batch_duration_ms=int((time.perf_counter() - started_at) * 1000),
        )
        return list(batch.results)

    def _observe_tool_loop_quality(
        self,
        tool_calls: list[ProviderToolCall],
    ) -> list[dict[str, str]]:
        observations = observe_tool_calls(
            tool_calls,
            seen_signatures=getattr(
                self._runtime,
                "tool_call_signature_counts",
                None,
            ),
        )
        if not observations:
            return []
        runtime_observations = getattr(self._runtime, "tool_loop_observations", None)
        if isinstance(runtime_observations, list):
            runtime_observations.extend(observations)
        progress_callback = getattr(self._runtime, "progress_callback", None)
        if not callable(progress_callback):
            return observations
        for observation in observations:
            try:
                progress_callback(dict(observation))
            except Exception:
                pass
        return observations

    async def execute_tool_calls(
        self,
        tool_calls: list[ProviderToolCall],
        *,
        tool_budget_state: ToolBudgetState | None,
        context_metadata_overrides: Mapping[str, Any] | None = None,
    ) -> tuple[ToolExecutionBatch, list[dict[str, str]], bool]:
        from openminion.services.security.blast_radius.wiring import (
            SEAM_AGENT_EXECUTOR_RUNTIME,
            build_default_composition_boundary_adapter,
        )

        turn_boundary_adapter = build_default_composition_boundary_adapter(
            seam_id=SEAM_AGENT_EXECUTOR_RUNTIME,
        )
        self._observe_tool_loop_quality(tool_calls)
        policy_adapter = self._build_policy_adapter(
            tool_budget_state=tool_budget_state,
            turn_boundary_adapter=turn_boundary_adapter,
        )
        (
            allowed_calls,
            security_events,
            denied_results,
        ) = await self._filter_allowed_tool_calls(
            tool_calls,
            policy_adapter=policy_adapter,
        )
        ctx = self._build_execution_context_with_overrides(
            context_metadata_overrides=context_metadata_overrides,
            turn_boundary_adapter=turn_boundary_adapter,
        )
        batch_results = self._execute_allowed_tool_calls(
            allowed_calls=allowed_calls,
            ctx=ctx,
        )
        batch_results.extend(denied_results)
        return (
            ToolExecutionBatch(results=batch_results),
            security_events,
            bool(denied_results),
        )

    def record_self_improvement(
        self, *, user_message: str, tool_results: list[ToolExecutionResult]
    ) -> None:
        if (
            not self._service_port.self_improvement
            or not self._service_port.self_improvement.enabled
        ):
            return
        if not tool_results:
            return
        filtered = [
            result for result in tool_results if str(result.source or "") != "policy"
        ]
        if not filtered:
            return
        captured = self._service_port.self_improvement.capture_tool_failures(
            agent_id=self._service_port.identity_agent_id,
            user_message=user_message,
            tool_results=filtered,
        )
        self._runtime.self_improvement_metadata["improvement_notes_captured_count"] = (
            str(len(captured))
        )
        if self._service_port.self_improvement.is_review_first and captured:
            self._runtime.self_improvement_metadata["improvement_review_required"] = (
                "true"
            )

    def record_argument_failure(
        self, *, tool_name: str, missing_fields: str, user_message: str
    ) -> None:
        if (
            not self._service_port.self_improvement
            or not self._service_port.self_improvement.enabled
        ):
            return
        self.record_self_improvement(
            user_message=user_message,
            tool_results=[
                ToolExecutionResult(
                    tool_name=tool_name or "unknown",
                    ok=False,
                    verified=False,
                    content="",
                    error="missing_required_args",
                    data={"missing": missing_fields},
                    source="tool_arg_error",
                )
            ],
        )


ExecutorRuntimeMixin = ExecutorRuntime


__all__ = ["ExecutorRuntime", "ExecutorRuntimeMixin"]
