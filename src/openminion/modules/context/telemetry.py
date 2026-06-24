from typing import Any, Callable

from openminion.modules.telemetry.events.module import (
    emit_module_counter as _emit_module_counter_impl,
    emit_module_operation as _emit_module_operation_impl,
    emit_module_telemetry as _emit_module_telemetry_impl,
    run_telemetry_result as _run_telemetry_result_impl,
)


def emit_pack_module_telemetry(
    *,
    emit_module_operation_fn: Callable[..., bool],
    emit_module_counter_fn: Callable[..., bool],
    session_id: str,
    turn_id: str,
    pack: Any,
    module_id: str,
    drop_count: int,
    truncation_count: int,
    cache_hit: bool,
    mode: str | None = None,
) -> None:
    extra = {
        "pack_version": pack.pack_version,
        "prompt_context_id": pack.prompt_context_id or "",
        "cache_hit": cache_hit,
    }
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode:
        extra["mode"] = normalized_mode
    emit_module_operation_fn(
        session_id=session_id,
        turn_id=turn_id,
        module_id=module_id,
        operation="pack_build",
        count=1,
        extra=extra,
    )
    if drop_count > 0:
        emit_module_operation_fn(
            session_id=session_id,
            turn_id=turn_id,
            module_id=module_id,
            operation="drop",
            count=drop_count,
            extra=extra,
        )
    if truncation_count > 0:
        emit_module_operation_fn(
            session_id=session_id,
            turn_id=turn_id,
            module_id=module_id,
            operation="truncate",
            count=truncation_count,
            extra=extra,
        )
    emit_module_counter_fn(
        session_id=session_id,
        turn_id=turn_id,
        module_id=module_id,
        counter_name="dropped_segments",
        value=float(max(0, drop_count)),
        extra=extra,
    )
    emit_module_counter_fn(
        session_id=session_id,
        turn_id=turn_id,
        module_id=module_id,
        counter_name="truncated_segments",
        value=float(max(0, truncation_count)),
        extra=extra,
    )


def emit_identity_audit_events(
    *,
    sessctl: Any,
    session_id: str,
    agent_id: str,
    purpose: str,
    profile_version: str,
    render_version: str,
) -> None:
    bind_agent = getattr(sessctl, "bind_agent", None)
    if callable(bind_agent):
        try:
            bind_agent(
                session_id=session_id,
                agent_id=agent_id,
                profile_version=profile_version,
            )
        except Exception:
            pass
    append_started = getattr(sessctl, "append_llm_request_started", None)
    if callable(append_started):
        try:
            append_started(
                session_id=session_id,
                purpose=purpose,
                profile_version=profile_version,
                render_version=render_version,
                agent_id=agent_id,
            )
        except Exception:
            pass


def emit_pack_manifest_event(
    *,
    sessctl: Any,
    session_id: str,
    agent_id: str,
    pack: Any,
    cache_hit: bool,
    llm_call_id: str = "",
) -> None:
    emit_canonical = getattr(sessctl, "emit_canonical_event", None)
    manifest = pack.context_manifest
    report = pack.token_budget_report

    payload = {
        "pack_version": pack.pack_version,
        "prompt_cache_key": pack.prompt_cache_key,
        "static_prefix_hash": pack.static_prefix_hash,
        "cache_hit": cache_hit,
        "total_used_tokens": report.total_used_tokens if report else 0,
        "total_cap_tokens": report.total_cap_tokens if report else 0,
        "included_segment_ids": manifest.included_segment_ids if manifest else [],
        "dropped_segment_ids": manifest.dropped_segment_ids if manifest else [],
        "recalled_memory": manifest.recalled_memory if manifest else [],
        "warnings": list(pack.warnings),
        "llm_call_id": llm_call_id or (manifest.llm_call_id if manifest else ""),
        "prompt_context_id": pack.prompt_context_id
        or (manifest.prompt_context_id if manifest else None),
        "pack_policy_used": manifest.pack_policy_used if manifest else "",
        "retrievers_used": manifest.retrievers_used if manifest else [],
        "compressors_used": manifest.compressors_used if manifest else [],
        "identity_budget": {},
    }
    if manifest and manifest.context_budget_tier is not None:
        payload["context_budget_tier"] = manifest.context_budget_tier

    if callable(emit_canonical):
        try:
            emit_canonical(
                session_id=session_id,
                event_type="context.manifest.created",
                payload=payload,
                actor_type="system",
                actor_id=agent_id,
            )
            return
        except Exception:
            pass

    append_event = getattr(sessctl, "append_event", None)
    if not callable(append_event):
        return

    try:
        append_event(
            session_id=session_id,
            type="context.manifest.created",
            payload=payload,
            agent_id=agent_id,
            status="ok",
        )
    except Exception:
        pass


def record_cache_metrics(
    *,
    sessctl: Any,
    session_id: str,
    agent_id: str,
    prompt_cache_key: str,
    cached_tokens: int,
    total_tokens: int,
    provider: str,
) -> None:
    emit_canonical = getattr(sessctl, "emit_canonical_event", None)
    if callable(emit_canonical):
        try:
            emit_canonical(
                session_id=session_id,
                event_type="llm.cache.metrics",
                payload={
                    "prompt_cache_key": prompt_cache_key,
                    "cached_tokens": cached_tokens,
                    "total_tokens": total_tokens,
                    "provider": provider,
                },
                actor_type="system",
                actor_id=agent_id,
            )
        except Exception:
            pass


class ContextTelemetryBridge:
    def __init__(
        self,
        *,
        sessctl: Any,
        telemetryctl: Any,
        logger: Any,
        module_id: str,
    ) -> None:
        self._sessctl = sessctl
        self._telemetryctl = telemetryctl
        self._logger = logger
        self._module_id = module_id

    def record_cache_metrics(
        self,
        *,
        session_id: str,
        agent_id: str,
        prompt_cache_key: str,
        cached_tokens: int,
        total_tokens: int,
        provider: str,
    ) -> None:
        record_cache_metrics(
            sessctl=self._sessctl,
            session_id=session_id,
            agent_id=agent_id,
            prompt_cache_key=prompt_cache_key,
            cached_tokens=cached_tokens,
            total_tokens=total_tokens,
            provider=provider,
        )

    def emit_identity_audit_events(
        self,
        *,
        session_id: str,
        agent_id: str,
        purpose: str,
        profile_version: str,
        render_version: str,
    ) -> None:
        emit_identity_audit_events(
            sessctl=self._sessctl,
            session_id=session_id,
            agent_id=agent_id,
            purpose=purpose,
            profile_version=profile_version,
            render_version=render_version,
        )

    def emit_pack_manifest_event(
        self,
        *,
        session_id: str,
        agent_id: str,
        pack: Any,
        cache_hit: bool,
        llm_call_id: str = "",
    ) -> None:
        emit_pack_manifest_event(
            sessctl=self._sessctl,
            session_id=session_id,
            agent_id=agent_id,
            pack=pack,
            cache_hit=cache_hit,
            llm_call_id=llm_call_id,
        )

    def emit_pack_module_telemetry(
        self,
        *,
        session_id: str,
        turn_id: str,
        pack: Any,
        drop_count: int,
        truncation_count: int,
        cache_hit: bool,
        mode: str | None = None,
    ) -> None:
        emit_pack_module_telemetry(
            emit_module_operation_fn=self._emit_module_operation,
            emit_module_counter_fn=self._emit_module_counter,
            session_id=session_id,
            turn_id=turn_id,
            pack=pack,
            module_id=self._module_id,
            drop_count=drop_count,
            truncation_count=truncation_count,
            cache_hit=cache_hit,
            mode=mode,
        )

    def _emit_module_operation(
        self,
        *,
        session_id: str,
        turn_id: str,
        module_id: str,
        operation: str,
        count: int = 1,
        status: str = "ok",
        extra: dict[str, Any] | None = None,
    ) -> bool:
        return _emit_module_operation_impl(
            emit_module_telemetry_fn=self._emit_module_telemetry,
            session_id=session_id,
            turn_id=turn_id,
            module_id=module_id,
            operation=operation,
            count=count,
            status=status,
            extra=extra,
        )

    def _emit_module_counter(
        self,
        *,
        session_id: str,
        turn_id: str,
        module_id: str,
        counter_name: str,
        value: float,
        status: str = "ok",
        extra: dict[str, Any] | None = None,
    ) -> bool:
        return _emit_module_counter_impl(
            emit_module_telemetry_fn=self._emit_module_telemetry,
            session_id=session_id,
            turn_id=turn_id,
            module_id=module_id,
            counter_name=counter_name,
            value=value,
            status=status,
            extra=extra,
        )

    def _emit_module_telemetry(
        self, method_name: str, *args: Any, **kwargs: Any
    ) -> bool:
        return _emit_module_telemetry_impl(
            self._telemetryctl,
            method_name,
            *args,
            logger=self._logger,
            run_telemetry_result_fn=self._run_telemetry_result,
            **kwargs,
        )

    def _run_telemetry_result(self, result: Any) -> bool:
        return _run_telemetry_result_impl(result, logger=self._logger)
