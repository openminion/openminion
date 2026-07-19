from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from typing import Any, Optional

from openminion.base.types import Message
from openminion.modules.telemetry.usage import RunStats


def _human_participant_id(
    *,
    session: Any,
    channel: str,
    target: str,
    inbound_metadata: dict[str, str],
) -> str:
    explicit = str(
        inbound_metadata.get("participant_id")
        or inbound_metadata.get("human_id")
        or inbound_metadata.get("user")
        or ""
    ).strip()
    if explicit:
        return explicit
    metadata = getattr(session, "metadata", {}) or {}
    local_human_id = str(metadata.get("local_human_id", "") or "").strip()
    if local_human_id:
        return local_human_id
    return str(target or channel or "human").strip()


def _progress_payload_mapping(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return dict(payload)
    model_dump = getattr(payload, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped
    return {}


def _progress_usage_stats(payload: Any) -> tuple[RunStats | None, bool]:
    mapped = _progress_payload_mapping(payload)
    if not mapped:
        return None, False
    stats = RunStats.from_mapping(mapped)
    if stats is None:
        return None, bool(mapped.get("token_usage_estimated", False))
    return stats, bool(mapped.get("token_usage_estimated", False))


def _attach_progress_usage_metadata(
    metadata: dict[str, str],
    stats: RunStats | None,
) -> None:
    if stats is None:
        return
    existing = RunStats.from_mapping(metadata)
    if existing is not None and (
        existing.input_tokens > 0 or existing.output_tokens > 0
    ):
        return
    total_tokens = max(0, int(stats.input_tokens) + int(stats.output_tokens))
    if total_tokens <= 0:
        return
    metadata["total_input_tokens_used"] = str(int(stats.input_tokens))
    metadata["total_output_tokens_used"] = str(int(stats.output_tokens))
    metadata["total_tokens_used"] = str(total_tokens)


@dataclass
class _RoutingResult:
    """Resolved routing state for a single gateway turn."""

    early_return: Optional[Message]
    normalized_request_id: str = ""
    normalized_inbound_metadata: dict[str, str] = _dc_field(default_factory=dict)
    conversation_id: str = ""
    thread_id: str = ""
    attach_id: str = ""
    session: Any = None
    lifecycle: Any = None
    routing_action: str = ""
    routing_reason: str = ""
    resume_requested: bool = False
    reset_requested: bool = False
    auto_resume_inferred: bool = False
    explicit_conversation: bool = False
    explicit_thread: bool = False


def _response_is_pae_idle_tick_noop(response: Any) -> bool:
    metadata = getattr(response, "metadata", None) or {}
    if not isinstance(metadata, dict):
        return False
    return str(metadata.get("pae_idle_tick_noop", "")).strip().lower() == "true"
