import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MemoryRuntimeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    """MRIR-02: Bounded runtime snapshot of memory state for introspection responses.

    Provides counts, summary status, highlights, and degraded markers without
    dumping raw active-state payloads into prompt or user output.
    """
    session_records: int = Field(default=0, ge=0)
    agent_records: int = Field(default=0, ge=0)
    global_records: int = Field(default=0, ge=0)
    candidate_count: int = Field(default=0, ge=0)
    total_records: int = Field(default=0, ge=0)
    memory_available: bool = Field(default=False)
    vector_search_available: bool = Field(default=False)
    degraded: bool = Field(default=False)
    degraded_reason: str | None = Field(default=None)
    recent_highlights: list[str] = Field(default_factory=list)
    snapshot_timestamp: str = Field(default="")
    scope_filter: str | None = Field(default=None)


class RetrievalStatsSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    """MRIR-03: Bounded retrieval stats for introspection responses."""
    last_strategy: str = Field(default="none")
    last_hit_count: int = Field(default=0, ge=0)
    last_query: str | None = Field(default=None)
    last_latency_ms: float | None = Field(default=None)
    retrieve_available: bool = Field(default=False)
    last_error: str | None = Field(default=None)
    error_count_recent: int = Field(default=0, ge=0)
    total_retrievals_session: int = Field(default=0, ge=0)
    avg_hits_per_query: float = Field(default=0.0, ge=0.0)
    snapshot_timestamp: str = Field(default="")


class RuntimeIntrospectionDigest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    """MRIR-04: Optional introspection digest section for prompt packs.

    This is attached only when introspection intent is active.
    Provides bounded memory and retrieval stats with token cap.
    """
    introspection_active: bool = Field(default=False)
    memory: MemoryRuntimeSnapshot | None = Field(default=None)
    retrieval: RetrievalStatsSnapshot | None = Field(default=None)
    summary_text: str = Field(default="")
    estimated_tokens: int = Field(default=0, ge=0)
    capped: bool = Field(default=False)
    cap_reason: str | None = Field(default=None)


def _utc_now_isoformat() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_records(records: list[Any]) -> list[Any]:
    result = []
    for record in records:
        try:
            result.append(dataclasses.asdict(record))
        except TypeError:
            try:
                result.append(record.model_dump())
            except (AttributeError, TypeError):
                result.append({"repr": repr(record)})
    return result


def _list_store_records(store: Any, scopes: list[str], *, limit: int) -> list[Any]:
    from openminion.modules.memory.storage.base import ListQueryOptions

    return list(store.list(ListQueryOptions(scopes=scopes, limit=limit)))


def _record_highlight(record: Any) -> str | None:
    title = getattr(record, "title", None)
    if title:
        return str(title)
    record_id = getattr(record, "id", None)
    if record_id:
        return str(record_id)
    return None


def export_to_files(
    store: Any,
    *,
    session_id: str,
    agent_id: str,
    output_dir: Any,
) -> Any:
    """MV2-09: Export all scoped memory records to JSON files in output_dir."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    for scope_key, filename in [
        (f"session:{session_id}", "session_records.json"),
        (f"agent:{agent_id}", "agent_records.json"),
        ("global:system", "global_records.json"),
    ]:
        records: list = []
        if store is not None and hasattr(store, "list"):
            try:
                records = _list_store_records(store, [scope_key], limit=1000)
            except Exception:
                pass
        (out / filename).write_text(
            json.dumps(_serialize_records(records), indent=2, default=str)
        )

    return out


def build_memory_snapshot(
    store: Any,
    session_id: str,
    agent_id: str,
    max_highlights: int = 5,
) -> MemoryRuntimeSnapshot:
    """MRIR-02: Build a bounded memory runtime snapshot from a memory store."""
    snapshot = MemoryRuntimeSnapshot(
        snapshot_timestamp=_utc_now_isoformat(),
        scope_filter=f"session:{session_id}",
    )

    if store is None:
        snapshot.degraded = True
        snapshot.degraded_reason = "memory_store_unavailable"
        return snapshot

    try:
        if hasattr(store, "list"):
            try:
                session_records = len(
                    _list_store_records(store, [f"session:{session_id}"], limit=1000)
                )
                agent_records = len(
                    _list_store_records(store, [f"agent:{agent_id}"], limit=1000)
                )
                global_records = len(_list_store_records(store, ["global"], limit=1000))
                snapshot.session_records = session_records
                snapshot.agent_records = agent_records
                snapshot.global_records = global_records
                snapshot.total_records = (
                    session_records + agent_records + global_records
                )
            except Exception:
                pass

        if hasattr(store, "_vector_adapter") and store._vector_adapter is not None:
            snapshot.vector_search_available = True

        if hasattr(store, "candidate_list"):
            try:
                from openminion.modules.memory.storage.base import CandidateListOptions

                cand_opts = CandidateListOptions(limit=1000)
                candidates = store.candidate_list(cand_opts)
                snapshot.candidate_count = len(candidates)
            except Exception:
                pass

        snapshot.memory_available = True
        snapshot.degraded = False

        try:
            if hasattr(store, "list"):
                recent_records = _list_store_records(
                    store,
                    [f"session:{session_id}", f"agent:{agent_id}"],
                    limit=max_highlights,
                )
                for record in recent_records:
                    highlight = _record_highlight(record)
                    if highlight:
                        snapshot.recent_highlights.append(highlight)
        except Exception:
            pass

    except Exception as exc:
        snapshot.degraded = True
        snapshot.degraded_reason = f"memory_snapshot_error: {str(exc)[:100]}"
        snapshot.memory_available = False

    return snapshot


def build_retrieval_stats(
    retrieve_svc: Any,
    session_id: str,
) -> RetrievalStatsSnapshot:
    """MRIR-03: Build bounded retrieval stats for introspection responses."""
    snapshot = RetrievalStatsSnapshot(
        snapshot_timestamp=_utc_now_isoformat(),
    )

    if retrieve_svc is None:
        snapshot.retrieve_available = False
        return snapshot

    try:
        snapshot.retrieve_available = True

        if hasattr(retrieve_svc, "_last_strategy"):
            snapshot.last_strategy = str(retrieve_svc._last_strategy)
        if hasattr(retrieve_svc, "_last_hit_count"):
            snapshot.last_hit_count = int(retrieve_svc._last_hit_count)
        if hasattr(retrieve_svc, "_last_query"):
            snapshot.last_query = str(retrieve_svc._last_query)[:100]
        if hasattr(retrieve_svc, "_last_latency_ms"):
            snapshot.last_latency_ms = float(retrieve_svc._last_latency_ms)

        if hasattr(retrieve_svc, "_error_count"):
            snapshot.error_count_recent = int(retrieve_svc._error_count)
        if hasattr(retrieve_svc, "_last_error"):
            last_err = retrieve_svc._last_error
            if last_err:
                snapshot.last_error = str(last_err)[:200]

        if hasattr(retrieve_svc, "_session_stats"):
            stats = retrieve_svc._session_stats
            if isinstance(stats, dict) and session_id in stats:
                sess_stats = stats[session_id]
                if isinstance(sess_stats, dict):
                    snapshot.total_retrievals_session = sess_stats.get("count", 0)
                    hits = sess_stats.get("total_hits", 0)
                    count = sess_stats.get("count", 0)
                    if count > 0:
                        snapshot.avg_hits_per_query = round(hits / count, 2)

    except Exception as exc:
        snapshot.retrieve_available = False
        snapshot.last_error = f"retrieval_stats_error: {str(exc)[:100]}"

    return snapshot


def format_introspection_digest(
    memory: MemoryRuntimeSnapshot,
    retrieval: RetrievalStatsSnapshot,
    max_tokens: int = 300,
) -> RuntimeIntrospectionDigest:
    """MRIR-04: Format bounded introspection digest for prompt inclusion."""
    lines: list[str] = []

    lines.append("## Memory Status")
    if memory.degraded:
        lines.append(f"⚠️ Memory system degraded: {memory.degraded_reason or 'Unknown'}")
    elif not memory.memory_available:
        lines.append("⚠️ Memory system unavailable")
    else:
        lines.append(f"✓ Memory active: {memory.total_records} records")
        lines.append(f"  - This session: {memory.session_records}")
        lines.append(f"  - Agent scope: {memory.agent_records}")
        lines.append(f"  - Global: {memory.global_records}")
        lines.append(f"  - Candidates: {memory.candidate_count}")
        if memory.vector_search_available:
            lines.append("✓ Vector search enabled")
        if memory.recent_highlights:
            lines.append("Recent highlights:")
            for hl in memory.recent_highlights[:3]:
                lines.append(f"  - {hl[:60]}{'...' if len(hl) > 60 else ''}")

    lines.append("")
    lines.append("## Retrieval Status")
    if not retrieval.retrieve_available:
        lines.append("⚠️ Retrieval system unavailable")
    else:
        lines.append(f"✓ Retrieval active (strategy: {retrieval.last_strategy})")
        lines.append(f"  - Last query hits: {retrieval.last_hit_count}")
        if retrieval.last_latency_ms is not None:
            lines.append(f"  - Last latency: {retrieval.last_latency_ms:.0f}ms")
        lines.append(f"  - Session retrievals: {retrieval.total_retrievals_session}")
        if retrieval.error_count_recent > 0:
            lines.append(f"⚠️ Recent errors: {retrieval.error_count_recent}")

    summary_text = "\n".join(lines)

    estimated = len(summary_text) // 4

    capped = estimated > max_tokens
    cap_reason = None
    if capped:
        max_chars = max_tokens * 4
        summary_text = (
            summary_text[:max_chars]
            + "\n[Additional memory details omitted for token efficiency]"
        )
        cap_reason = f"Token cap exceeded ({estimated} > {max_tokens})"
        estimated = len(summary_text) // 4

    return RuntimeIntrospectionDigest(
        introspection_active=True,
        memory=memory,
        retrieval=retrieval,
        summary_text=summary_text,
        estimated_tokens=estimated,
        capped=capped,
        cap_reason=cap_reason,
    )
