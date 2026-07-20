from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from openminion.modules.tool.base import ToolExecutionContext, ToolExecutionResult
from openminion.modules.tool.contracts import ProviderToolCall
from openminion.modules.tool.constants import OPENMINION_CONFIG_PATH_ENV
from openminion.modules.tool.registry.catalog import ToolSpec
from openminion.modules.tool.runtime.blast_radius import (
    TOOL_RESULT_BLAST_RADIUS_KEY,
    classify_tool_blast_radius,
)
from openminion.modules.tool.runtime.dispatch import (
    adapt_arguments_for_runtime_call,
    reorder_runtime_chain,
    resolve_binding_for_call,
)
from openminion.modules.tool.runtime.registry_toolspec import (
    execute_tool_spec_call as execute_tool_spec_call_registry_toolspec_runtime,
)
from openminion.modules.telemetry.events.module import (
    emit_module_counter,
    emit_module_telemetry,
)
from openminion.tools.config import resolve_tool_env

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry


@dataclass
class ToolExecutionBatch:
    results: list[ToolExecutionResult]

    @property
    def all_verified(self) -> bool:
        successful = [item for item in self.results if item.ok]
        if not successful:
            return False
        return all(item.verified for item in successful)

    @property
    def has_success(self) -> bool:
        return any(item.ok for item in self.results)

    def to_metadata_payload(self) -> str:
        return json.dumps(
            [
                {
                    "tool_name": item.tool_name,
                    "ok": item.ok,
                    "verified": item.verified,
                    "content": item.content,
                    "error": item.error,
                    "data": item.data,
                    "call_id": item.call_id,
                    "source": item.source,
                }
                for item in self.results
            ],
            sort_keys=True,
        )


def _runtime_env_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> Mapping[str, object] | None:
    if not isinstance(metadata, Mapping):
        return None
    runtime_env = metadata.get("runtime_env")
    if isinstance(runtime_env, Mapping):
        return runtime_env
    if isinstance(runtime_env, str):
        try:
            parsed = json.loads(runtime_env)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, Mapping):
            return parsed
    return None


def normalize_tool_call(call: ProviderToolCall) -> ProviderToolCall:
    tool_name = str(getattr(call, "name", "") or "").strip()
    raw_arguments = getattr(call, "arguments", {})
    arguments: dict[str, Any] = (
        dict(raw_arguments) if isinstance(raw_arguments, Mapping) else {}
    )

    nested_name = str(arguments.get("name", "") or "").strip()
    nested_arguments = arguments.get("arguments")
    if isinstance(nested_arguments, Mapping):
        if nested_name and (not tool_name or nested_name == tool_name):
            tool_name = nested_name
            arguments = dict(nested_arguments)
        elif not nested_name:
            arguments = dict(nested_arguments)

    if (
        "input" in arguments
        and isinstance(arguments.get("input"), Mapping)
        and len(arguments) == 1
    ):
        arguments = dict(arguments["input"])

    return ProviderToolCall(
        name=tool_name,
        arguments=arguments,
        id=str(getattr(call, "id", "") or ""),
        source=str(getattr(call, "source", "") or ""),
        depends_on=_normalized_depends_on(call),
    )


def is_retry_eligible_for_fallback(
    result: ToolExecutionResult,
    *,
    runtime_binding_policies: Any,
) -> bool:
    if bool(getattr(result, "ok", False)):
        return False
    error = str(getattr(result, "error", "") or "").strip().lower()
    data = getattr(result, "data", {}) or {}
    error_code = str(data.get("error_code", "") or "").strip().lower()
    joined_error = " ".join(part for part in (error, error_code) if part).strip()
    if not joined_error:
        return False

    from openminion.modules.tool.runtime.policy import ToolBindingPolicyManager

    manager = ToolBindingPolicyManager.from_runtime_binding_policy_payload(
        runtime_binding_policies
        if isinstance(runtime_binding_policies, Mapping)
        else None
    )
    return manager.should_fallback(error_text=joined_error)


def dependency_error_result(
    *,
    call: ProviderToolCall,
    error_code: str,
    reason_code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
) -> ToolExecutionResult:
    payload: dict[str, Any] = {
        "error_code": str(error_code or "").strip().lower() or "dependency_error",
        "reason_code": str(reason_code or "").strip().upper() or "DEPENDENCY_ERROR",
    }
    if details:
        payload.update(dict(details))
    return ToolExecutionResult(
        tool_name=str(getattr(call, "name", "") or "").strip() or "unknown",
        ok=False,
        content="",
        verified=False,
        error=message,
        call_id=str(getattr(call, "id", "") or ""),
        source=str(getattr(call, "source", "") or ""),
        data=payload,
    )


def _runtime_resolution_data(
    *,
    resolution: Any,
    runtime_binding_id: str,
    runtime_tool_name: str,
    runtime_fallback_chain: Sequence[str],
    runtime_fallback_used: bool,
) -> dict[str, Any]:
    return {
        "model_tool_name": resolution.model_tool_id,
        "runtime_binding_id": runtime_binding_id,
        "runtime_tool_name": runtime_tool_name,
        "runtime_fallback_chain": list(runtime_fallback_chain),
        "runtime_fallback_used": runtime_fallback_used,
        "runtime_resolution_source": resolution.source,
    }


def _tool_contract_metadata(tool: Any) -> dict[str, Any]:
    if not isinstance(tool, ToolSpec):
        return {}
    metadata: dict[str, Any] = {
        TOOL_RESULT_BLAST_RADIUS_KEY: classify_tool_blast_radius(tool).blast_radius,
    }
    min_scope = str(getattr(tool, "min_scope", "") or "").strip().upper()
    if min_scope:
        metadata["tool_min_scope"] = min_scope
    return metadata


def _unknown_tool_result(
    *,
    call: ProviderToolCall,
    raw_tool_name: str,
    resolution: Any | None = None,
    runtime_binding_id: str = "",
    runtime_tool_name: str = "",
    runtime_fallback_chain: Sequence[str] = (),
) -> ToolExecutionResult:
    data: dict[str, Any] = {
        "error_code": "unknown_tool_name",
        "reason_code": "UNKNOWN_TOOL_NAME",
    }
    if resolution is not None:
        data.update(
            _runtime_resolution_data(
                resolution=resolution,
                runtime_binding_id=runtime_binding_id,
                runtime_tool_name=runtime_tool_name,
                runtime_fallback_chain=runtime_fallback_chain,
                runtime_fallback_used=False,
            )
        )
    return ToolExecutionResult(
        tool_name=raw_tool_name or "unknown",
        ok=False,
        content="",
        verified=False,
        error=f"unknown tool '{raw_tool_name}'",
        call_id=call.id,
        source=call.source,
        data=data,
    )


def _hidden_tool_result(
    *,
    call: ProviderToolCall,
    decision: Any,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=str(call.name or "").strip() or "unknown",
        ok=False,
        content="",
        verified=False,
        error="tool is not active in the current exposure profile",
        call_id=call.id,
        source=call.source,
        data={
            "error_code": "tool_exposure_denied",
            "reason_code": decision.reason_code or "profile_inactive",
            "profile_id": str(getattr(decision, "profile_id", "") or ""),
            "activation_id": str(getattr(decision, "activation_id", "") or ""),
            "target_id": str(getattr(decision, "target_id", "") or ""),
        },
    )


def _normalized_depends_on(call: ProviderToolCall) -> list[str]:
    raw_depends_on = getattr(call, "depends_on", []) or []
    if isinstance(raw_depends_on, str):
        values = [raw_depends_on]
    elif isinstance(raw_depends_on, (list, tuple, set)):
        values = list(raw_depends_on)
    else:
        values = []

    seen: set[str] = set()
    normalized: list[str] = []
    for dep in values:
        token = str(dep).strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _stamp_execution_facts(
    result: ToolExecutionResult,
    *,
    started_at: float,
) -> ToolExecutionResult:
    ended_at = time.time()
    result.started_at = started_at
    result.ended_at = ended_at
    result.duration_ms = int(max(0.0, (ended_at - started_at)) * 1000)
    if result.ok:
        result.state = "ok"
    else:
        data = result.data if isinstance(result.data, Mapping) else {}
        reason_code = str(data.get("reason_code", "") or "").upper()
        error_code = str(data.get("error_code", "") or "").lower()
        if reason_code.startswith("POLICY_") or error_code in {
            "policy_denied",
            "approval_denied",
            "confirm_required",
            "require_approval",
            "tool_exposure_denied",
        }:
            result.state = "denied"
        else:
            result.state = "error"
    return result


def _emit_tool_execution_counter(
    context: ToolExecutionContext,
    *,
    counter_name: str,
    status: str,
    extra: Mapping[str, Any] | None = None,
) -> bool:
    telemetryctl = getattr(context, "telemetryctl", None)
    if telemetryctl is None:
        return False
    metadata = context.metadata if isinstance(context.metadata, Mapping) else {}

    def _emit(method_name: str, *args: Any, **kwargs: Any) -> bool:
        return emit_module_telemetry(
            telemetryctl,
            method_name,
            *args,
            logger=logging.getLogger(__name__),
            **kwargs,
        )

    return emit_module_counter(
        emit_module_telemetry_fn=_emit,
        session_id=str(context.session_id or metadata.get("session_id") or ""),
        turn_id=str(metadata.get("turn_id") or metadata.get("trace_id") or ""),
        module_id="openminion-tool",
        counter_name=counter_name,
        value=1.0,
        status=status,
        extra=dict(extra or {}),
    )


def _is_timeout_result(result: ToolExecutionResult) -> bool:
    data = result.data if isinstance(result.data, Mapping) else {}
    tokens = {
        str(data.get("error_code", "") or "").lower(),
        str(data.get("reason_code", "") or "").lower(),
        str(result.error or "").lower(),
    }
    return any("timeout" in token or "timed out" in token for token in tokens)


def _stamp_and_emit_tool_result(
    context: ToolExecutionContext,
    *,
    result: ToolExecutionResult,
    started_at: float,
) -> ToolExecutionResult:
    stamped = _stamp_execution_facts(result, started_at=started_at)
    if stamped.ok:
        counter_name = "tool_execution_success"
        status = "ok"
    elif _is_timeout_result(stamped):
        counter_name = "tool_execution_timeout"
        status = "error"
    else:
        counter_name = "tool_execution_failure"
        status = "error"
    _emit_tool_execution_counter(
        context,
        counter_name=counter_name,
        status=status,
        extra={
            "tool_name": stamped.tool_name,
            "state": stamped.state,
            "duration_ms": int(stamped.duration_ms or 0),
        },
    )
    return stamped


def _runtime_direct_allowed(
    registry: "ToolRegistry",
    *,
    context: ToolExecutionContext,
    raw_tool_name: str,
) -> bool:
    metadata = context.metadata
    if not isinstance(metadata, Mapping):
        return True
    origin = str(metadata.get("tool_call_origin", "") or "").strip().lower()
    if origin != "model":
        return True
    tool = registry._tools.get(raw_tool_name)
    if isinstance(tool, ToolSpec) and bool(
        getattr(tool, "prompt_visible_runtime_name", False)
    ):
        return True
    override = str(metadata.get("allow_runtime_direct", "") or "").strip().lower()
    return override in {"1", "true", "yes", "on"}


def _sidecar_start_failure(
    registry: "ToolRegistry",
    *,
    tool: ToolSpec,
    runtime_tool_name: str,
    call: ProviderToolCall,
    env_owner: Any,
) -> ToolExecutionResult | None:
    if not tool.sidecar:
        return None
    try:
        autostart = registry.ensure_sidecar_autostart(
            name=tool.sidecar,
            config_path=env_owner.get(OPENMINION_CONFIG_PATH_ENV, "") or None,
            runtime_env=env_owner.snapshot(),
            interactive=bool(sys.stdin.isatty()),
            logger=logging.getLogger("openminion.sidecars"),
        )
    except Exception as exc:
        return ToolExecutionResult(
            tool_name=runtime_tool_name or "unknown",
            ok=False,
            content="",
            verified=False,
            error=f"sidecar '{tool.sidecar}' autostart failed: {exc}",
            call_id=call.id,
            source=call.source,
            data={"sidecar": tool.sidecar},
        )
    if autostart.get("enabled", False):
        return None
    return ToolExecutionResult(
        tool_name=runtime_tool_name or "unknown",
        ok=False,
        content="",
        verified=False,
        error=f"sidecar '{tool.sidecar}' not enabled",
        call_id=call.id,
        source=call.source,
        data={"sidecar": tool.sidecar, "autostart": autostart},
    )


def execute_single_call(
    registry: "ToolRegistry",
    *,
    call: ProviderToolCall,
    context: ToolExecutionContext,
    available_tool_names: tuple[str, ...],
    runtime_binding_policies: Any,
) -> ToolExecutionResult:
    started_at = time.time()
    raw_tool_name = str(call.name).strip()
    _emit_tool_execution_counter(
        context,
        counter_name="tool_execution_started",
        status="ok",
        extra={"tool_name": raw_tool_name or "unknown"},
    )
    resolution = resolve_binding_for_call(
        raw_tool_name=raw_tool_name,
        available_tool_names=available_tool_names,
        allow_runtime_direct=_runtime_direct_allowed(
            registry,
            context=context,
            raw_tool_name=raw_tool_name,
        ),
    )
    if resolution is None:
        return _stamp_and_emit_tool_result(
            context,
            result=_unknown_tool_result(call=call, raw_tool_name=raw_tool_name),
            started_at=started_at,
        )

    from openminion.modules.tool.exposure import exposure_scope

    scope = exposure_scope(
        context.metadata if isinstance(context.metadata, Mapping) else None
    )
    exposure_service = getattr(registry, "exposure_service", None)
    decision = None
    if exposure_service is not None and exposure_service.profiles:
        decision = exposure_service.decide(
            resolution.model_tool_id or raw_tool_name,
            **scope,
        )
    if decision is not None and decision.state != "visible":
        exposure_service.record_refusal(decision, **scope)
        result = _hidden_tool_result(call=call, decision=decision)
        _emit_tool_execution_counter(
            context,
            counter_name="tool_exposure_refused",
            status="denied",
            extra={
                "tool_name": result.tool_name,
                "profile_id": decision.profile_id,
                "reason_code": decision.reason_code or "profile_inactive",
            },
        )
        return _stamp_and_emit_tool_result(
            context,
            result=result,
            started_at=started_at,
        )

    fallback_chain = reorder_runtime_chain(
        runtime_binding_id=resolution.runtime_binding_id,
        default_chain=tuple(resolution.runtime_fallback_chain),
        runtime_binding_policies=(
            dict(runtime_binding_policies)
            if isinstance(runtime_binding_policies, Mapping)
            else None
        ),
        available_tool_names=available_tool_names,
    )
    if not fallback_chain:
        return _stamp_and_emit_tool_result(
            context,
            result=_unknown_tool_result(
                call=call,
                raw_tool_name=raw_tool_name,
                resolution=resolution,
                runtime_binding_id=resolution.runtime_binding_id,
            ),
            started_at=started_at,
        )

    raw_arguments: Mapping[str, Any]
    if isinstance(call.arguments, dict):
        raw_arguments = call.arguments
    else:
        raw_arguments = {}
    env_owner = resolve_tool_env(
        runtime_env=_runtime_env_from_metadata(
            context.metadata if isinstance(context.metadata, Mapping) else None
        )
    )

    last_result: ToolExecutionResult | None = None
    for idx, runtime_tool_name in enumerate(fallback_chain):
        tool = registry._tools.get(runtime_tool_name)
        if tool is None:
            continue

        effective_runtime_binding_id = resolution.runtime_binding_id
        if (
            not effective_runtime_binding_id
            and isinstance(tool, ToolSpec)
            and str(getattr(tool, "runtime_binding_id", "") or "").strip()
        ):
            effective_runtime_binding_id = str(
                getattr(tool, "runtime_binding_id", "") or ""
            ).strip()

        arguments = adapt_arguments_for_runtime_call(
            model_tool_id=resolution.model_tool_id,
            runtime_binding_id=effective_runtime_binding_id,
            runtime_tool_name=runtime_tool_name,
            arguments=raw_arguments,
        )

        if isinstance(tool, ToolSpec):
            last_result = _sidecar_start_failure(
                registry,
                tool=tool,
                runtime_tool_name=runtime_tool_name,
                call=call,
                env_owner=env_owner,
            )
            if last_result is not None:
                break

        boundary_adapter = getattr(context, "blast_radius_adapter", None)
        if boundary_adapter is not None and isinstance(tool, ToolSpec):
            boundary_adapter.step(tool)

        if isinstance(tool, ToolSpec):
            executed = execute_tool_spec_call_registry_toolspec_runtime(
                tool=tool,
                arguments=arguments,
                context=context,
            )
        else:
            executed = tool.execute(arguments=arguments, context=context)

        enriched_data = dict(executed.data or {})
        enriched_data.update(
            _runtime_resolution_data(
                resolution=resolution,
                runtime_binding_id=effective_runtime_binding_id,
                runtime_tool_name=runtime_tool_name,
                runtime_fallback_chain=fallback_chain,
                runtime_fallback_used=idx > 0,
            )
        )
        enriched_data.update(_tool_contract_metadata(tool))
        if decision is not None and decision.profile_id:
            enriched_data["tool_exposure"] = {
                "profile_id": decision.profile_id,
                "activation_id": decision.activation_id,
                "target_id": decision.target_id,
            }
        last_result = ToolExecutionResult(
            tool_name=runtime_tool_name or resolution.runtime_tool_name or "unknown",
            ok=bool(executed.ok),
            content=str(executed.content or ""),
            verified=bool(executed.verified),
            error=str(executed.error or ""),
            data=enriched_data,
            call_id=call.id,
            source=call.source or executed.source,
        )
        last_result.fallback_index = idx
        if last_result.ok:
            break
        if not is_retry_eligible_for_fallback(
            last_result,
            runtime_binding_policies=runtime_binding_policies,
        ):
            break

    if last_result is None:
        return _stamp_and_emit_tool_result(
            context,
            result=_unknown_tool_result(
                call=call,
                raw_tool_name=raw_tool_name,
                resolution=resolution,
                runtime_binding_id=resolution.runtime_binding_id,
                runtime_fallback_chain=fallback_chain,
            ),
            started_at=started_at,
        )
    if decision is not None:
        exposure_service.record_invocation(decision, **scope)
    return _stamp_and_emit_tool_result(
        context,
        result=last_result,
        started_at=started_at,
    )


def execute_calls_with_dependencies(
    registry: "ToolRegistry",
    *,
    calls: Sequence[ProviderToolCall],
    context: ToolExecutionContext,
    available_tool_names: tuple[str, ...],
    runtime_binding_policies: Any,
) -> list[ToolExecutionResult]:
    node_order: list[str] = []
    call_by_node: dict[str, ProviderToolCall] = {}
    ref_to_node: dict[str, str] = {}
    deps_by_node: dict[str, list[str]] = {}
    result_by_node: dict[str, ToolExecutionResult] = {}

    for idx, call in enumerate(calls):
        provided_id = str(getattr(call, "id", "") or "").strip()
        node_id = provided_id or f"__call_{idx}"
        if provided_id and provided_id in ref_to_node:
            node_id = f"__dup_call_{idx}"
            result_by_node[node_id] = dependency_error_result(
                call=call,
                error_code="invalid_dependency_graph",
                reason_code="DUPLICATE_CALL_ID",
                message=f"duplicate tool call id '{provided_id}'",
                details={"duplicate_call_id": provided_id},
            )
        else:
            if provided_id:
                ref_to_node[provided_id] = node_id
        node_order.append(node_id)
        call_by_node[node_id] = call

    for node_id in node_order:
        call = call_by_node[node_id]
        normalized_deps: list[str] = []
        seen: set[str] = set()
        raw_depends_on = getattr(call, "depends_on", []) or []
        for dep in raw_depends_on:
            dep_ref = str(dep or "").strip()
            if not dep_ref or dep_ref in seen:
                continue
            seen.add(dep_ref)
            dep_node = ref_to_node.get(dep_ref)
            if dep_node is None:
                if node_id not in result_by_node:
                    result_by_node[node_id] = dependency_error_result(
                        call=call,
                        error_code="invalid_dependency_graph",
                        reason_code="UNKNOWN_DEPENDENCY",
                        message=f"unknown dependency '{dep_ref}'",
                        details={"unknown_dependency": dep_ref},
                    )
                continue
            normalized_deps.append(dep_node)
        deps_by_node[node_id] = normalized_deps

    pending = [node_id for node_id in node_order if node_id not in result_by_node]
    while pending:
        ready = [
            node_id
            for node_id in pending
            if all(dep in result_by_node for dep in deps_by_node.get(node_id, []))
        ]
        if not ready:
            for node_id in list(pending):
                if node_id in result_by_node:
                    continue
                call = call_by_node[node_id]
                result_by_node[node_id] = dependency_error_result(
                    call=call,
                    error_code="dependency_cycle",
                    reason_code="DEPENDENCY_CYCLE",
                    message="tool dependency cycle detected",
                    details={"depends_on": deps_by_node.get(node_id, [])},
                )
            break

        for node_id in ready:
            call = call_by_node[node_id]
            deps = deps_by_node.get(node_id, [])
            failed_deps = [
                dep_id
                for dep_id in deps
                if not bool(result_by_node.get(dep_id).ok)  # type: ignore[union-attr]
            ]
            if failed_deps:
                failed_refs: list[str] = []
                for dep_id in failed_deps:
                    dep_call = call_by_node.get(dep_id)
                    ref = str(getattr(dep_call, "id", "") or "").strip() or dep_id
                    failed_refs.append(ref)
                result_by_node[node_id] = dependency_error_result(
                    call=call,
                    error_code="dependency_failed",
                    reason_code="DEPENDENCY_FAILED",
                    message="tool execution skipped due to failed dependency",
                    details={"failed_dependencies": failed_refs},
                )
            else:
                result_by_node[node_id] = execute_single_call(
                    registry,
                    call=call,
                    context=context,
                    available_tool_names=available_tool_names,
                    runtime_binding_policies=runtime_binding_policies,
                )
            pending.remove(node_id)

    return [result_by_node[node_id] for node_id in node_order]


def _duplicate_call_id_results(
    calls: Sequence[ProviderToolCall],
) -> dict[int, ToolExecutionResult]:
    duplicate_results: dict[int, ToolExecutionResult] = {}
    seen_ids: set[str] = set()
    for idx, call in enumerate(calls):
        provided_id = str(getattr(call, "id", "") or "").strip()
        if not provided_id:
            continue
        if provided_id in seen_ids:
            duplicate_results[idx] = dependency_error_result(
                call=call,
                error_code="invalid_dependency_graph",
                reason_code="DUPLICATE_CALL_ID",
                message=f"duplicate tool call id '{provided_id}'",
                details={"duplicate_call_id": provided_id},
            )
            continue
        seen_ids.add(provided_id)
    return duplicate_results


def execute_calls(
    registry: "ToolRegistry",
    tool_calls: Sequence[ProviderToolCall],
    *,
    context: ToolExecutionContext,
) -> ToolExecutionBatch:
    normalized_calls = [normalize_tool_call(raw_call) for raw_call in tool_calls]
    if not normalized_calls:
        return ToolExecutionBatch(results=[])

    available_tool_names = tuple(registry._tools.keys())
    runtime_binding_policies = None
    if isinstance(getattr(context, "metadata", None), Mapping):
        runtime_binding_policies = context.metadata.get("runtime_binding_policies")
    duplicate_results = _duplicate_call_id_results(normalized_calls)
    if not any(bool(getattr(call, "depends_on", [])) for call in normalized_calls):
        results = []
        for idx, call in enumerate(normalized_calls):
            duplicate_result = duplicate_results.get(idx)
            if duplicate_result is not None:
                results.append(duplicate_result)
                continue
            results.append(
                execute_single_call(
                    registry,
                    call=call,
                    context=context,
                    available_tool_names=available_tool_names,
                    runtime_binding_policies=runtime_binding_policies,
                )
            )

        return ToolExecutionBatch(results=results)

    return ToolExecutionBatch(
        results=execute_calls_with_dependencies(
            registry,
            calls=normalized_calls,
            context=context,
            available_tool_names=available_tool_names,
            runtime_binding_policies=runtime_binding_policies,
        )
    )
