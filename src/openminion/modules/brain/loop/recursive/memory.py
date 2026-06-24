import hashlib
from datetime import datetime, timezone
from typing import Any

from .schemas import (
    EvidenceRef,
    MemoryWriteIntent,
    RetrievedContext,
    RetrievalQuality,
    RetrievalStrategy,
    TaskState,
    TickOutput,
    WMState,
    iso_now,
)
from .payloads import _dedupe_keep_order
from openminion.base.constants import STATE_KEY_ACTIVE


def refresh_working_memory(
    self, session_id: str, agent_id: str, reason: str
) -> WMState:
    wm_state = self._load_wm_state(session_id=session_id)
    session_slice = self._safe_get_slice(session_id=session_id)

    turns = session_slice.get("recent_turns", [])
    open_questions = []
    assistant_decisions = []
    objective = wm_state.objective
    for item in turns:
        role = str(item.get("role", ""))
        text = str(item.get("text", item.get("content", ""))).strip()
        if not text:
            continue
        if role == "user" and not objective:
            objective = text
        if role == "user" and text.endswith("?"):
            open_questions.append(text)
        if role == "assistant":
            assistant_decisions.append(text)

    recent_tool_events = session_slice.get("recent_tool_events", [])
    tool_summaries: list[str] = []
    for event in recent_tool_events:
        name = str(event.get("tool_name", "tool")).strip()
        excerpt = str(event.get("excerpt", "")).strip()
        summary = f"{name}: {excerpt}" if excerpt else name
        if summary:
            tool_summaries.append(summary)

    state_payload = session_slice.get(STATE_KEY_ACTIVE)
    current_step = wm_state.current_step
    step_cursor = wm_state.step_cursor
    if isinstance(state_payload, dict):
        current_step = str(state_payload.get("phase", current_step) or current_step)
        cursor_value = state_payload.get("cursor")
        if cursor_value is not None:
            step_cursor = str(cursor_value)

    refreshed = WMState(
        wm_version=wm_state.wm_version + 1,
        objective=objective or wm_state.objective,
        constraints=_dedupe_keep_order(wm_state.constraints),
        current_step=current_step,
        step_cursor=step_cursor,
        key_decisions=_dedupe_keep_order(
            (wm_state.key_decisions + assistant_decisions)[
                -self.config.wm_max_items_per_list :
            ]
        ),
        assumptions=_dedupe_keep_order(wm_state.assumptions)[
            -self.config.wm_max_items_per_list :
        ],
        open_questions=_dedupe_keep_order(
            (wm_state.open_questions + open_questions)[
                -self.config.wm_max_items_per_list :
            ]
        ),
        must_not_forget=_dedupe_keep_order(wm_state.must_not_forget)[
            -self.config.wm_max_items_per_list :
        ],
        invariants=_dedupe_keep_order(
            wm_state.invariants
            + [str(item) for item in state_payload.get("invariants", [])]
        )
        if isinstance(state_payload, dict)
        else wm_state.invariants,
        tool_summaries=_dedupe_keep_order(
            (wm_state.tool_summaries + tool_summaries)[
                -self.config.wm_max_tool_summaries :
            ]
        ),
        updated_at=iso_now(),
    )

    self._save_wm_state(
        session_id=session_id, wm_state=refreshed, task_state=None, reason=reason
    )
    self._append_event(
        session_id=session_id,
        agent_id=agent_id,
        event_type="wm.updated",
        payload={"wm_version": refreshed.wm_version, "reason": reason},
    )
    return refreshed


def _load_wm_state(self, *, session_id: str) -> WMState:
    latest = self._sessctl.get_latest_working_state(session_id)
    if not latest:
        return WMState()
    inline = latest.get("state_inline")
    if isinstance(inline, dict):
        if isinstance(inline.get("wm_state"), dict):
            wm_state = self._validate_wm_state(inline["wm_state"])
            if wm_state is not None:
                return wm_state
        candidate = {key: inline[key] for key in WMState.model_fields if key in inline}
        wm_state = self._validate_wm_state(candidate)
        if wm_state is not None:
            return wm_state
    return WMState()


def _validate_wm_state(self, payload: dict[str, Any]) -> WMState | None:
    if not payload:
        return None
    try:
        return WMState.model_validate(payload)
    except Exception:  # noqa: BLE001
        return None


def _save_wm_state(
    self,
    *,
    session_id: str,
    wm_state: WMState,
    task_state: TaskState | None,
    reason: str,
) -> int:
    latest = self._sessctl.get_latest_working_state(session_id)
    state_inline: dict[str, Any] = {}
    if latest and isinstance(latest.get("state_inline"), dict):
        state_inline = dict(latest["state_inline"])
    state_inline["wm_state"] = wm_state.model_dump(mode="json")
    if task_state is not None:
        state_inline["task_state"] = task_state.model_dump(mode="json")
    state_inline["wm_last_reason"] = reason
    version = self._sessctl.put_working_state(session_id, state_inline=state_inline)
    return int(version)


def _safe_get_slice(self, *, session_id: str) -> dict[str, Any]:
    try:
        return self._sessctl.get_slice(
            session_id=session_id,
            purpose="summarize",
            limits={
                "max_turns": 12,
                "max_tool_events": 8,
                "include_open_tasks": True,
                "include_active_state": True,
            },
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        events = self._sessctl.list_events(session_id, limit=40)
    except Exception:  # noqa: BLE001
        events = []
    return {
        "session_id": session_id,
        "recent_turns": [],
        "open_tasks": [],
        STATE_KEY_ACTIVE: {},
        "recent_tool_events": events[-8:],
    }


def _merge_wm(
    self,
    *,
    wm_state: WMState,
    wm_patch: dict[str, Any],
    query: str,
    answer: str,
    max_items: int,
    max_tool_summaries: int,
) -> WMState:
    patch = dict(wm_patch or {})
    objective = str(patch.get("objective", wm_state.objective or query)).strip()
    constraints = _dedupe_keep_order(
        wm_state.constraints + [str(item) for item in patch.get("constraints", [])]
    )
    decisions = _dedupe_keep_order(
        wm_state.key_decisions
        + [str(item) for item in patch.get("key_decisions", [])]
        + ([answer] if answer else [])
    )
    assumptions = _dedupe_keep_order(
        wm_state.assumptions + [str(item) for item in patch.get("assumptions", [])]
    )
    open_questions = _dedupe_keep_order(
        wm_state.open_questions
        + [str(item) for item in patch.get("open_questions", [])]
    )
    must_not_forget = _dedupe_keep_order(
        wm_state.must_not_forget
        + [str(item) for item in patch.get("must_not_forget", [])]
    )
    invariants = _dedupe_keep_order(
        wm_state.invariants + [str(item) for item in patch.get("invariants", [])]
    )
    tool_summaries = _dedupe_keep_order(
        wm_state.tool_summaries
        + [str(item) for item in patch.get("tool_summaries", [])]
    )

    return WMState(
        wm_version=wm_state.wm_version + 1,
        objective=objective,
        constraints=constraints[-max_items:],
        current_step=(
            str(patch.get("current_step"))
            if patch.get("current_step") is not None
            else wm_state.current_step
        ),
        step_cursor=(
            str(patch.get("step_cursor"))
            if patch.get("step_cursor") is not None
            else wm_state.step_cursor
        ),
        key_decisions=decisions[-max_items:],
        assumptions=assumptions[-max_items:],
        open_questions=open_questions[-max_items:],
        must_not_forget=must_not_forget[-max_items:],
        invariants=invariants[-max_items:],
        tool_summaries=tool_summaries[-max_tool_summaries:],
        updated_at=iso_now(),
    )


def _estimate_citation_coverage(self, *, answer: str, evidence_count: int) -> float:
    text = (answer or "").strip()
    if not text:
        return 0.0
    claims = max(1, sum(text.count(ch) for ch in [".", "!", "?"]))
    coverage = float(evidence_count) / float(claims)
    return max(0.0, min(1.0, coverage))


def _normalize_evidence_refs(
    self, raw_refs: list[Any], source_hint: str
) -> list[EvidenceRef]:
    out: list[EvidenceRef] = []
    for raw in raw_refs:
        if isinstance(raw, dict):
            ref_id = str(raw.get("ref_id", raw.get("ref", ""))).strip()
            if not ref_id:
                continue
            ref_type = (
                str(raw.get("ref_type", self._infer_ref_type(ref_id))).strip().lower()
            )
            note = str(raw.get("note")) if raw.get("note") is not None else None
            out.append(
                EvidenceRef(
                    ref_type=self._normalize_ref_type(ref_type),
                    ref_id=ref_id,
                    source=self._normalize_source(source_hint),
                    note=note,
                )
            )
            continue

        ref_id = str(raw).strip()
        if not ref_id:
            continue
        out.append(
            EvidenceRef(
                ref_type=self._infer_ref_type(ref_id),
                ref_id=ref_id,
                source=self._normalize_source(source_hint),
                note=None,
            )
        )
    return out


def _merge_evidence_refs(
    self, left: list[EvidenceRef], right: list[EvidenceRef]
) -> list[EvidenceRef]:
    out: list[EvidenceRef] = []
    seen: set[str] = set()
    for item in list(left) + list(right):
        key = f"{item.ref_type}:{item.ref_id}"
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _infer_ref_type(self, ref_id: str) -> str:
    raw = str(ref_id)
    if raw.startswith("artifact://"):
        return "artifact"
    if raw.startswith("ev-") or raw.startswith("event:"):
        return "event"
    if raw.startswith("mem") or raw.startswith("cand-"):
        return "memory"
    if raw.startswith("skill:"):
        return "skill"
    return "other"


def _normalize_ref_type(self, value: str) -> str:
    normalized = str(value or "other").strip().lower()
    if normalized in {"artifact", "event", "memory", "session", "skill", "other"}:
        return normalized
    return "other"


def _write_episode_note(
    self,
    *,
    session_id: str,
    agent_id: str,
    tick_index: int,
    query: str,
    output: TickOutput,
    retrieved: list[RetrievedContext],
    llm_status: str,
    retrieval_quality: RetrievalQuality,
    retrieval_strategy: RetrievalStrategy,
    compression_meta: dict[str, Any],
) -> str | None:
    if self._artifactctl is None:
        return None

    note = self._build_episode_markdown(
        tick_index=tick_index,
        query=query,
        output=output,
        retrieved=retrieved,
        llm_status=llm_status,
        retrieval_quality=retrieval_quality,
        retrieval_strategy=retrieval_strategy,
        compression_meta=compression_meta,
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(note.encode("utf-8")).hexdigest()[:12]
    name = f"episodes/{session_id}/{timestamp}_{digest}.md"
    meta = {
        "tags": ["episode", "rlm"],
        "tick_index": tick_index,
        "query": query,
        "llm_status": llm_status,
        "retrieval_quality": retrieval_quality,
        "retrieval_strategy": retrieval_strategy,
        "retrieval_refs": [item.ref_id for item in retrieved],
        "compression": compression_meta,
    }
    try:
        ref = self._artifactctl.ingest_bytes(
            note.encode("utf-8"),
            mime="text/markdown",
            original_name=name,
            label=f"episode:{session_id}:{tick_index}",
            meta=meta,
            session_id=session_id,
            agent_id=agent_id,
        )
    except TypeError:
        ref = self._artifactctl.ingest_bytes(
            note.encode("utf-8"),
            mime="text/markdown",
            original_name=name,
            meta=meta,
        )
    return self._artifact_ref_to_text(ref)


def _build_episode_markdown(
    self,
    *,
    tick_index: int,
    query: str,
    output: TickOutput,
    retrieved: list[RetrievedContext],
    llm_status: str,
    retrieval_quality: RetrievalQuality,
    retrieval_strategy: RetrievalStrategy,
    compression_meta: dict[str, Any],
) -> str:
    lines = [
        f"# RLM Episode Tick {tick_index}",
        "",
        "## Query",
        query,
        "",
        "## LLM Status",
        llm_status,
        "",
        "## Retrieval",
        f"- strategy: {retrieval_strategy}",
        f"- quality: {retrieval_quality}",
        "",
        "## Compression",
        f"- method: {compression_meta.get('method_id', '')}",
        f"- ratio: {compression_meta.get('ratio', 1.0)}",
        "",
        "## Answer",
        output.answer or "(empty)",
        "",
        "## Episode Note",
        output.episode_note or "(none)",
        "",
        "## Retrieved Context",
    ]
    for item in retrieved:
        lines.append(
            f"- [{item.source}/{item.unit_kind}] {item.ref_id} (score={item.score:.3f})"
        )
    if not retrieved:
        lines.append("- (empty augmentation)")
    return "\n".join(lines).strip() + "\n"


def _stage_memory_candidates(
    self,
    *,
    session_id: str,
    intents: list[MemoryWriteIntent],
    fallback_evidence: list[str],
) -> list[str]:
    if self._memctl is None:
        return []
    out: list[str] = []
    for intent in intents:
        evidence = intent.evidence_refs or fallback_evidence
        try:
            record_id = self._memctl.stage_candidate(
                scope=f"session:{session_id}",
                record_type=intent.intent_type,
                title=intent.title,
                content=intent.content,
                tags=intent.tags,
                evidence_refs=evidence,
            )
        except Exception:  # noqa: BLE001
            continue
        out.append(str(record_id))
    return out


def _append_event(
    self,
    *,
    session_id: str,
    agent_id: str,
    event_type: str,
    payload: dict[str, Any],
    artifact_refs: list[str] | None = None,
    memory_refs: list[str] | None = None,
    status: str | None = None,
) -> None:
    try:
        self._sessctl.append_event(
            session_id=session_id,
            type=event_type,
            payload=payload,
            agent_id=agent_id,
            artifact_refs=artifact_refs,
            memory_refs=memory_refs,
            status=status,
        )
    except TypeError:
        self._sessctl.append_event(
            session_id=session_id,
            event_type=event_type,
            payload=payload,
            actor_type="agent",
            actor_id=agent_id,
        )
    except Exception:  # noqa: BLE001
        pass


def _normalize_source(self, value: Any) -> str:
    candidate = str(value or "em").strip().lower()
    if candidate in {"wm", "em", "sm", "skill", "session"}:
        return candidate
    return "em"


def _normalize_strategy(self, value: Any) -> RetrievalStrategy:
    candidate = str(value or "auto").strip().lower()
    if candidate in {"auto", "contextual", "raptor", "longrag_doc_group"}:
        return candidate  # type: ignore[return-value]
    return "auto"


def _normalize_unit_kind(self, value: Any) -> str:
    candidate = str(value or "unknown").strip().lower()
    if candidate in {"chunk", "doc_group", "document", "unknown"}:
        return candidate
    return "unknown"


def _normalize_raptor_level(self, value: Any) -> str:
    candidate = str(value or "none").strip().lower()
    if candidate in {"none", "internal", "leaf"}:
        return candidate
    return "none"


def _trust_score_from_source(self, source: Any) -> float:
    normalized = self._normalize_source(source)
    if normalized == "sm":
        return 1.0
    if normalized == "skill":
        return 0.9
    if normalized == "session":
        return 0.7
    return 0.6


def _to_plain(self, value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except TypeError:
            return dump()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {"value": value}
