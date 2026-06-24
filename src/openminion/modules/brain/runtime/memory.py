from __future__ import annotations

import json
from uuid import uuid4
from typing import TYPE_CHECKING, Any, Mapping, get_args

from ...memory.models import MemoryType
from ...memory.runtime.scope import emit_write_decision
from ..diagnostics.events import CanonicalEventLogger
from ..runtime.recovery import TCRPContext, TCRPRetryBudget, validate_payload
from ..schemas import (
    FailureMemoryReport,
    GoalDeclaration,
    GoalRevision,
    MetaRulePreference,
    ReflectReport,
    SuccessMemoryReport,
    WorkingState,
)
from .goal.policy import authorize_goal_action
from openminion.base.constants import STATE_KEY_SOURCE_OUTCOME

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..runner import BrainRunner


_SUPPORTED_MEMORY_TYPES = frozenset(get_args(MemoryType))
_CANDIDATE_RECORD_TYPE_BY_FIX_KIND = {
    "pin_candidate": "pin",
}

# Caller-declared CAMI seam IDs.
_SEAM_APPLY_IMPROVEMENTS = "brain.runtime.memory.apply_improvements"
_SEAM_APPLY_IMPROVEMENTS_CANDIDATE = "brain.runtime.memory.apply_improvements.candidate"
_SEAM_APPLY_SUCCESS = "brain.runtime.memory.apply_success_memories"
_SEAM_APPLY_FAILURE = "brain.runtime.memory.apply_failure_memories"
_SEAM_META_RULE_PREFERENCE = "brain.runtime.memory.stage_meta_rule_preference"
_SEAM_DECLARED_GOAL = "brain.runtime.memory.stage_declared_goal"
_SEAM_STRATEGY_OUTCOME = "brain.runtime.memory.stage_strategy_outcome"
_SEAM_GOAL_REVISION = "brain.runtime.memory.stage_goal_revision"
_SEAM_SELF_IMPROVEMENT_DECISION = "brain.runtime.memory.stage_self_improvement_decision"


def _validate_typed_record(
    *,
    payload: Mapping[str, Any],
    model: Any,
    channel_name: str,
) -> Any:
    validation = validate_payload(
        dict(payload),
        model=model,
        ctx=TCRPContext(channel_name=channel_name),
        retry_budget=TCRPRetryBudget(channel_name=channel_name, max_retries=0),
    )
    if validation.structured_payload is not None:
        return validation.structured_payload
    first = validation.validation_errors[0] if validation.validation_errors else None
    if first is None:
        raise ValueError(f"{channel_name} validation failed")
    raise ValueError(
        f"{channel_name} validation failed: {first.field_path} {first.error_code.value}"
    )


def _candidate_record_type_for_fix_kind(kind: str) -> str | None:
    if kind in _SUPPORTED_MEMORY_TYPES:
        return kind
    return _CANDIDATE_RECORD_TYPE_BY_FIX_KIND.get(kind)


def _success_memory_config(runner: "BrainRunner") -> Any:
    profile_cfg = getattr(getattr(runner, "profile", None), "success_memory", None)
    if profile_cfg is not None:
        return profile_cfg
    return getattr(getattr(runner, "options", None), "success_memory_config", None)


def stage_self_improvement_candidate(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    record_type: str,
    title: str,
    content: dict[str, Any],
    tags: list[str],
    evidence_refs: list[str],
    confidence: float,
    meta: dict[str, Any],
) -> str | None:
    if runner.memory_api is None:
        return None
    write_scope, _event = emit_write_decision(
        runner.profile.agent_id,
        caller_seam=_SEAM_SELF_IMPROVEMENT_DECISION,
    )
    candidate_id = runner.memory_api.stage_candidate(
        scope=write_scope,
        record_type=record_type,
        title=title,
        content=content,
        tags=tags,
        evidence_refs=evidence_refs,
        confidence=confidence,
        meta=meta,
    )
    state.memory_candidates.append(candidate_id)
    return candidate_id


def apply_improvements(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    report: ReflectReport,
    logger: CanonicalEventLogger,
) -> None:
    memory_ids: list[str] = []
    candidate_ids: list[str] = []
    skipped_fixes: list[dict[str, str]] = []
    governance_actions: list[dict[str, str]] = []

    for fix in report.fixes:
        action = str(getattr(fix, "action", "") or "").strip().lower()
        if action:
            governance_actions.append(
                {
                    "action": action,
                    "title": fix.title,
                    "target_command_id": str(
                        getattr(fix, "target_command_id", "") or ""
                    ).strip(),
                }
            )
        if fix.kind in {"lesson", "procedure"}:
            if (
                runner.memory_api is not None
                and runner.profile.defaults.auto_save_lessons
            ):
                # Brain reflection may emit "lesson", but memory backends only
                record_type = "fact" if fix.kind == "lesson" else fix.kind
                tags = list(fix.tags or ["self-improvement"])
                if fix.kind == "lesson" and "self-improvement:lesson" not in tags:
                    tags.append("self-improvement:lesson")
                write_scope, _event = emit_write_decision(
                    runner.profile.agent_id,
                    caller_seam=_SEAM_APPLY_IMPROVEMENTS,
                )
                try:
                    record_id = runner.memory_api.put_record(
                        scope=write_scope,
                        record_type=record_type,
                        title=fix.title,
                        content=fix.content,
                        tags=tags,
                        evidence_refs=[item.ref for item in fix.evidence_refs],
                    )
                except (TypeError, ValueError) as exc:
                    skipped_fixes.append(
                        {
                            "kind": fix.kind,
                            "title": fix.title,
                            "reason": str(exc),
                        }
                    )
                else:
                    memory_ids.append(record_id)
        else:
            if (
                runner.memory_api is not None
                and runner.profile.defaults.auto_stage_policy_candidates
            ):
                record_type = _candidate_record_type_for_fix_kind(fix.kind)
                if not record_type:
                    skipped_fixes.append(
                        {
                            "kind": fix.kind,
                            "title": fix.title,
                            "reason": "unsupported_candidate_record_type",
                        }
                    )
                else:
                    tags = list(fix.tags or ["candidate"])
                    if fix.kind != record_type:
                        normalized_tag = f"candidate_kind:{fix.kind}"
                        if normalized_tag not in tags:
                            tags.append(normalized_tag)
                    candidate_scope, _event = emit_write_decision(
                        runner.profile.agent_id,
                        caller_seam=_SEAM_APPLY_IMPROVEMENTS_CANDIDATE,
                    )
                    suggestion = str(getattr(fix, "scope_suggestion", "") or "").strip()
                    if suggestion and f"scope_suggestion:{suggestion}" not in tags:
                        tags.append(f"scope_suggestion:{suggestion}")
                    try:
                        candidate_id = runner.memory_api.stage_candidate(
                            scope=candidate_scope,
                            record_type=record_type,
                            title=fix.title,
                            content=fix.content,
                            tags=tags,
                            evidence_refs=[item.ref for item in fix.evidence_refs],
                        )
                    except (TypeError, ValueError) as exc:
                        skipped_fixes.append(
                            {
                                "kind": fix.kind,
                                "title": fix.title,
                                "reason": str(exc),
                            }
                        )
                    else:
                        candidate_ids.append(candidate_id)

        safeguard = f"Guardrail: {fix.title}"
        if safeguard not in state.constraints:
            state.constraints.append(safeguard)

    state.memory_candidates.extend(candidate_ids)
    logger.emit(
        "brain.improve.applied",
        {
            "memory_ids": memory_ids,
            "candidate_ids": candidate_ids,
            "skipped_fixes": skipped_fixes,
            "governance_actions": governance_actions,
        },
        trace_id=state.trace_id,
        memory_refs=memory_ids + candidate_ids,
    )


def apply_success_memories(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    report: SuccessMemoryReport,
    logger: CanonicalEventLogger,
    provenance_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = _success_memory_config(runner)
    if runner.memory_api is None or config is None:
        return {
            "candidate_ids": [],
            "skipped_items": [
                {
                    "reason": "memory_api_unavailable",
                    "title": "",
                }
            ],
        }

    max_items = max(1, int(getattr(config, "max_items_per_turn", 3) or 3))
    min_confidence = float(getattr(config, "min_item_confidence", 0.7) or 0.7)
    procedure_enabled = bool(getattr(config, "procedure_enabled", True))
    tool_habit_enabled = bool(getattr(config, "tool_habit_enabled", True))
    base_meta = dict(provenance_meta or {})

    candidate_ids: list[str] = []
    skipped_items: list[dict[str, Any]] = []
    staged_items = 0

    for item in report.items:
        if staged_items >= max_items:
            skipped_items.append(
                {
                    "kind": item.kind,
                    "title": item.title,
                    "reason": "max_items_reached",
                }
            )
            continue
        if float(item.confidence) < min_confidence:
            skipped_items.append(
                {
                    "kind": item.kind,
                    "title": item.title,
                    "reason": "below_min_confidence",
                    "confidence": float(item.confidence),
                }
            )
            continue
        if item.kind == "procedure" and not procedure_enabled:
            skipped_items.append(
                {
                    "kind": item.kind,
                    "title": item.title,
                    "reason": "procedure_disabled",
                }
            )
            continue
        if item.kind == "tool_habit" and not tool_habit_enabled:
            skipped_items.append(
                {
                    "kind": item.kind,
                    "title": item.title,
                    "reason": "tool_habit_disabled",
                }
            )
            continue

        tags = list(item.tags or [])
        if "success_path" not in tags:
            tags.append("success_path")
        meta = dict(base_meta)
        item_rationale = (
            str(getattr(item, "rationale", "") or "").strip()
            or str(base_meta.get("source_thinking_rationale", "") or "").strip()
        )
        meta.update(
            {
                "source_kind": item.kind,
                "source_success_path": True,
            }
        )
        if item_rationale:
            meta["rationale"] = item_rationale
        content_payload = item.content
        if item_rationale and isinstance(item.content, Mapping):
            content_payload = dict(item.content)
            content_payload.setdefault("rationale", item_rationale)
        write_scope, _event = emit_write_decision(
            runner.profile.agent_id,
            caller_seam=_SEAM_APPLY_SUCCESS,
        )
        candidate_id = runner.memory_api.stage_candidate(
            scope=write_scope,
            record_type=item.kind,
            title=item.title,
            content=content_payload,
            tags=tags,
            evidence_refs=[artifact.ref for artifact in item.evidence_refs],
            confidence=float(item.confidence),
            meta=meta,
        )
        candidate_ids.append(candidate_id)
        staged_items += 1

    state.memory_candidates.extend(candidate_ids)
    return {
        "candidate_ids": candidate_ids,
        "skipped_items": skipped_items,
    }


def apply_failure_memories(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    report: FailureMemoryReport,
    logger: CanonicalEventLogger,
    provenance_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del logger
    if runner.memory_api is None:
        return {
            "candidate_ids": [],
            "meta_rule_preference_candidate_id": None,
            "meta_rule_preference_skipped_reason": "memory_api_unavailable",
            "skipped_items": [{"reason": "memory_api_unavailable", "title": ""}],
        }

    base_meta = dict(provenance_meta or {})
    candidate_ids: list[str] = []
    skipped_items: list[dict[str, Any]] = []

    for item in report.items:
        tags = list(item.tags or [])
        if "failure_path" not in tags:
            tags.append("failure_path")
        meta = dict(base_meta)
        meta.update(
            {
                "source_kind": item.kind,
                "source_failure_path": True,
                "source_negative_outcome": True,
                STATE_KEY_SOURCE_OUTCOME: "failure",
                "source_termination_reason": report.termination_reason,
            }
        )
        write_scope, _event = emit_write_decision(
            runner.profile.agent_id,
            caller_seam=_SEAM_APPLY_FAILURE,
        )
        # CAMI-01b: replace the ad-hoc `f"{item.scope_suggestion}:{agent_id}"`
        suggestion = str(getattr(item, "scope_suggestion", "") or "").strip()
        if suggestion and f"scope_suggestion:{suggestion}" not in tags:
            tags.append(f"scope_suggestion:{suggestion}")
        candidate_id = runner.memory_api.stage_candidate(
            scope=write_scope,
            record_type=item.kind,
            title=item.title,
            content=item.content,
            tags=tags,
            evidence_refs=[artifact.ref for artifact in item.evidence_refs],
            confidence=float(item.confidence),
            meta=meta,
        )
        candidate_ids.append(candidate_id)

    preference_candidate_id: str | None = None
    preference_skipped_reason: str | None = None
    if report.meta_rule_preference is not None:
        preference_result = stage_meta_rule_preference(
            runner,
            state=state,
            preference=report.meta_rule_preference,
            provenance_meta={
                **base_meta,
                "source_failure_path": True,
                "source_negative_outcome": True,
                STATE_KEY_SOURCE_OUTCOME: "failure",
                "source_termination_reason": report.termination_reason,
            },
        )
        raw_candidate_id = preference_result.get("candidate_id")
        if raw_candidate_id is not None:
            preference_candidate_id = str(raw_candidate_id)
        raw_skipped_reason = preference_result.get("skipped_reason")
        if raw_skipped_reason is not None:
            preference_skipped_reason = str(raw_skipped_reason)

    state.memory_candidates.extend(candidate_ids)
    return {
        "candidate_ids": candidate_ids,
        "meta_rule_preference_candidate_id": preference_candidate_id,
        "meta_rule_preference_skipped_reason": preference_skipped_reason,
        "skipped_items": skipped_items,
    }


def stage_meta_rule_preference(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    preference: MetaRulePreference | Mapping[str, Any],
    provenance_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if runner.memory_api is None:
        return {"candidate_id": None, "skipped_reason": "memory_api_unavailable"}

    structured = (
        preference
        if isinstance(preference, MetaRulePreference)
        else _validate_typed_record(
            payload=dict(preference),
            model=MetaRulePreference,
            channel_name="memory.meta_rule_preference",
        )
    )
    normalized_value = structured.preferred_value
    value_text = json.dumps(normalized_value, ensure_ascii=True)
    reasoning = str(structured.reasoning or "").strip()
    text = f"rule={structured.rule} preferred_value={value_text}"
    if reasoning:
        text = f"{text} reasoning={reasoning}"
    write_scope, _event = emit_write_decision(
        runner.profile.agent_id,
        caller_seam=_SEAM_META_RULE_PREFERENCE,
    )
    candidate_id = runner.memory_api.stage_candidate(
        scope=write_scope,
        record_type="meta_rule_preference",
        title=f"meta_rule_preference:{structured.rule}:{value_text}",
        content={
            "rule": structured.rule,
            "preferred_value": normalized_value,
            "reasoning": reasoning,
            "text": text,
        },
        tags=["meta_rule_preference", f"rule:{structured.rule}"],
        evidence_refs=[],
        confidence=0.7,
        meta={
            "source_meta_rule_preference": True,
            **dict(provenance_meta or {}),
        },
    )
    state.memory_candidates.append(candidate_id)
    return {"candidate_id": candidate_id, "skipped_reason": None}


def stage_declared_goal(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    goal: GoalDeclaration | Mapping[str, Any],
    provenance_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stage a model-authored goal declaration as a `declared_goal` candidate."""
    if runner.memory_api is None:
        return {"candidate_id": None, "skipped_reason": "memory_api_unavailable"}

    structured = (
        goal
        if isinstance(goal, GoalDeclaration)
        else _validate_typed_record(
            payload=dict(goal),
            model=GoalDeclaration,
            channel_name="memory.declared_goal",
        )
    )
    title_suffix = (
        structured.goal[:60].rstrip() if len(structured.goal) > 60 else structured.goal
    )
    text_parts = [
        f"goal_id={structured.goal_id or ''}",
        f"parent_goal_id={structured.parent_goal_id or ''}",
        f"depth={structured.depth}",
        f"goal={structured.goal}",
        f"trigger={structured.trigger}",
        f"priority={structured.priority}",
        f"action_type={structured.action_type}",
    ]
    if structured.suggested_schedule:
        text_parts.append(f"schedule={structured.suggested_schedule}")
    text = "; ".join(text_parts)
    write_scope, _event = emit_write_decision(
        runner.profile.agent_id,
        caller_seam=_SEAM_DECLARED_GOAL,
    )
    candidate_id = runner.memory_api.stage_candidate(
        scope=write_scope,
        record_type="declared_goal",
        title=f"declared_goal:{structured.action_type}:{title_suffix}",
        content={
            "goal_id": structured.goal_id or f"goal_{uuid4().hex[:12]}",
            "parent_goal_id": structured.parent_goal_id,
            "depth": structured.depth,
            "goal": structured.goal,
            "trigger": structured.trigger,
            "priority": structured.priority,
            "action_type": structured.action_type,
            "suggested_schedule": structured.suggested_schedule,
            "text": text,
        },
        tags=[
            "declared_goal",
            f"action_type:{structured.action_type}",
            f"priority:{structured.priority}",
        ],
        evidence_refs=[],
        # confidence at 0.6 — slightly below meta_rule_preference's
        confidence=0.6,
        meta={
            "source_declared_goal": True,
            **dict(provenance_meta or {}),
        },
    )
    state.memory_candidates.append(candidate_id)
    return {"candidate_id": candidate_id, "skipped_reason": None}


def stage_strategy_outcome(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    strategy_id: str,
    capability_category: str,
    intent_category: str,
    outcome_status: str,
    provenance_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if runner.memory_api is None:
        return {"record_id": None, "skipped_reason": "memory_api_unavailable"}

    normalized_strategy_id = str(strategy_id or "").strip().lower()
    normalized_capability = str(capability_category or "").strip().lower()
    normalized_intent = str(intent_category or "").strip().lower()
    normalized_outcome = str(outcome_status or "").strip().lower()
    if not normalized_strategy_id:
        return {"record_id": None, "skipped_reason": "missing_strategy_id"}
    if not normalized_outcome:
        return {"record_id": None, "skipped_reason": "missing_outcome_status"}

    title_parts = ["strategy_outcome", normalized_strategy_id, normalized_outcome]
    if normalized_intent:
        title_parts.append(normalized_intent)
    title = ":".join(title_parts)
    text_parts = [
        f"strategy_id={normalized_strategy_id}",
        f"outcome_status={normalized_outcome}",
    ]
    if normalized_capability:
        text_parts.append(f"capability_category={normalized_capability}")
    if normalized_intent:
        text_parts.append(f"intent_category={normalized_intent}")
    content = {
        "strategy_id": normalized_strategy_id,
        "capability_category": normalized_capability,
        "intent_category": normalized_intent,
        "outcome_status": normalized_outcome,
        "agent_id": str(getattr(state, "agent_id", "") or "").strip(),
        "session_id": str(getattr(state, "session_id", "") or "").strip(),
        "created_at": str(
            getattr(state, "decision_context_recorded_at", "") or ""
        ).strip(),
        "turn_id": str(getattr(state, "trace_id", "") or "").strip(),
        "turn_index": getattr(state, "turn_index", None),
        "termination_reason": str(
            dict(provenance_meta or {}).get("source_termination_reason") or ""
        ).strip(),
        "text": "; ".join(text_parts),
    }
    write_scope, _event = emit_write_decision(
        runner.profile.agent_id,
        caller_seam=_SEAM_STRATEGY_OUTCOME,
    )
    record_id = runner.memory_api.put_record(
        scope=write_scope,
        record_type="strategy_outcome",
        title=title,
        content=content,
        tags=[
            tag
            for tag in [
                "strategy_outcome",
                f"strategy_id:{normalized_strategy_id}",
                (
                    f"capability_category:{normalized_capability}"
                    if normalized_capability
                    else ""
                ),
                f"intent_category:{normalized_intent}" if normalized_intent else "",
                f"outcome_status:{normalized_outcome}",
            ]
            if tag
        ],
        evidence_refs=[],
    )
    return {"record_id": record_id, "skipped_reason": None}


def stage_goal_revision(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    goal_revision: GoalRevision | Mapping[str, Any],
    provenance_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist an authorized model-authored goal revision as a typed record."""
    if runner.memory_api is None:
        return {
            "record_id": None,
            "skipped_reason": "memory_api_unavailable",
            "policy_verdict": None,
            "policy_allowed": None,
            "requires_user_confirm": None,
        }

    structured = (
        goal_revision
        if isinstance(goal_revision, GoalRevision)
        else _validate_typed_record(
            payload=dict(goal_revision),
            model=GoalRevision,
            channel_name="memory.goal_revision",
        )
    )
    auth = authorize_goal_action(
        profile_policy=getattr(runner.profile, "goal_execution_policy", None),
        action_type=structured.action_type,
    )
    if not auth.allowed:
        skipped_reason = f"policy_denied:{auth.reason}"
        return {
            "record_id": None,
            "skipped_reason": skipped_reason,
            "policy_verdict": auth.reason,
            "policy_allowed": False,
            "requires_user_confirm": bool(auth.requires_user_confirm),
        }

    text_parts = [
        f"goal_id={structured.goal_id or ''}",
        f"parent_goal_id={structured.parent_goal_id or ''}",
        f"depth={structured.depth}",
        f"previous_goal={structured.previous_goal}",
        f"goal={structured.goal}",
        f"trigger={structured.trigger}",
        f"priority={structured.priority}",
        f"action_type={structured.action_type}",
    ]
    if structured.suggested_schedule:
        text_parts.append(f"schedule={structured.suggested_schedule}")
    text = "; ".join(text_parts)
    title_suffix = structured.goal[:60].rstrip()
    content = {
        "goal_id": structured.goal_id or f"goal_{uuid4().hex[:12]}",
        "parent_goal_id": structured.parent_goal_id,
        "depth": structured.depth,
        "previous_goal": structured.previous_goal,
        "goal": structured.goal,
        "trigger": structured.trigger,
        "priority": structured.priority,
        "action_type": structured.action_type,
        "suggested_schedule": structured.suggested_schedule,
        "agent_id": str(getattr(state, "agent_id", "") or "").strip(),
        "session_id": str(getattr(state, "session_id", "") or "").strip(),
        "created_at": str(
            getattr(state, "decision_context_recorded_at", "") or ""
        ).strip(),
        "turn_id": str(getattr(state, "trace_id", "") or "").strip(),
        "turn_index": getattr(state, "turn_index", None),
        "policy_verdict": auth.reason,
        "policy_allowed": True,
        "requires_user_confirm": False,
        "text": text,
    }
    write_scope, _event = emit_write_decision(
        runner.profile.agent_id,
        caller_seam=_SEAM_GOAL_REVISION,
    )
    record_id = runner.memory_api.put_record(
        scope=write_scope,
        record_type="goal_revision",
        title=f"goal_revision:{structured.action_type}:{title_suffix}",
        content=content,
        tags=[
            "goal_revision",
            f"action_type:{structured.action_type}",
            f"priority:{structured.priority}",
        ],
        evidence_refs=[],
    )
    return {
        "record_id": record_id,
        "skipped_reason": None,
        "policy_verdict": auth.reason,
        "policy_allowed": True,
        "requires_user_confirm": False,
    }
