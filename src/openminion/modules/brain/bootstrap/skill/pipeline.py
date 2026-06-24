from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Any

from openminion.base.config import SKILL_SELECTION_AUTO, skill_value_to_list
from openminion.modules.brain.config import (
    DIRECT_PROMPT_BUDGET_TOKENS as _DIRECT_PROMPT_BUDGET_TOKENS,
    MAX_SKILLS_PER_SESSION as _MAX_SKILLS_PER_SESSION,
)

from . import selection as _skill_selection
from ..budget import infer_context_budget_tier
from .selection import (
    SkillPipelineResult,
    SkillRef,
    SkillSubsetSelection,
    _CONTEXT_BUDGET_TIER_MEDIUM,
    _SKILL_SELECTION_REASON_DIRECT_NAMED,
    _SKILL_SELECTION_REASON_DIRECT_SINGLE_CATALOG,
    _catalog_refs,
    _normalized_skill_ids,
    _normalized_text_list,
    _select_skills_with_llm,
    _select_skills_with_retrieval,
    _summarize_model_name,
    _emit_skill_selection_event,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...diagnostics.events import CanonicalEventLogger
    from ...runner import BrainRunner
    from ...schemas import WorkingState

_build_skill_selection_context = _skill_selection._build_skill_selection_context
_catalog_by_id = _skill_selection._catalog_by_id
_catalog_prompt_line = _skill_selection._catalog_prompt_line
_catalog_ref = _skill_selection._catalog_ref
_emit_shortlist = _skill_selection._emit_shortlist
_retrieval_shortlist_limit = _skill_selection._retrieval_shortlist_limit
_SKILL_SELECT_PROMPT = _skill_selection._SKILL_SELECT_PROMPT
_SKILL_SELECTION_REASON_LLM = _skill_selection._SKILL_SELECTION_REASON_LLM


@dataclass(frozen=True)
class SkillCatalogState:
    effective_catalog: list[dict[str, Any]]
    effective_refs: list[SkillRef]
    sources: dict[str, str]
    capacity: int
    projected_selection_mode: str
    auto_enabled: bool


def build_skill_session_snapshot(
    runner: "BrainRunner",
    *,
    state: "WorkingState",
    purpose: str,
) -> dict[str, Any]:
    default_snapshot = {
        "has_prior_transcript": False,
        "recent_turn_count": 0,
        "open_task_count": 0,
        "last_turn_had_tool_activity": False,
        "has_session_summary": False,
    }
    session_api = getattr(runner, "session_api", None)
    if session_api is None or not hasattr(session_api, "get_slice"):
        return default_snapshot
    try:
        raw_slice = session_api.get_slice(
            session_id=state.session_id,
            purpose=purpose,
            limits={"max_turns": 12, "max_tool_events": 1},
        )
    except Exception:
        return default_snapshot

    recent_turns = _slice_list(raw_slice, "recent_turns")
    open_tasks = _slice_list(raw_slice, "open_tasks")
    recent_tool_events = _slice_list(raw_slice, "recent_tool_events")
    summary_short = _slice_value(raw_slice, "summary_short", "")
    return {
        "has_prior_transcript": bool(recent_turns),
        "recent_turn_count": len(recent_turns),
        "open_task_count": len(open_tasks),
        "last_turn_had_tool_activity": bool(recent_tool_events),
        "has_session_summary": bool(str(summary_short or "").strip()),
    }


def resolve_skill_pipeline(
    runner: "BrainRunner",
    *,
    intent: str,
    purpose: str,
    state: "WorkingState",
    logger: "CanonicalEventLogger",
) -> SkillPipelineResult:
    normalized_intent = str(intent or "").strip()
    if not normalized_intent:
        return SkillPipelineResult(
            selected_refs=[],
            selection_mode="none",
            context_budget=_CONTEXT_BUDGET_TIER_MEDIUM,
            capacity=0,
            effective_count=0,
            selection_reason="no_intent",
            routed_intent="",
            shortlisted_ids=[],
        )

    session_snapshot = build_skill_session_snapshot(
        runner,
        state=state,
        purpose=purpose,
    )
    catalog = _load_catalog(
        skill_api=getattr(runner, "skill_api", None),
        agent_id=str(getattr(state, "agent_id", "") or "").strip(),
    )
    catalog_state = describe_skill_catalog(
        profile=getattr(runner, "profile", None),
        state=state,
        catalog=catalog,
    )
    effective_catalog = catalog_state.effective_catalog
    context_budget = _infer_context_budget(
        intent=normalized_intent,
        session_snapshot=session_snapshot,
        effective_skill_count=len(effective_catalog),
    )
    if not effective_catalog:
        _emit_skill_selection_event(
            logger=logger,
            state=state,
            model="",
            selection_mode="none",
            selected_refs=[],
            effective_count=0,
            capacity=0,
            routed_intent=normalized_intent,
            fail_closed_reason=None,
            context_budget=context_budget,
            shortlisted_ids=[],
        )
        return SkillPipelineResult(
            selected_refs=[],
            selection_mode="none",
            context_budget=context_budget,
            capacity=0,
            effective_count=0,
            selection_reason="no_catalog",
            routed_intent=normalized_intent,
            shortlisted_ids=[],
        )

    capacity = catalog_state.capacity
    named_refs = _resolve_unique_named_skill_refs(
        intent=normalized_intent,
        catalog=effective_catalog,
    )
    if named_refs:
        _emit_shortlist(
            logger=logger,
            state=state,
            shortlisted_ids=[ref.skill_id for ref in named_refs],
            strategy="direct-named",
            query=normalized_intent,
        )
        _emit_skill_selection_event(
            logger=logger,
            state=state,
            model="",
            selection_mode="direct",
            selected_refs=named_refs,
            effective_count=len(effective_catalog),
            capacity=capacity,
            routed_intent=normalized_intent,
            fail_closed_reason=None,
            context_budget=context_budget,
            shortlisted_ids=[ref.skill_id for ref in named_refs],
        )
        return SkillPipelineResult(
            selected_refs=named_refs,
            selection_mode="direct",
            context_budget=context_budget,
            capacity=capacity,
            effective_count=len(effective_catalog),
            selection_reason=_SKILL_SELECTION_REASON_DIRECT_NAMED,
            routed_intent=normalized_intent,
            shortlisted_ids=[ref.skill_id for ref in named_refs],
        )

    if _can_use_direct_catalog(
        catalog=effective_catalog,
        capacity=capacity,
        auto_enabled=catalog_state.auto_enabled,
    ):
        selected_refs = _catalog_refs(effective_catalog, source="direct")
        _emit_shortlist(
            logger=logger,
            state=state,
            shortlisted_ids=[ref.skill_id for ref in selected_refs],
            strategy="direct",
            query=normalized_intent,
        )
        _emit_skill_selection_event(
            logger=logger,
            state=state,
            model="",
            selection_mode="direct",
            selected_refs=selected_refs,
            effective_count=len(effective_catalog),
            capacity=capacity,
            routed_intent=normalized_intent,
            fail_closed_reason=None,
            context_budget=context_budget,
            shortlisted_ids=[ref.skill_id for ref in selected_refs],
        )
        return SkillPipelineResult(
            selected_refs=selected_refs,
            selection_mode="direct",
            context_budget=context_budget,
            capacity=capacity,
            effective_count=len(effective_catalog),
            selection_reason=_SKILL_SELECTION_REASON_DIRECT_SINGLE_CATALOG,
            routed_intent=normalized_intent,
            shortlisted_ids=[ref.skill_id for ref in selected_refs],
        )

    if len(effective_catalog) > capacity * 2:
        retrieval_result = _select_skills_with_retrieval(
            runner,
            intent=normalized_intent,
            purpose=purpose,
            state=state,
            catalog=effective_catalog,
            capacity=capacity,
            logger=logger,
        )
        if retrieval_result is not None:
            retrieval_result = SkillPipelineResult(
                selected_refs=retrieval_result.selected_refs,
                selection_mode=retrieval_result.selection_mode,
                context_budget=context_budget,
                capacity=capacity,
                effective_count=len(effective_catalog),
                selection_reason=retrieval_result.selection_reason,
                fail_closed_reason=retrieval_result.fail_closed_reason,
                routed_intent=retrieval_result.routed_intent or normalized_intent,
                shortlisted_ids=retrieval_result.shortlisted_ids,
                llm_pick_details=retrieval_result.llm_pick_details,
            )
            if retrieval_result.selected_refs:
                _emit_skill_selection_event(
                    logger=logger,
                    state=state,
                    model=_summarize_model_name(runner),
                    selection_mode=retrieval_result.selection_mode,
                    selected_refs=retrieval_result.selected_refs,
                    effective_count=len(effective_catalog),
                    capacity=capacity,
                    routed_intent=retrieval_result.routed_intent or normalized_intent,
                    fail_closed_reason=retrieval_result.fail_closed_reason,
                    context_budget=context_budget,
                    shortlisted_ids=retrieval_result.shortlisted_ids or [],
                    llm_pick_details=retrieval_result.llm_pick_details,
                )
                return retrieval_result

    llm_result = _select_skills_with_llm(
        runner,
        intent=normalized_intent,
        state=state,
        catalog=effective_catalog,
        capacity=capacity,
        logger=logger,
        strategy="llm",
    )
    llm_result = SkillPipelineResult(
        selected_refs=llm_result.selected_refs,
        selection_mode=llm_result.selection_mode,
        context_budget=context_budget,
        capacity=capacity,
        effective_count=len(effective_catalog),
        selection_reason=llm_result.selection_reason,
        fail_closed_reason=llm_result.fail_closed_reason,
        routed_intent=llm_result.routed_intent or normalized_intent,
        shortlisted_ids=llm_result.shortlisted_ids,
        llm_pick_details=llm_result.llm_pick_details,
    )
    _emit_skill_selection_event(
        logger=logger,
        state=state,
        model=_summarize_model_name(runner),
        selection_mode=llm_result.selection_mode,
        selected_refs=llm_result.selected_refs,
        effective_count=len(effective_catalog),
        capacity=capacity,
        routed_intent=llm_result.routed_intent or normalized_intent,
        fail_closed_reason=llm_result.fail_closed_reason,
        context_budget=context_budget,
        shortlisted_ids=llm_result.shortlisted_ids or [],
        llm_pick_details=llm_result.llm_pick_details,
    )
    return llm_result


def _effective_catalog(
    *,
    profile: Any,
    state: "WorkingState",
    catalog: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str], bool]:
    configured_auto, configured_skills = skill_value_to_list(
        getattr(profile, "skill", None)
    )
    configured_catalog = [
        str(item).strip()
        for item in list(getattr(profile, "skill_catalog", []) or [])
        if str(item).strip()
    ]
    session_loaded = _normalized_skill_ids(
        getattr(state, "session_skill_loaded", []) or []
    )
    session_unloaded = set(
        item.lower()
        for item in _normalized_skill_ids(
            getattr(state, "session_skill_unloaded", []) or []
        )
    )
    session_auto = (
        str(getattr(state, "skill_selection_mode", "") or "").strip().lower()
        == SKILL_SELECTION_AUTO
    )
    default_auto = not configured_auto and not configured_skills

    catalog_by_id = _catalog_by_id(catalog)
    if configured_catalog:
        allowed = {item.lower() for item in configured_catalog}
        catalog_by_id = {
            skill_id: entry
            for skill_id, entry in catalog_by_id.items()
            if skill_id.lower() in allowed
        }

    if session_auto or configured_auto or default_auto:
        base_ids = list(catalog_by_id.keys())
    else:
        base_ids = [
            skill_id for skill_id in configured_skills if skill_id in catalog_by_id
        ]

    effective_ids: list[str] = []
    seen: set[str] = set()
    sources: dict[str, str] = {}
    for skill_id in base_ids:
        lowered = skill_id.lower()
        if lowered in session_unloaded or lowered in seen:
            continue
        seen.add(lowered)
        effective_ids.append(skill_id)
        sources[skill_id] = (
            "config"
            if not (session_auto or configured_auto or default_auto)
            else "catalog"
        )
    for skill_id in session_loaded:
        lowered = skill_id.lower()
        if lowered in seen or lowered in session_unloaded:
            continue
        if skill_id not in catalog_by_id:
            continue
        seen.add(lowered)
        effective_ids.append(skill_id)
        sources[skill_id] = "session"

    return (
        [
            catalog_by_id[skill_id]
            for skill_id in effective_ids
            if skill_id in catalog_by_id
        ],
        sources,
        bool(session_auto or configured_auto or default_auto),
    )


def describe_skill_catalog(
    *,
    profile: Any,
    state: "WorkingState",
    catalog: list[dict[str, Any]],
) -> SkillCatalogState:
    effective_catalog, sources, auto_enabled = _effective_catalog(
        profile=profile,
        state=state,
        catalog=catalog,
    )
    capacity = min(
        _direct_capacity(effective_catalog), _configured_skill_capacity(profile)
    )
    if not effective_catalog:
        projected_selection_mode = "none"
    elif _can_use_direct_catalog(
        catalog=effective_catalog,
        capacity=capacity,
        auto_enabled=auto_enabled,
    ):
        projected_selection_mode = "direct"
    elif len(effective_catalog) > capacity * 2:
        projected_selection_mode = "retrieval-select"
    else:
        projected_selection_mode = "llm-select"
    return SkillCatalogState(
        effective_catalog=effective_catalog,
        effective_refs=_catalog_refs(effective_catalog, source="effective"),
        sources=sources,
        capacity=capacity,
        projected_selection_mode=projected_selection_mode,
        auto_enabled=auto_enabled,
    )


def apply_skill_selection_to_state(
    *,
    state: "WorkingState",
    result: SkillPipelineResult,
) -> None:
    primary = result.primary_ref
    selected_skill_ids = [ref.skill_id for ref in result.selected_refs]
    state.active_skill_id = primary.skill_id if primary is not None else None
    state.active_skill_version_hash = (
        primary.version_hash if primary is not None else None
    )
    state.active_skill_ids = list(selected_skill_ids)
    state.resolved_skill_ids = list(selected_skill_ids)
    state.resolved_skill_versions = {
        ref.skill_id: ref.version_hash
        for ref in result.selected_refs
        if str(ref.version_hash or "").strip()
    }


def _direct_capacity(catalog: list[dict[str, Any]]) -> int:
    if not catalog:
        return 0
    average_tokens = sum(_catalog_entry_tokens(entry) for entry in catalog) / max(
        1, len(catalog)
    )
    return max(1, int(_DIRECT_PROMPT_BUDGET_TOKENS / max(12.0, average_tokens)))


def _resolve_unique_named_skill_refs(
    *, intent: str, catalog: list[dict[str, Any]]
) -> list[SkillRef]:
    if not catalog:
        return []
    intent_lower = str(intent or "").strip().lower()
    if not intent_lower:
        return []
    matched_ids: list[str] = []
    seen: set[str] = set()
    for entry in catalog:
        skill_id = str(entry.get("id", "") or "").strip()
        if not skill_id:
            continue
        lowered = skill_id.lower()
        if lowered in seen:
            continue
        if _intent_matches_skill_identity(intent_lower=intent_lower, entry=entry):
            seen.add(lowered)
            matched_ids.append(skill_id)
    if len(matched_ids) != 1:
        return []
    catalog_by_id = _catalog_by_id(catalog)
    selected = catalog_by_id.get(matched_ids[0])
    if selected is None:
        return []
    return [_catalog_ref(selected, source="direct-named")]


def _intent_matches_skill_identity(*, intent_lower: str, entry: dict[str, Any]) -> bool:
    candidates: list[str] = []
    for raw in (
        entry.get("id", ""),
        entry.get("name", ""),
        entry.get("canonical_name", ""),
        entry.get("display_name", ""),
    ):
        text = str(raw or "").strip()
        if not text:
            continue
        candidates.append(text)
        compact = _normalize_skill_identity_phrase(text)
        if compact and compact.lower() != text.lower():
            candidates.append(compact)
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if _is_explicit_named_skill_request(
            intent_lower=intent_lower,
            candidate_lower=normalized,
        ):
            return True
    return False


def _normalize_skill_identity_phrase(value: str) -> str:
    normalized = re.sub(r"[_\-]+", " ", str(value or "").strip().lower())
    return " ".join(part for part in normalized.split() if part)


def _is_explicit_named_skill_request(
    *, intent_lower: str, candidate_lower: str
) -> bool:
    if not intent_lower or not candidate_lower:
        return False
    normalized_intent = _normalize_skill_identity_phrase(intent_lower)
    normalized_candidate = _normalize_skill_identity_phrase(candidate_lower)
    if not normalized_intent or not normalized_candidate:
        return False
    if normalized_intent == normalized_candidate:
        return True
    explicit_patterns = (
        rf"(?<![a-z0-9])use\s+(?:the\s+)?(?:exact\s+)?skill\s+{re.escape(normalized_candidate)}(?![a-z0-9])",
        rf"(?<![a-z0-9])use\s+(?:the\s+)?{re.escape(normalized_candidate)}\s+skill(?![a-z0-9])",
        rf"(?<![a-z0-9])named\s+skill\s+{re.escape(normalized_candidate)}(?![a-z0-9])",
    )
    return any(re.search(pattern, normalized_intent) for pattern in explicit_patterns)


def _configured_skill_capacity(profile: Any) -> int:
    raw_capacity = getattr(profile, "max_skills_per_session", _MAX_SKILLS_PER_SESSION)
    try:
        return max(1, int(raw_capacity))
    except (TypeError, ValueError):
        return _MAX_SKILLS_PER_SESSION


def _catalog_entry_tokens(entry: dict[str, Any]) -> int:
    parts = [
        str(entry.get("id", "") or "").strip(),
        str(entry.get("name", "") or "").strip(),
        str(entry.get("canonical_name", "") or "").strip(),
        str(entry.get("display_name", "") or "").strip(),
        str(entry.get("short_description", "") or "").strip(),
        str(entry.get("one_liner", "") or "").strip(),
        ", ".join(_normalized_text_list(entry.get("tags"))[:4]),
        ", ".join(_normalized_text_list(entry.get("tools"))[:4]),
    ]
    words = len(" ".join(part for part in parts if part).split())
    return max(12, words + 6)


def _load_catalog(*, skill_api: Any, agent_id: str) -> list[dict[str, Any]]:
    if skill_api is None or not hasattr(skill_api, "catalog_summaries"):
        return []
    try:
        raw_catalog = skill_api.catalog_summaries(agent_id=agent_id)
    except Exception:
        return []
    if not isinstance(raw_catalog, list):
        return []
    catalog: list[dict[str, Any]] = []
    for item in raw_catalog:
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("id", "") or "").strip()
        if not skill_id:
            continue
        catalog.append(
            {
                "id": skill_id,
                "name": str(item.get("name", "") or "").strip(),
                "display_name": str(item.get("display_name", "") or "").strip(),
                "canonical_name": str(item.get("canonical_name", "") or "").strip(),
                "short_description": str(
                    item.get("short_description", "") or ""
                ).strip(),
                "one_liner": str(item.get("one_liner", "") or "").strip(),
                "version_hash": str(item.get("version_hash", "") or "").strip(),
                "tags": _normalized_text_list(item.get("tags")),
                "tools": _normalized_text_list(item.get("tools")),
                "reference_hints": _normalized_text_list(item.get("reference_hints")),
            }
        )
    return catalog


def _can_use_direct_catalog(
    *,
    catalog: list[dict[str, Any]],
    capacity: int,
    auto_enabled: bool,
) -> bool:
    if not catalog or len(catalog) > capacity:
        return False
    if not auto_enabled:
        return True
    return len(catalog) == 1


def _infer_context_budget(
    *,
    intent: str,
    session_snapshot: dict[str, Any],
    effective_skill_count: int,
) -> str:
    return infer_context_budget_tier(
        intent=intent,
        session_snapshot=session_snapshot,
        effective_skill_count=effective_skill_count,
    )


def _slice_value(raw_slice: Any, key: str, default: Any) -> Any:
    if isinstance(raw_slice, dict):
        return raw_slice.get(key, default)
    return getattr(raw_slice, key, default)


def _slice_list(raw_slice: Any, key: str) -> list[Any]:
    value = _slice_value(raw_slice, key, [])
    if isinstance(value, list):
        return value
    return []


__all__ = [
    "SkillCatalogState",
    "SkillPipelineResult",
    "SkillRef",
    "SkillSubsetSelection",
    "apply_skill_selection_to_state",
    "build_skill_session_snapshot",
    "describe_skill_catalog",
    "resolve_skill_pipeline",
    "_configured_skill_capacity",
]
