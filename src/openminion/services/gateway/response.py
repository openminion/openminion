import json
from typing import Any

from openminion.base.types import Message
from openminion.modules.telemetry.usage import RunStats


def envelope_truncation_payload(
    *,
    memory_context_meta: dict[str, str],
    memory_retrieval_meta: dict[str, str],
) -> tuple[bool, str]:
    envelope_reasons: set[str] = set()
    for meta in (memory_context_meta, memory_retrieval_meta):
        raw_reasons = str(
            meta.get("memory_envelope_truncation_reasons", "") or ""
        ).strip()
        if not raw_reasons:
            continue
        for reason in raw_reasons.split(","):
            normalized_reason = reason.strip()
            if normalized_reason:
                envelope_reasons.add(normalized_reason)

    envelope_truncated = (
        str(memory_context_meta.get("memory_envelope_truncated", "false")).lower()
        == "true"
        or str(memory_retrieval_meta.get("memory_envelope_truncated", "false")).lower()
        == "true"
    )
    return envelope_truncated, ",".join(sorted(envelope_reasons))


def build_outbound_message(
    *,
    response: Any,
    session_id: str,
    run_id: str,
    request_id: str,
    conversation_id: str,
    thread_id: str,
    attach_id: str,
    memory_context_meta: dict[str, str],
    memory_retrieval_meta: dict[str, str],
) -> Message:
    envelope_truncated, envelope_reasons = envelope_truncation_payload(
        memory_context_meta=memory_context_meta,
        memory_retrieval_meta=memory_retrieval_meta,
    )
    outbound = Message(
        channel=response.channel,
        target=response.target,
        body=response.text,
        metadata={
            **response.metadata,
            "session_id": session_id,
            "run_id": run_id,
            "request_id": request_id,
            **({"conversation_id": conversation_id} if conversation_id else {}),
            **({"thread_id": thread_id} if thread_id else {}),
            **({"attach_id": attach_id} if attach_id else {}),
        },
        stats=RunStats.from_mapping(getattr(response, "metadata", None)),
    )
    for meta in (memory_context_meta, memory_retrieval_meta):
        for key, value in meta.items():
            outbound.metadata.setdefault(str(key), str(value))
    if memory_context_meta:
        outbound.metadata.setdefault(
            "memory_capsule_envelope_limit_chars",
            str(memory_context_meta.get("memory_envelope_limit_chars", "") or ""),
        )
    if memory_retrieval_meta:
        outbound.metadata.setdefault(
            "memory_retrieval_envelope_limit_chars",
            str(memory_retrieval_meta.get("memory_envelope_limit_chars", "") or ""),
        )
    if outbound.stats is not None and outbound.stats.has_any_data:
        outbound.metadata["run_stats_json"] = json.dumps(
            outbound.stats.as_payload(),
            sort_keys=True,
        )
    outbound.metadata["memory_envelope_truncated"] = str(envelope_truncated).lower()
    outbound.metadata["memory_envelope_truncation_reasons"] = envelope_reasons
    return outbound
