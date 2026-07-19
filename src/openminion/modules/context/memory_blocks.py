"""Sophiagraph memory-block consumption for OpenMinion context packs."""

from __future__ import annotations

from typing import Any

from openminion.base.time import utc_now_iso

from .constants import MEMORY_BLOCK_CONTEXT_DEFAULT_LIMIT
from .schemas import ContextSegment, MemoryBlockSegmentRef

_ACTIVE_BLOCK_MODES = frozenset({"read_only", "pinned"})
_V1_BLOCK_CLASSES = ("agent_identity", "active_mission", "session_pin")


def build_memory_block_segments_for_context(
    *,
    enabled: bool,
    memory_block_store: Any,
    memory_client: Any,
    session_id: str,
    agent_id: str,
    memory_token_budget: int,
) -> tuple[list[ContextSegment], dict[str, Any]]:
    if not enabled:
        return [], {}
    store = memory_block_store
    if store is None and callable(getattr(memory_client, "list_memory_blocks", None)):
        store = memory_client
    if store is None:
        return [], {
            "total_available": 0,
            "dropped": 0,
            "missing_reason": "no_memory_block_store",
        }
    return build_memory_block_segments(
        store=store,
        session_id=session_id,
        agent_id=agent_id,
        memory_token_budget=memory_token_budget,
    )


def build_memory_block_segments(
    *,
    store: Any,
    session_id: str,
    agent_id: str,
    memory_token_budget: int,
) -> tuple[list[ContextSegment], dict[str, Any]]:
    """Return budgeted memory-block segments from the Sophiagraph query owner."""

    if store is None or memory_token_budget <= 0:
        return [], _stats(total_available=0, dropped=0, missing_reason="disabled")
    blocks = _list_candidate_blocks(
        store,
        session_id=session_id,
        agent_id=agent_id,
    )
    active_blocks = [
        block for block in blocks if str(block.mode) in _ACTIVE_BLOCK_MODES
    ]
    inactive_count = max(0, len(blocks) - len(active_blocks))
    if not active_blocks:
        return [], _stats(
            total_available=len(blocks),
            dropped=inactive_count,
            missing_reason="no_active_memory_blocks",
        )

    from sophiagraph.contracts.errors import MemoryBlocksBudgetHardFloorViolatedError

    try:
        package = _assemble_block_context(
            active_blocks,
            ceiling_tokens=max(1, int(memory_token_budget)),
            now_iso=utc_now_iso(),
            session_id=session_id,
        )
    except MemoryBlocksBudgetHardFloorViolatedError:
        return [], _stats(
            total_available=len(blocks),
            dropped=len(active_blocks) + inactive_count,
            budget_exceeded=True,
            missing_reason="memory_blocks_hard_floor_violated",
        )
    segments = [_segment_from_rendered(block) for block in package.rendered]
    return segments, _stats(
        total_available=len(blocks),
        dropped=inactive_count + len(package.dropped_block_ids),
        truncated=len(package.truncated_block_ids),
        stale=len(package.stale_block_ids),
        budget_exceeded=bool(package.budget_exceeded),
    )


def _list_candidate_blocks(
    store: Any,
    *,
    session_id: str,
    agent_id: str,
) -> list[Any]:
    list_blocks = getattr(store, "list_memory_blocks", None)
    if not callable(list_blocks):
        return []
    namespaces = _candidate_namespaces(session_id=session_id, agent_id=agent_id)
    return list(
        list_blocks(
            namespaces=namespaces,
            class_names=list(_V1_BLOCK_CLASSES),
            include_stale=True,
            limit=MEMORY_BLOCK_CONTEXT_DEFAULT_LIMIT,
        )
    )


def _candidate_namespaces(*, session_id: str, agent_id: str) -> list[Any]:
    namespace_cls = _memory_namespace_cls()
    namespaces = []
    if agent_id:
        namespaces.append(namespace_cls(agent_id=agent_id))
    if session_id:
        namespaces.append(namespace_cls(session_id=session_id))
    if agent_id and session_id:
        namespaces.append(namespace_cls(agent_id=agent_id, session_id=session_id))
    return namespaces


def _segment_from_rendered(rendered: Any) -> ContextSegment:
    namespace = _namespace_id(getattr(rendered, "owner_namespace", None))
    ref = MemoryBlockSegmentRef(
        block_id=str(rendered.block_id),
        class_name=str(rendered.class_name),
        mode=str(rendered.mode),
        namespace_id=str(namespace or ""),
        provenance_ref=f"memory-block:{rendered.block_id}",
        stale=bool(getattr(rendered, "is_stale", False)),
    )
    return ContextSegment(
        id=f"memory-block:{rendered.block_id}",
        bucket="memory",
        role="system",
        content=str(rendered.content),
        token_estimate=max(0, int(rendered.token_cost)),
        refs=[f"memory-block:{rendered.block_id}"],
        pinned=str(rendered.mode) == "pinned",
        content_hash="",
        cache_invalidation_refs=[f"memory-block:{rendered.block_id}"],
        metadata={"memory_block": ref.model_dump(mode="json", exclude_none=True)},
    )


def _namespace_id(namespace: Any) -> str:
    as_dict = getattr(namespace, "as_dict", None)
    if not callable(as_dict):
        return ""
    parts = as_dict()
    return "|".join(f"{key}:{parts[key]}" for key in sorted(parts))


def _stats(
    *,
    total_available: int,
    dropped: int,
    truncated: int = 0,
    stale: int = 0,
    budget_exceeded: bool = False,
    missing_reason: str = "",
) -> dict[str, Any]:
    return {
        "total_available": max(0, int(total_available)),
        "dropped": max(0, int(dropped)),
        "truncated": max(0, int(truncated)),
        "stale": max(0, int(stale)),
        "budget_exceeded": bool(budget_exceeded),
        "missing_reason": missing_reason,
    }


def _memory_namespace_cls() -> Any:
    from sophiagraph.models import MemoryNamespace

    return MemoryNamespace


def _assemble_block_context(blocks: list[Any], **kwargs: Any) -> Any:
    from sophiagraph.query.blocks import assemble_block_context

    return assemble_block_context(blocks, **kwargs)


__all__ = [
    "MemoryBlockSegmentRef",
    "build_memory_block_segments",
    "build_memory_block_segments_for_context",
]
