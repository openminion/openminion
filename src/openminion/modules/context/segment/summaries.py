"""Summary segment assembly helpers."""

from __future__ import annotations

from typing import Any

from ..constants import CONTEXT_PURPOSE_DECIDE
from ..schemas import BuildPackRequest, SessionSlice
from .summary_continuation import (
    continuation_payload as _continuation_payload,
    render_continuation_payload,
)


def _append_summary_segment(
    runtime: Any,
    *,
    segment_id: str,
    text: str,
    remaining_tokens: int,
    cap_tokens: int | None = None,
    pinned: bool = False,
    refs: list[str] | None = None,
) -> tuple[int, bool]:
    available = min(remaining_tokens, cap_tokens or remaining_tokens)
    if available <= 0 or not text.strip():
        return remaining_tokens, False
    content = runtime.fit_section(segment_id, text, available)
    if not content.strip():
        return remaining_tokens, False
    segment = runtime.make(
        segment_id,
        "summaries",
        content,
        pinned=pinned,
        refs=refs,
    )
    runtime.segments.append(segment)
    return max(0, remaining_tokens - segment.token_estimate), True



def append_summary_segments(
    runtime: Any,
    *,
    request: BuildPackRequest,
    session_slice: SessionSlice,
    seed_text: str | None,
    rolling_enabled: bool,
    compression_enabled: bool,
    compressctl: Any | None,
) -> None:
    summary_items = 0
    remaining_tokens = runtime.budgets.summary_tokens
    continuation_event, continuation_payload = _continuation_payload(session_slice)
    if continuation_payload:
        continuation_text = render_continuation_payload(continuation_payload)
        remaining_tokens, added = _append_summary_segment(
            runtime,
            segment_id="continuation",
            text=f"[SESSION CONTINUATION]\n{continuation_text}",
            remaining_tokens=remaining_tokens,
            pinned=True,
            refs=[
                str(ref)
                for ref in (
                    continuation_event.get("packet_id"),
                    continuation_event.get("source_session_id"),
                )
                if ref
            ],
        )
        summary_items += int(added)

    if compression_enabled and seed_text and seed_text.strip():
        remaining_tokens, added = _append_summary_segment(
            runtime,
            segment_id="seed_block",
            text=f"[CONTEXT SEED]\n{seed_text}",
            remaining_tokens=remaining_tokens,
        )
        summary_items += int(added)

    summary_items += _append_session_summaries(
        runtime,
        request=request,
        session_slice=session_slice,
        remaining_tokens=remaining_tokens,
        continuation_payload=continuation_payload,
        rolling_enabled=rolling_enabled,
        compression_enabled=compression_enabled,
        compressctl=compressctl,
    )



def _append_session_summaries(
    runtime: Any,
    *,
    request: BuildPackRequest,
    session_slice: SessionSlice,
    remaining_tokens: int,
    continuation_payload: dict[str, Any],
    rolling_enabled: bool,
    compression_enabled: bool,
    compressctl: Any | None,
) -> int:
    summary_items = 0
    summary_text = session_slice.summary_short if rolling_enabled else ""
    active_state = (
        session_slice.active_state
        if isinstance(session_slice.active_state, dict)
        else {}
    )
    session_work_summary_text = str(
        active_state.get("session_work_summary", "") or ""
    ).strip()
    continuation_summary = str(
        continuation_payload.get("session_work_summary") or ""
    ).strip()
    if continuation_summary and continuation_summary == session_work_summary_text:
        session_work_summary_text = ""
    compression_ref = _compression_ref(
        request=request,
        session_slice=session_slice,
        compression_enabled=compression_enabled,
        compressctl=compressctl,
    )
    if (
        rolling_enabled
        and request.purpose in {"judge", "validate"}
        and session_slice.summary_long
    ):
        summary_text = f"{summary_text}\n\n{session_slice.summary_long}"
    summary_cap_tokens = remaining_tokens
    session_work_summary_cap_tokens = remaining_tokens
    if summary_text.strip() and session_work_summary_text:
        session_work_summary_cap_tokens = max(1, remaining_tokens // 2)
        summary_cap_tokens = max(1, remaining_tokens - session_work_summary_cap_tokens)
    remaining_tokens, added = _append_summary_texts(
        runtime,
        summary_text=summary_text,
        summary_cap_tokens=summary_cap_tokens,
        session_work_summary_text=session_work_summary_text,
        session_work_summary_cap_tokens=session_work_summary_cap_tokens,
        compression_ref=compression_ref,
        remaining_tokens=remaining_tokens,
    )
    summary_items += added
    remaining_tokens, archive_added = _append_archive_refs(
        runtime,
        session_slice=session_slice,
        remaining_tokens=remaining_tokens,
    )
    summary_items += int(archive_added)
    runtime.bucket_stats["summaries"] = {"total_available": summary_items, "dropped": 0}
    _append_conversation_summary(
        runtime,
        request=request,
        session_slice=session_slice,
        rolling_enabled=rolling_enabled,
    )
    return summary_items


def _compression_ref(
    *,
    request: BuildPackRequest,
    session_slice: SessionSlice,
    compression_enabled: bool,
    compressctl: Any | None,
) -> str | None:
    if not compression_enabled or compressctl is None:
        return None
    try:
        snapshot = compressctl.get_snapshot(
            session_id=request.session_id,
            agent_id=request.agent_id,
            mode_name=request.mode_name,
        )
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
        return None
    if not snapshot:
        return None
    return session_slice.seed_bundle_id or session_slice.checkpoint_id


def _append_summary_texts(
    runtime: Any,
    *,
    summary_text: str,
    summary_cap_tokens: int,
    session_work_summary_text: str,
    session_work_summary_cap_tokens: int,
    compression_ref: str | None,
    remaining_tokens: int,
) -> tuple[int, int]:
    added_count = 0
    if summary_text.strip():
        remaining_tokens, added = _append_summary_segment(
            runtime,
            segment_id="summary",
            text=f"[SESSION SUMMARY]\n{summary_text}",
            remaining_tokens=remaining_tokens,
            cap_tokens=summary_cap_tokens,
        )
        added_count += int(added)
        if compression_ref and added:
            remaining_tokens, ref_added = _append_summary_segment(
                runtime,
                segment_id="compression_ref",
                text=f"[COMPRESSION REFERENCE]\ncheckpoint: {compression_ref}",
                remaining_tokens=remaining_tokens,
            )
            added_count += int(ref_added)
    if session_work_summary_text:
        remaining_tokens, added = _append_summary_segment(
            runtime,
            segment_id="session_work_summary",
            text=f"[SESSION WORK SUMMARY]\n{session_work_summary_text}",
            remaining_tokens=remaining_tokens,
            cap_tokens=session_work_summary_cap_tokens,
        )
        added_count += int(added)
    return remaining_tokens, added_count


def _append_archive_refs(
    runtime: Any,
    *,
    session_slice: SessionSlice,
    remaining_tokens: int,
) -> tuple[int, bool]:
    if not session_slice.archive_refs:
        return remaining_tokens, False
    archive_text = "Compaction archive refs (full transcript chunks):\n" + "\n".join(
        f"- {ref}" for ref in session_slice.archive_refs[:5]
    )
    return _append_summary_segment(
        runtime,
        segment_id="archive_refs",
        text=archive_text,
        remaining_tokens=remaining_tokens,
        cap_tokens=max(1, runtime.budgets.summary_tokens // 4),
    )


def _append_conversation_summary(
    runtime: Any,
    *,
    request: BuildPackRequest,
    session_slice: SessionSlice,
    rolling_enabled: bool,
) -> None:
    conversation_summary_items = 0
    conversation_summary_text = (
        str(session_slice.conversation_summary or "").strip()
        if rolling_enabled and request.purpose == CONTEXT_PURPOSE_DECIDE
        else ""
    )
    conversation_summary_block = runtime.fit_section(
        "conversation_summary",
        conversation_summary_text,
        runtime.budgets.conversation_summary_tokens,
    )
    if conversation_summary_block.strip():
        runtime.segments.append(
            runtime.make(
                "conversation_summary",
                "conversation_summary",
                "[CONVERSATION SUMMARY]\n" + conversation_summary_block,
            )
        )
        conversation_summary_items = 1
    runtime.bucket_stats["conversation_summary"] = {
        "total_available": conversation_summary_items,
        "dropped": 0,
    }


__all__ = ["append_summary_segments"]
