import hashlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field
from openminion.modules.brain.config import (
    RETRIEVAL_SHORTLIST_K as _RETRIEVAL_SHORTLIST_K,
    RETRIEVAL_SHORTLIST_MAX as _RETRIEVAL_SHORTLIST_MAX,
)
from openminion.modules.brain.constants import (
    CONTEXT_BUDGET_TIER_FULL as _CONTEXT_BUDGET_TIER_FULL,
    CONTEXT_BUDGET_TIER_MEDIUM as _CONTEXT_BUDGET_TIER_MEDIUM,
    CONTEXT_BUDGET_TIER_SHORT as _CONTEXT_BUDGET_TIER_SHORT,
    SKILL_SELECTION_INVALID_SKILL_ID as _SKILL_SELECTION_INVALID_SKILL_ID,
    SKILL_SELECTION_MODEL_UNAVAILABLE as _SKILL_SELECTION_MODEL_UNAVAILABLE,
    SKILL_SELECTION_PARSE_ERROR as _SKILL_SELECTION_PARSE_ERROR,
    SKILL_SELECTION_RATE_LIMITED as _SKILL_SELECTION_RATE_LIMITED,
    SKILL_SELECTION_REASON_DIRECT as _SKILL_SELECTION_REASON_DIRECT,
    SKILL_SELECTION_REASON_DIRECT_NAMED as _SKILL_SELECTION_REASON_DIRECT_NAMED,
    SKILL_SELECTION_REASON_DIRECT_SINGLE_CATALOG as _SKILL_SELECTION_REASON_DIRECT_SINGLE_CATALOG,
    SKILL_SELECTION_REASON_LLM as _SKILL_SELECTION_REASON_LLM,
    SKILL_SELECTION_REASON_RETRIEVAL as _SKILL_SELECTION_REASON_RETRIEVAL,
    SKILL_SELECTION_TIMEOUT as _SKILL_SELECTION_TIMEOUT,
)

from ...retry import call_structured_with_retry

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...diagnostics.events import CanonicalEventLogger
    from ...runner import BrainRunner
    from ...schemas import WorkingState

_SKILL_SELECT_PROMPT = (
    "You are an internal skill selection helper. Pick the smallest useful subset "
    "of skills for the next assistant turn. Return JSON only. Use only ids from "
    "the provided catalog. If the user names a specific skill and that skill "
    "exists in the catalog by id, name, canonical_name, or display_name, select "
    "exactly that skill id. Do not substitute a different skill when a named "
    "skill exists. Return an empty list when no skill is needed or when the "
    "user names a skill that is absent from the catalog."
)


class SkillSubsetSelection(BaseModel):
    """LLM-owned final skill-selection authority — catalog-bounded, fail-closed."""

    model_config = ConfigDict(extra="ignore")

    skill_ids: list[str] = Field(default_factory=list)
    intent: str = ""


@dataclass(frozen=True)
class SkillRef:
    skill_id: str
    version_hash: str
    source: str


@dataclass(frozen=True)
class SkillPipelineResult:
    selected_refs: list[SkillRef]
    selection_mode: str
    context_budget: str
    capacity: int
    effective_count: int
    selection_reason: str
    fail_closed_reason: str | None = None
    routed_intent: str = ""
    shortlisted_ids: list[str] | None = None
    # Raw LLM/retrieval picks for post-hoc selection telemetry.
    llm_pick_details: dict[str, Any] | None = None

    @property
    def primary_ref(self) -> SkillRef | None:
        return self.selected_refs[0] if self.selected_refs else None


def _select_skills_with_retrieval(
    runner: "BrainRunner",
    *,
    intent: str,
    purpose: str,
    state: "WorkingState",
    catalog: list[dict[str, Any]],
    capacity: int,
    logger: "CanonicalEventLogger",
) -> SkillPipelineResult | None:
    retrieve_api = getattr(runner, "retrieve_api", None)
    if retrieve_api is None or not hasattr(retrieve_api, "retrieve"):
        return None
    ingest_skill = getattr(retrieve_api, "ingest_skill", None)
    if callable(ingest_skill):
        for entry in catalog:
            try:
                ingest_skill(
                    skill_id=str(entry.get("id", "") or "").strip(),
                    version_hash=str(entry.get("version_hash", "") or "").strip(),
                    source_ref=_skill_source_ref(entry),
                    meta={
                        "text": _catalog_retrieval_text(entry),
                        "title": _catalog_retrieval_title(entry),
                        "scope": "agent",
                        "tags": ["skill", "catalog"],
                        "unit_kind": "chunk",
                    },
                )
            except Exception:
                continue
    try:
        raw_items = retrieve_api.retrieve(
            query=intent,
            purpose=purpose,
            scope={
                "scope": "agent",
                "session_id": str(getattr(state, "session_id", "") or "").strip(),
                "turn_id": str(getattr(state, "trace_id", "") or "").strip(),
            },
            k=_retrieval_shortlist_limit(
                catalog_size=len(catalog),
                capacity=capacity,
            ),
            strategy="auto",
            filters={"types": ["skill"], "tags": ["skill"]},
        )
    except Exception:
        return None
    catalog_by_id = _catalog_by_id(catalog)
    shortlist_ids: list[str] = []
    seen: set[str] = set()
    for item in list(raw_items or []):
        skill_id = _skill_id_from_retrieve_item(item)
        if not skill_id:
            continue
        lowered = skill_id.lower()
        if lowered in seen or skill_id not in catalog_by_id:
            continue
        seen.add(lowered)
        shortlist_ids.append(skill_id)
    if not shortlist_ids:
        return None
    _emit_shortlist(
        logger=logger,
        state=state,
        shortlisted_ids=shortlist_ids,
        strategy="retrieval",
        query=intent,
    )
    shortlist = [catalog_by_id[skill_id] for skill_id in shortlist_ids]
    return _select_skills_with_llm(
        runner,
        intent=intent,
        state=state,
        catalog=shortlist,
        capacity=capacity,
        logger=logger,
        strategy="retrieval",
    )


def _select_skills_with_llm(
    runner: "BrainRunner",
    *,
    intent: str,
    state: "WorkingState",
    catalog: list[dict[str, Any]],
    capacity: int,
    logger: "CanonicalEventLogger",
    strategy: str,
) -> SkillPipelineResult:
    model = _summarize_model_name(runner)
    if getattr(runner, "llm_api", None) is None or not model:
        return SkillPipelineResult(
            selected_refs=[],
            selection_mode=f"{strategy}-select",
            context_budget=_CONTEXT_BUDGET_TIER_MEDIUM,
            capacity=capacity,
            effective_count=len(catalog),
            selection_reason=_SKILL_SELECTION_REASON_RETRIEVAL
            if strategy == "retrieval"
            else _SKILL_SELECTION_REASON_LLM,
            fail_closed_reason=_SKILL_SELECTION_MODEL_UNAVAILABLE,
            routed_intent=intent,
            shortlisted_ids=[],
        )

    context = _build_skill_selection_context(
        intent=intent,
        catalog=catalog,
        capacity=capacity,
    )
    token_count = _estimate_token_count(
        llm_api=runner.llm_api,
        model=model,
        context=context,
    )
    started = time.perf_counter()
    try:
        raw = call_structured_with_retry(
            runner.llm_api,
            model=model,
            purpose="skill_selection",
            context=context,
            schema=SkillSubsetSelection,
        )
    except Exception as exc:
        _emit_skill_prerouting_failure(
            logger=logger,
            state=state,
            model=model,
            token_count=token_count,
            latency_ms=_elapsed_ms_since(started),
            fail_closed_reason=_classify_failure_reason(exc),
        )
        return SkillPipelineResult(
            selected_refs=[],
            selection_mode=f"{strategy}-select",
            context_budget=_CONTEXT_BUDGET_TIER_MEDIUM,
            capacity=capacity,
            effective_count=len(catalog),
            selection_reason=_SKILL_SELECTION_REASON_RETRIEVAL
            if strategy == "retrieval"
            else _SKILL_SELECTION_REASON_LLM,
            fail_closed_reason=_classify_failure_reason(exc),
            routed_intent=intent,
            shortlisted_ids=[],
        )

    latency_ms = _elapsed_ms_since(started)
    shortlisted_ids = _normalized_skill_ids(raw.get("skill_ids", []))
    if not shortlisted_ids:
        _emit_skill_prerouting_failure(
            logger=logger,
            state=state,
            model=model,
            token_count=token_count,
            latency_ms=latency_ms,
            fail_closed_reason=None,
        )
        return SkillPipelineResult(
            selected_refs=[],
            selection_mode=f"{strategy}-select",
            context_budget=_CONTEXT_BUDGET_TIER_MEDIUM,
            capacity=capacity,
            effective_count=len(catalog),
            selection_reason=_SKILL_SELECTION_REASON_RETRIEVAL
            if strategy == "retrieval"
            else _SKILL_SELECTION_REASON_LLM,
            routed_intent=str(raw.get("intent", "") or "").strip() or intent,
            shortlisted_ids=[],
        )

    catalog_by_id = _catalog_by_id(catalog)
    refs: list[SkillRef] = []
    seen: set[str] = set()
    invalid_picks: list[str] = []
    clamped_count = 0
    for skill_id in shortlisted_ids:
        if skill_id not in catalog_by_id:
            invalid_picks.append(skill_id)
            continue
        lowered = skill_id.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        if len(refs) >= max(1, capacity):
            # this valid pick was dropped because we already hit
            # capacity. Record the count so post-hoc analysis can see when
            # the LLM is being asked to clamp itself.
            clamped_count += 1
            continue
        refs.append(_catalog_ref(catalog_by_id[skill_id], source=f"{strategy}-select"))
    if invalid_picks and not refs:
        fail_reason = _SKILL_SELECTION_INVALID_SKILL_ID
    else:
        fail_reason = None
    llm_pick_details = {
        "raw_pick_ids": list(shortlisted_ids),
        "invalid_pick_ids": invalid_picks,
        "clamped_pick_count": clamped_count,
    }
    if refs:
        _emit_shortlist(
            logger=logger,
            state=state,
            shortlisted_ids=[ref.skill_id for ref in refs],
            strategy=f"{strategy}-llm",
            query=intent,
        )
    return SkillPipelineResult(
        selected_refs=refs,
        selection_mode=f"{strategy}-select",
        context_budget=_CONTEXT_BUDGET_TIER_MEDIUM,
        capacity=capacity,
        effective_count=len(catalog),
        selection_reason=_SKILL_SELECTION_REASON_RETRIEVAL
        if strategy == "retrieval"
        else _SKILL_SELECTION_REASON_LLM,
        fail_closed_reason=fail_reason,
        routed_intent=str(raw.get("intent", "") or "").strip() or intent,
        shortlisted_ids=[ref.skill_id for ref in refs],
        llm_pick_details=llm_pick_details,
    )


def _build_skill_selection_context(
    *, intent: str, catalog: list[dict[str, Any]], capacity: int
) -> dict[str, Any]:
    catalog_lines = "\n".join(
        line for line in (_catalog_prompt_line(entry) for entry in catalog) if line
    )
    user_prompt = (
        "Available skills (identifiers are exact catalog facts; prefer exact "
        "identifier alignment over semantic substitution):\n"
        f"{catalog_lines}\n\n"
        f"Maximum skills to select: {max(1, capacity)}\n\n"
        f'User message: "{intent}"\n\n'
        "Rules:\n"
        "1. If the user names a skill that appears in the catalog by id, name, "
        "canonical_name, or display_name, return exactly that skill id.\n"
        "2. If the named skill is absent from the catalog, return an empty "
        "skill_ids list.\n"
        "3. Do not substitute a different skill just because it seems related.\n\n"
        "Return a JSON object with fields:\n"
        '{\n  "skill_ids": ["catalog skill id", "..."],\n  "intent": "brief intent phrase"\n}'
    )
    return {
        "messages": [
            {"role": "system", "content": _SKILL_SELECT_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "hints": {
            "user_input": intent,
            "mode_name": "skill_selection",
        },
    }


def _retrieval_shortlist_limit(*, catalog_size: int, capacity: int) -> int:
    normalized_catalog_size = max(0, int(catalog_size))
    normalized_capacity = max(1, int(capacity))
    if normalized_catalog_size <= 0:
        return max(normalized_capacity, _RETRIEVAL_SHORTLIST_K)
    return max(
        normalized_capacity,
        _RETRIEVAL_SHORTLIST_K,
        min(normalized_catalog_size, _RETRIEVAL_SHORTLIST_MAX),
    )


def _catalog_prompt_line(entry: dict[str, Any]) -> str:
    skill_id = str(entry.get("id", "") or "").strip()
    name = str(entry.get("name", "") or "").strip()
    canonical_name = str(entry.get("canonical_name", "") or "").strip()
    display_name = str(entry.get("display_name", "") or "").strip()
    summary = (
        str(entry.get("short_description", "") or "").strip()
        or str(entry.get("one_liner", "") or "").strip()
    )
    tags = ", ".join(_normalized_text_list(entry.get("tags"))[:4])
    tools = ", ".join(_normalized_text_list(entry.get("tools"))[:4])
    if not skill_id:
        return ""
    parts = [f"id={skill_id}"]
    if canonical_name and canonical_name.lower() != skill_id.lower():
        parts.append(f"canonical_name={canonical_name}")
    if display_name and display_name.lower() != name.lower():
        parts.append(f"display_name={display_name}")
    if name:
        parts.append(f"name={name}")
    if summary:
        parts.append(f"summary={summary}")
    if tags:
        parts.append(f"tags={tags}")
    if tools:
        parts.append(f"tools={tools}")
    return "- " + " | ".join(parts)


def _catalog_retrieval_title(entry: dict[str, Any]) -> str:
    display_name = str(entry.get("display_name", "") or "").strip()
    name = str(entry.get("name", "") or "").strip()
    skill_id = str(entry.get("id", "") or "").strip()
    if display_name:
        return display_name
    if name and not _looks_slug_like(name):
        return name
    for candidate in (name, skill_id):
        if candidate:
            humanized = _humanize_skill_alias(candidate)
            if humanized and humanized.lower() != candidate.lower():
                return humanized
            return candidate
    humanized = _humanize_skill_alias(skill_id)
    return humanized or skill_id


def _catalog_retrieval_text(entry: dict[str, Any]) -> str:
    skill_id = str(entry.get("id", "") or "").strip()
    if not skill_id:
        return ""
    base = _catalog_prompt_line(entry)
    aliases = _catalog_retrieval_aliases(entry)
    parts = [base[2:] if base.startswith("- ") else base]
    if aliases:
        parts.append(f"aliases={' ; '.join(aliases)}")
    return " ".join(part.strip() for part in parts if part.strip())


def _catalog_retrieval_aliases(entry: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    seen: set[str] = set()
    for raw in (
        entry.get("display_name", ""),
        entry.get("name", ""),
        entry.get("canonical_name", ""),
        entry.get("id", ""),
    ):
        text = str(raw or "").strip()
        if not text:
            continue
        for candidate in (text, _humanize_skill_alias(text)):
            normalized = str(candidate or "").strip()
            lowered = normalized.lower()
            if not normalized or lowered in seen:
                continue
            seen.add(lowered)
            aliases.append(normalized)
    return aliases


def _humanize_skill_alias(text: str) -> str:
    normalized = str(text or "").strip().replace("_", " ").replace("-", " ")
    return " ".join(part for part in normalized.split() if part)


def _looks_slug_like(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    return "_" in normalized or "-" in normalized or normalized == normalized.lower()


def _catalog_by_id(catalog: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(entry.get("id", "") or "").strip(): entry
        for entry in catalog
        if str(entry.get("id", "") or "").strip()
    }


def _catalog_ref(entry: dict[str, Any], *, source: str) -> SkillRef:
    return SkillRef(
        skill_id=str(entry.get("id", "") or "").strip(),
        version_hash=str(entry.get("version_hash", "") or "").strip(),
        source=source,
    )


def _catalog_refs(catalog: list[dict[str, Any]], *, source: str) -> list[SkillRef]:
    return [_catalog_ref(entry, source=source) for entry in catalog]


def _normalized_text_list(values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    if not isinstance(values, list):
        return out
    for raw in values:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _normalized_skill_ids(values: list[Any]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in list(values or []):
        skill_id = str(raw or "").strip()
        if not skill_id:
            continue
        lowered = skill_id.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(skill_id)
    return normalized


def _skill_source_ref(entry: dict[str, Any]) -> str:
    skill_id = str(entry.get("id", "") or "").strip()
    version_hash = str(entry.get("version_hash", "") or "").strip()
    return f"skill:{skill_id}@{version_hash}" if version_hash else f"skill:{skill_id}"


def _skill_id_from_retrieve_item(item: Any) -> str | None:
    ref_id = str(getattr(item, "ref_id", "") or "").strip()
    if not ref_id and isinstance(item, dict):
        ref_id = str(item.get("ref_id", "") or "").strip()
    if ref_id.startswith("skill:"):
        payload = ref_id.split(":", 1)[1]
        return payload.split("@", 1)[0].strip() or None
    return None


def _summarize_model_name(runner: "BrainRunner") -> str:
    llm_profiles = getattr(getattr(runner, "profile", None), "llm_profiles", None)
    return (
        str(getattr(llm_profiles, "act_model", "") or "").strip()
        or str(getattr(llm_profiles, "summarize_model", "") or "").strip()
    )


def _estimate_token_count(*, llm_api: Any, model: str, context: dict[str, Any]) -> int:
    try:
        return max(0, int(llm_api.estimate_tokens(model=model, context=context)))
    except Exception:
        return 0


def _elapsed_ms_since(started: float) -> int:
    return max(0, int(round((time.perf_counter() - started) * 1000.0)))


def _classify_failure_reason(exc: Exception) -> str:
    code = str(getattr(exc, "code", "") or "").strip().upper()
    if code == "TIMEOUT":
        return _SKILL_SELECTION_TIMEOUT
    if code == "RATE_LIMITED":
        return _SKILL_SELECTION_RATE_LIMITED
    message = str(exc).strip().lower()
    if "structured output" in message or "submit_output" in message:
        return _SKILL_SELECTION_PARSE_ERROR
    if "rate limited" in message:
        return _SKILL_SELECTION_RATE_LIMITED
    if "timed out" in message or " timeout" in message:
        return _SKILL_SELECTION_TIMEOUT
    return _SKILL_SELECTION_MODEL_UNAVAILABLE


def _emit_shortlist(
    *,
    logger: "CanonicalEventLogger",
    state: "WorkingState",
    shortlisted_ids: list[str],
    strategy: str,
    query: str,
) -> None:
    if not shortlisted_ids:
        return
    logger.emit(
        "skill.shortlisted",
        {
            "skill_ids": shortlisted_ids,
            "limit": len(shortlisted_ids),
            "strategy": strategy,
            "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest()[:16],
        },
        trace_id=state.trace_id,
    )


def _emit_skill_prerouting_failure(
    *,
    logger: "CanonicalEventLogger",
    state: "WorkingState",
    model: str,
    token_count: int,
    latency_ms: int,
    fail_closed_reason: str | None,
) -> None:
    logger.emit(
        "skill.prerouting",
        {
            "strategy": "llm",
            "needed": False,
            "skill_id": None,
            "intent": "",
            "model": model,
            "latency_ms": latency_ms,
            "token_count": token_count,
            "fail_closed_reason": fail_closed_reason,
        },
        trace_id=state.trace_id,
    )


def _emit_skill_selection_event(
    *,
    logger: "CanonicalEventLogger",
    state: "WorkingState",
    model: str,
    selection_mode: str,
    selected_refs: list[SkillRef],
    effective_count: int,
    capacity: int,
    routed_intent: str,
    fail_closed_reason: str | None,
    context_budget: str,
    shortlisted_ids: list[str],
    llm_pick_details: dict[str, Any] | None = None,
) -> None:
    primary = selected_refs[0] if selected_refs else None
    payload: dict[str, Any] = {
        "strategy": selection_mode,
        "needed": bool(selected_refs),
        "skill_id": primary.skill_id if primary else None,
        "primary_skill_id": primary.skill_id if primary else None,
        "selected_skill_ids": [ref.skill_id for ref in selected_refs],
        "selected_skill_count": len(selected_refs),
        "intent": routed_intent,
        "model": model,
        "latency_ms": 0,
        "token_count": 0,
        "fail_closed_reason": fail_closed_reason,
        "context_budget": context_budget,
        "effective_skill_count": effective_count,
        "prompt_capacity": capacity,
        "shortlisted_ids": shortlisted_ids,
    }
    # Keep absent on direct/no-catalog paths.
    if llm_pick_details is not None:
        payload["llm_pick_details"] = llm_pick_details
    logger.emit(
        "skill.prerouting",
        payload,
        trace_id=state.trace_id,
    )
    if primary is None:
        return
    logger.emit(
        "skill.selected",
        {
            "skill_ref": {
                "id": primary.skill_id,
                "version": primary.version_hash,
                "sha256": primary.version_hash,
            },
            "confidence": 1.0,
            "selection_reason": primary.source,
            "intent": routed_intent,
            "primary_skill_id": primary.skill_id,
            "selected_skill_ids": [ref.skill_id for ref in selected_refs],
            "selected_skill_count": len(selected_refs),
            "selection_mode": selection_mode,
        },
        trace_id=state.trace_id,
    )


__all__ = [
    "SkillPipelineResult",
    "SkillRef",
    "SkillSubsetSelection",
    "_CONTEXT_BUDGET_TIER_FULL",
    "_CONTEXT_BUDGET_TIER_MEDIUM",
    "_CONTEXT_BUDGET_TIER_SHORT",
    "_SKILL_SELECTION_REASON_DIRECT",
    "_SKILL_SELECTION_REASON_DIRECT_NAMED",
    "_SKILL_SELECTION_REASON_DIRECT_SINGLE_CATALOG",
    "_build_skill_selection_context",
    "_catalog_prompt_line",
    "_catalog_ref",
    "_catalog_refs",
    "_normalized_skill_ids",
    "_normalized_text_list",
    "_retrieval_shortlist_limit",
    "_select_skills_with_llm",
    "_select_skills_with_retrieval",
    "_summarize_model_name",
    "_emit_skill_selection_event",
]
