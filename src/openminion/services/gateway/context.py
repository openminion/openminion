import logging
from typing import Any, Callable, cast

from openminion.base.types import Message
from openminion.modules.context.knowledge.constants import (
    EVENT_QUERY_COMPLETED,
    EVENT_QUERY_DEGRADED,
    EVENT_QUERY_FAILED,
    EVENT_QUERY_STARTED,
    EVENT_SOURCE_RESOLVED,
    LAYER_THIRD_BRAIN,
)
from openminion.modules.context.knowledge.errors import KnowledgeGraphError
from openminion.modules.context.knowledge.models import (
    GraphQueryRequest,
    GraphQueryResult,
)
from openminion.services.constants import (
    MEMORY_CAPSULE_CACHEABLE_STRATEGIES,
    MEMORY_CAPSULE_STRATEGY_OFF,
)
from openminion.services.gateway.memory import (
    MEMORY_CONTEXT_BUILD_FAILED_CODE,
    MEMORY_CONTEXT_BUILD_FAILED_REASON,
    _text_fingerprint,
    memory_error_facts,
)
from openminion.modules.context.pack.evidence import (
    map_knowledge_evidence as _map_knowledge_evidence,
    map_memory_evidence as _map_memory_evidence,
    pack_evidence_context as _pack_evidence_context,
)
from openminion.modules.memory.surfacing.evidence import (
    MemoryRetrievalEvidenceSelection,
)
from openminion.services.gateway.types import TurnContext
from openminion.services.gateway.turn.runtime import (
    _append_knowledge_graph_context,
    _append_memory_retrieval_context,
    _inject_memory_context,
)

MemoryEventEmitter = Callable[..., None]


def history_has_prior_transcript(history: list[Message]) -> bool:
    for item in history:
        role = str(getattr(item, "metadata", {}).get("role", "") or "").strip().lower()
        if role in {"user", "assistant", "inbound", "outbound"}:
            return True
    return False


def _build_memory_capsule_payload(
    agent_memory: Any,
    *,
    session_id: str,
    user_message: str,
) -> tuple[str, dict[str, str]]:
    build_context_with_meta = getattr(agent_memory, "build_context_with_metadata", None)
    if callable(build_context_with_meta):
        return cast(
            tuple[str, dict[str, str]],
            build_context_with_meta(
                session_id=session_id,
                user_message=user_message,
            ),
        )
    return (
        str(
            agent_memory.build_context(
                session_id=session_id,
                user_message=user_message,
            )
        ),
        {},
    )


def _build_dynamic_retrieval_context(
    agent_memory: Any,
    *,
    session_id: str,
    user_message: str,
) -> tuple[str, dict[str, str]]:
    build_retrieval_with_meta = getattr(
        agent_memory,
        "build_retrieval_context_with_metadata",
        None,
    )
    if callable(build_retrieval_with_meta):
        return cast(
            tuple[str, dict[str, str]],
            build_retrieval_with_meta(
                session_id=session_id,
                user_message=user_message,
            ),
        )
    return (
        str(
            agent_memory.build_retrieval_context(
                session_id=session_id,
                user_message=user_message,
            )
        ),
        {},
    )


def _build_dynamic_retrieval_evidence(
    agent_memory: Any,
    *,
    session_id: str,
    user_message: str,
) -> MemoryRetrievalEvidenceSelection | None:
    build_items = getattr(agent_memory, "build_retrieval_evidence_items", None)
    if not callable(build_items):
        return None
    return cast(
        MemoryRetrievalEvidenceSelection,
        build_items(session_id=session_id, user_message=user_message),
    )


def _error_facts(exc: Exception) -> dict[str, str]:
    if isinstance(exc, KnowledgeGraphError):
        return {
            "error_code": exc.code,
            "reason_code": exc.code.lower(),
        }
    return {
        "error_code": "KNOWLEDGE_GRAPH_CONTEXT_FAILED",
        "reason_code": "knowledge_graph_context_failed",
    }


def _knowledge_graph_source_payload(knowledge_graphs: Any) -> dict[str, str]:
    list_sources = getattr(knowledge_graphs, "list_sources", None)
    if not callable(list_sources):
        return {"knowledge_graph_source_count": "0", "knowledge_graph_providers": ""}
    sources = tuple(list_sources(layer=LAYER_THIRD_BRAIN))
    providers = tuple(
        str(getattr(source, "name", "") or "").strip()
        for source in sources
        if str(getattr(source, "name", "") or "").strip()
    )
    return {
        "knowledge_graph_source_count": str(len(sources)),
        "knowledge_graph_providers": ",".join(providers),
    }


def _query_knowledge_graph_results(
    knowledge_graphs: Any,
    *,
    user_message: str,
) -> tuple[tuple[GraphQueryResult, ...], dict[str, str]]:
    if knowledge_graphs is None:
        return (), {}
    list_sources = getattr(knowledge_graphs, "list_sources", None)
    query = getattr(knowledge_graphs, "query", None)
    if not callable(list_sources) or not callable(query):
        return (), {}
    sources = tuple(list_sources(layer=LAYER_THIRD_BRAIN))
    if not sources:
        return (), {}
    request = GraphQueryRequest(
        query=user_message,
        max_results=None,
        max_chars=None,
        include_paths=True,
        include_explanations=True,
    )
    results: list[GraphQueryResult] = []
    failures: list[dict[str, str]] = []
    failed_exceptions: list[Exception] = []
    for source in sources:
        provider_name = str(getattr(source, "name", "") or "").strip()
        try:
            source_results = cast(
                tuple[GraphQueryResult, ...],
                query(
                    request,
                    provider_names=(provider_name,) if provider_name else None,
                    layer=LAYER_THIRD_BRAIN,
                ),
            )
        except TypeError:
            source_results = cast(
                tuple[GraphQueryResult, ...],
                query(request, layer=LAYER_THIRD_BRAIN),
            )
            results.extend(source_results)
            break
        except Exception as exc:
            facts = _error_facts(exc)
            failed_exceptions.append(exc)
            failures.append(
                {
                    "provider": provider_name or "<unknown>",
                    "error_code": facts["error_code"],
                    "reason_code": facts["reason_code"],
                }
            )
            continue
        results.extend(source_results)
    if failures and not results:
        raise failed_exceptions[0]
    meta = {
        "knowledge_graph_results": str(
            sum(len(result.items) + len(result.paths) for result in results)
        ),
        "knowledge_graph_omitted": str(sum(len(result.omitted) for result in results)),
        "knowledge_graph_providers": ",".join(result.provider for result in results),
        "knowledge_graph_truncated": "false",
    }
    if failures:
        meta = {
            **meta,
            "knowledge_graph_degraded": "true",
            "knowledge_graph_failed_providers": ",".join(
                failure["provider"] for failure in failures
            ),
            "knowledge_graph_failed_provider_error_codes": ",".join(
                f"{failure['provider']}:{failure['error_code']}" for failure in failures
            ),
        }
    return tuple(results), meta


def _build_cached_memory_context(
    agent_memory: Any,
    *,
    session_id: str,
    user_message: str,
    memory_strategy: str,
    memory_capsule_cache: dict[str, str],
) -> tuple[str, dict[str, str], bool]:
    memory_context_meta: dict[str, str] = {}
    capsule_cache_hit = False
    if memory_strategy in MEMORY_CAPSULE_CACHEABLE_STRATEGIES:
        capsule_cache_hit = session_id in memory_capsule_cache
        cached = memory_capsule_cache.get(session_id)
        if cached is None:
            cached, memory_context_meta = _build_memory_capsule_payload(
                agent_memory,
                session_id=session_id,
                user_message="",
            )
            memory_capsule_cache[session_id] = cached
        return cached, memory_context_meta, capsule_cache_hit
    memory_context, memory_context_meta = _build_memory_capsule_payload(
        agent_memory,
        session_id=session_id,
        user_message=user_message,
    )
    return memory_context, memory_context_meta, capsule_cache_hit


def _memory_envelope_details(meta: dict[str, str]) -> dict[str, str]:
    return {
        "envelope_truncated": str(
            meta.get("memory_envelope_truncated", "false") or "false"
        ).lower(),
        "envelope_reasons": str(
            meta.get("memory_envelope_truncation_reasons", "") or ""
        ),
        "envelope_limit_chars": str(meta.get("memory_envelope_limit_chars", "") or ""),
    }


def _emit_memory_build_events(
    *,
    emit_memory_event: MemoryEventEmitter,
    session_id: str,
    conversation_id: str,
    thread_id: str,
    attach_id: str,
    run_id: str,
    request_id: str,
    memory_strategy: str,
    memory_dynamic_retrieval_enabled: bool,
    capsule_cache_hit: bool,
    memory_context: str,
    memory_context_meta: dict[str, str],
    memory_retrieval_context: str,
    memory_retrieval_meta: dict[str, str],
) -> None:
    emit_memory_event(
        session_id=session_id,
        event_type="memory.context.built",
        conversation_id=conversation_id or None,
        thread_id=thread_id or None,
        attach_id=attach_id or None,
        payload={
            "run_id": run_id,
            "request_id": request_id,
            "strategy": memory_strategy,
            "cache_hit": str(capsule_cache_hit).lower(),
            "capsule_chars": str(len(memory_context)),
            "capsule_fingerprint": _text_fingerprint(memory_context),
            **_memory_envelope_details(memory_context_meta),
        },
    )
    if not memory_dynamic_retrieval_enabled:
        return
    emit_memory_event(
        session_id=session_id,
        event_type="memory.retrieval.built",
        conversation_id=conversation_id or None,
        thread_id=thread_id or None,
        attach_id=attach_id or None,
        payload={
            "run_id": run_id,
            "request_id": request_id,
            "strategy": memory_strategy,
            "enabled": str(memory_dynamic_retrieval_enabled).lower(),
            "retrieval_chars": str(len(memory_retrieval_context)),
            "retrieval_fingerprint": _text_fingerprint(memory_retrieval_context),
            **_memory_envelope_details(memory_retrieval_meta),
        },
    )


def _emit_knowledge_graph_event(
    *,
    emit_memory_event: MemoryEventEmitter,
    session_id: str,
    event_type: str,
    conversation_id: str,
    thread_id: str,
    attach_id: str,
    payload: dict[str, str],
) -> None:
    emit_memory_event(
        session_id=session_id,
        event_type=event_type,
        conversation_id=conversation_id or None,
        thread_id=thread_id or None,
        attach_id=attach_id or None,
        payload=payload,
    )


def build_turn_context(
    *,
    history: list[Message],
    agent_id: str,
    agent_memory: Any,
    logger: logging.Logger,
    emit_memory_event: MemoryEventEmitter,
    session_id: str,
    run_id: str,
    request_id: str,
    channel: str,
    target: str,
    user_message: str,
    conversation_id: str,
    thread_id: str,
    attach_id: str,
    memory_capsule_strategy: str,
    memory_capsule_cache: dict[str, str],
    memory_dynamic_retrieval_enabled: bool,
    knowledge_graphs: Any | None = None,
) -> TurnContext:
    turn_context = TurnContext(
        history=history,
        prior_transcript_available=history_has_prior_transcript(history),
        memory_strategy=memory_capsule_strategy,
    )
    if memory_capsule_strategy != MEMORY_CAPSULE_STRATEGY_OFF:
        try:
            _populate_memory_context(
                turn_context=turn_context,
                agent_memory=agent_memory,
                session_id=session_id,
                user_message=user_message,
                memory_strategy=memory_capsule_strategy,
                memory_capsule_cache=memory_capsule_cache,
                memory_dynamic_retrieval_enabled=memory_dynamic_retrieval_enabled,
            )
            _emit_memory_build_events_for_turn(
                turn_context=turn_context,
                emit_memory_event=emit_memory_event,
                session_id=session_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                attach_id=attach_id,
                run_id=run_id,
                request_id=request_id,
                memory_strategy=memory_capsule_strategy,
                memory_dynamic_retrieval_enabled=memory_dynamic_retrieval_enabled,
            )
        except Exception as exc:
            _record_memory_context_failure(
                turn_context=turn_context,
                emit_memory_event=emit_memory_event,
                logger=logger,
                agent_id=agent_id,
                exc=exc,
                session_id=session_id,
                conversation_id=conversation_id or None,
                thread_id=thread_id or None,
                attach_id=attach_id or None,
                run_id=run_id,
                request_id=request_id,
                memory_capsule_strategy=memory_capsule_strategy,
            )

    _populate_knowledge_graph_context(
        turn_context=turn_context,
        knowledge_graphs=knowledge_graphs,
        logger=logger,
        emit_memory_event=emit_memory_event,
        session_id=session_id,
        conversation_id=conversation_id,
        thread_id=thread_id,
        attach_id=attach_id,
        run_id=run_id,
        request_id=request_id,
        user_message=user_message,
    )

    _finalize_turn_context(
        turn_context=turn_context,
        channel=channel,
        target=target,
        session_id=session_id,
        memory_capsule_strategy=memory_capsule_strategy,
        agent_id=agent_id,
        agent_memory=agent_memory,
        logger=logger,
        user_message=user_message,
        memory_evidence_enabled=memory_dynamic_retrieval_enabled,
        knowledge_evidence_enabled=knowledge_graphs is not None,
    )

    return turn_context


def _finalize_turn_context(
    *,
    turn_context: TurnContext,
    channel: str,
    target: str,
    session_id: str,
    memory_capsule_strategy: str,
    agent_id: str,
    agent_memory: Any,
    logger: logging.Logger,
    user_message: str,
    memory_evidence_enabled: bool,
    knowledge_evidence_enabled: bool,
) -> None:
    _attach_memory_capsule_to_history(
        turn_context=turn_context,
        channel=channel,
        target=target,
        session_id=session_id,
    )
    if memory_capsule_strategy != MEMORY_CAPSULE_STRATEGY_OFF:
        _maybe_apply_contextctl_call_site(
            turn_context=turn_context,
            agent_id=agent_id,
            agent_memory=agent_memory,
            logger=logger,
            session_id=session_id,
            user_message=user_message,
        )
    _apply_shared_evidence_context(
        turn_context=turn_context,
        memory_evidence_enabled=(
            memory_evidence_enabled
            and memory_capsule_strategy != MEMORY_CAPSULE_STRATEGY_OFF
        ),
        knowledge_evidence_enabled=knowledge_evidence_enabled,
        channel=channel,
        target=target,
        session_id=session_id,
    )


def _populate_memory_context(
    agent_memory: Any,
    *,
    turn_context: TurnContext,
    session_id: str,
    user_message: str,
    memory_strategy: str,
    memory_capsule_cache: dict[str, str],
    memory_dynamic_retrieval_enabled: bool,
) -> None:
    (
        turn_context.memory_context,
        turn_context.memory_context_meta,
        turn_context.capsule_cache_hit,
    ) = _build_cached_memory_context(
        agent_memory,
        session_id=session_id,
        user_message=user_message,
        memory_strategy=memory_strategy,
        memory_capsule_cache=memory_capsule_cache,
    )
    if memory_dynamic_retrieval_enabled:
        selection = _build_dynamic_retrieval_evidence(
            agent_memory,
            session_id=session_id,
            user_message=user_message,
        )
        if selection is None:
            (
                turn_context.memory_retrieval_context,
                turn_context.memory_retrieval_meta,
            ) = _build_dynamic_retrieval_context(
                agent_memory,
                session_id=session_id,
                user_message=user_message,
            )
            return
        items, omissions = _map_memory_evidence(selection)
        turn_context.evidence_items += items
        turn_context.evidence_source_omissions += omissions
        turn_context.memory_retrieval_meta = selection.metadata_dict()
        turn_context.memory_evidence_typed = True


def _populate_knowledge_graph_context(
    *,
    turn_context: TurnContext,
    knowledge_graphs: Any | None,
    logger: logging.Logger,
    emit_memory_event: MemoryEventEmitter,
    session_id: str,
    conversation_id: str,
    thread_id: str,
    attach_id: str,
    run_id: str,
    request_id: str,
    user_message: str,
) -> None:
    if knowledge_graphs is None:
        return
    payload_base = {
        "run_id": run_id,
        "request_id": request_id,
        "layer": LAYER_THIRD_BRAIN,
    }
    source_payload = _knowledge_graph_source_payload(knowledge_graphs)
    _emit_knowledge_graph_event(
        emit_memory_event=emit_memory_event,
        session_id=session_id,
        event_type=EVENT_SOURCE_RESOLVED,
        conversation_id=conversation_id,
        thread_id=thread_id,
        attach_id=attach_id,
        payload={**payload_base, **source_payload},
    )
    _emit_knowledge_graph_event(
        emit_memory_event=emit_memory_event,
        session_id=session_id,
        event_type=EVENT_QUERY_STARTED,
        conversation_id=conversation_id,
        thread_id=thread_id,
        attach_id=attach_id,
        payload=payload_base,
    )
    try:
        results, turn_context.knowledge_graph_meta = _query_knowledge_graph_results(
            knowledge_graphs,
            user_message=user_message,
        )
        items, omissions = _map_knowledge_evidence(results)
        turn_context.evidence_items += items
        turn_context.evidence_source_omissions += omissions
        turn_context.knowledge_evidence_typed = True
    except Exception as exc:
        facts = _error_facts(exc)
        logger.warning(
            "knowledge graph context build failed session_id=%s error=%s",
            session_id,
            exc,
        )
        turn_context.knowledge_graph_meta = {
            "knowledge_graph_context_error_code": facts["error_code"],
            "knowledge_graph_context_reason_code": facts["reason_code"],
        }
        _emit_knowledge_graph_event(
            emit_memory_event=emit_memory_event,
            session_id=session_id,
            event_type=EVENT_QUERY_FAILED,
            conversation_id=conversation_id,
            thread_id=thread_id,
            attach_id=attach_id,
            payload={**payload_base, **facts},
        )
        return
    if turn_context.knowledge_graph_meta.get("knowledge_graph_degraded") == "true":
        _emit_knowledge_graph_event(
            emit_memory_event=emit_memory_event,
            session_id=session_id,
            event_type=EVENT_QUERY_DEGRADED,
            conversation_id=conversation_id,
            thread_id=thread_id,
            attach_id=attach_id,
            payload={**payload_base, **turn_context.knowledge_graph_meta},
        )
    _emit_knowledge_graph_event(
        emit_memory_event=emit_memory_event,
        session_id=session_id,
        event_type=EVENT_QUERY_COMPLETED,
        conversation_id=conversation_id,
        thread_id=thread_id,
        attach_id=attach_id,
        payload={**payload_base, **turn_context.knowledge_graph_meta},
    )


def _emit_memory_build_events_for_turn(
    *,
    turn_context: TurnContext,
    emit_memory_event: MemoryEventEmitter,
    session_id: str,
    conversation_id: str,
    thread_id: str,
    attach_id: str,
    run_id: str,
    request_id: str,
    memory_strategy: str,
    memory_dynamic_retrieval_enabled: bool,
) -> None:
    _emit_memory_build_events(
        emit_memory_event=emit_memory_event,
        session_id=session_id,
        conversation_id=conversation_id,
        thread_id=thread_id,
        attach_id=attach_id,
        run_id=run_id,
        request_id=request_id,
        memory_strategy=memory_strategy,
        memory_dynamic_retrieval_enabled=memory_dynamic_retrieval_enabled,
        capsule_cache_hit=turn_context.capsule_cache_hit,
        memory_context=turn_context.memory_context,
        memory_context_meta=turn_context.memory_context_meta,
        memory_retrieval_context=turn_context.memory_retrieval_context,
        memory_retrieval_meta=turn_context.memory_retrieval_meta,
    )


def _record_memory_context_failure(
    agent_id: str,
    session_id: str,
    *,
    turn_context: TurnContext,
    emit_memory_event: MemoryEventEmitter,
    logger: logging.Logger,
    exc: Exception,
    conversation_id: str | None,
    thread_id: str | None,
    attach_id: str | None,
    run_id: str,
    request_id: str,
    memory_capsule_strategy: str,
) -> None:
    error_facts = memory_error_facts(
        exc,
        fallback_code=MEMORY_CONTEXT_BUILD_FAILED_CODE,
        fallback_reason=MEMORY_CONTEXT_BUILD_FAILED_REASON,
    )
    logger.warning(
        "agent memory context build failed agent_id=%s session_id=%s strategy=%s reason=%s",
        agent_id,
        session_id,
        memory_capsule_strategy,
        error_facts["reason_code"],
    )
    emit_memory_event(
        session_id=session_id,
        event_type="memory.context.failed",
        conversation_id=conversation_id,
        thread_id=thread_id,
        attach_id=attach_id,
        payload={
            "run_id": run_id,
            "request_id": request_id,
            "strategy": memory_capsule_strategy,
            "error": MEMORY_CONTEXT_BUILD_FAILED_REASON,
            **error_facts,
        },
    )
    turn_context.memory_context_meta = {
        "memory_context_error_code": error_facts["error_code"],
        "memory_context_reason_code": error_facts["reason_code"],
    }


def _apply_shared_evidence_context(
    *,
    turn_context: TurnContext,
    memory_evidence_enabled: bool,
    knowledge_evidence_enabled: bool,
    channel: str,
    target: str,
    session_id: str,
) -> None:
    if not memory_evidence_enabled and not knowledge_evidence_enabled:
        return
    _pack_evidence_context(turn_context)
    _attach_evidence_context_to_history(
        turn_context=turn_context,
        channel=channel,
        target=target,
        session_id=session_id,
    )


def _attach_memory_capsule_to_history(
    *,
    turn_context: TurnContext,
    channel: str,
    target: str,
    session_id: str,
) -> None:
    if turn_context.memory_context:
        turn_context.history = _inject_memory_context(
            history=turn_context.history,
            channel=channel,
            target=target,
            session_id=session_id,
            memory_context=turn_context.memory_context,
        )


def _attach_evidence_context_to_history(
    *,
    turn_context: TurnContext,
    channel: str,
    target: str,
    session_id: str,
) -> None:
    if turn_context.memory_retrieval_context:
        turn_context.history = _append_memory_retrieval_context(
            history=turn_context.history,
            channel=channel,
            target=target,
            session_id=session_id,
            memory_context=turn_context.memory_retrieval_context,
        )
    if turn_context.knowledge_graph_context:
        turn_context.history = _append_knowledge_graph_context(
            history=turn_context.history,
            channel=channel,
            target=target,
            session_id=session_id,
            graph_context=turn_context.knowledge_graph_context,
        )


def _maybe_apply_contextctl_call_site(
    *,
    turn_context: TurnContext,
    agent_id: str,
    agent_memory: Any,
    logger: logging.Logger,
    session_id: str,
    user_message: str,
) -> None:
    """Maybe apply contextctl call site helper."""
    from openminion.services.config import resolve_services_env
    from openminion.services.context.adapter import ContextCtlGatewayAdapter
    from openminion.services.context.constants import (
        CONTEXTCTL_GATEWAY_ENABLED_ENV,
    )

    env_config = resolve_services_env()
    if not env_config.get_bool(CONTEXTCTL_GATEWAY_ENABLED_ENV, False):
        return

    try:
        adapter = ContextCtlGatewayAdapter.from_env(
            agent_id=agent_id,
            memory_client=agent_memory,
            logger=logger,
        )
        if not adapter.is_enabled:
            return
        ctxctl_messages = adapter.build_ctxctl_messages(
            session_id=session_id,
            agent_id=agent_id,
            query=user_message,
        )
        if ctxctl_messages is None:
            # build_ctxctl_messages returned None — adapter detected a
            return
        # `select_history` is typed `list[object]` to bridge generic adapter
        # call-sites; gateway's `turn_context.history` is `list[Message]`. The
        # adapter does not mutate element shape, so we cast on both sides.
        turn_context.history = cast(
            "list[Any]",
            adapter.select_history(
                history=cast("list[object]", turn_context.history),
                session_id=session_id,
                agent_id=agent_id,
                query=user_message,
                contextctl_messages=ctxctl_messages,
            ),
        )
    except Exception as exc:
        logger.warning(
            "contextctl gateway call-site failed agent_id=%s session_id=%s error=%s; "
            "falling back to existing history",
            agent_id,
            session_id,
            exc,
        )
