from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.skill.constants import RISK_CLASS_LOW
from openminion.modules.skill.models import normalize_text_list, slugify, stable_hash


class SkillProposalDraft(BaseModel):
    """Draft definition for a proposed skill."""

    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str = ""
    short_description: str = ""
    tools: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    risk_class: str = RISK_CLASS_LOW
    applies_to: dict[str, list[str]] = Field(default_factory=dict)
    inputs_schema: list[dict[str, Any]] = Field(default_factory=list)
    verification_rules: list[str] = Field(default_factory=list)


class SkillProposal(BaseModel):
    """One typed proposal derived from a recurring task-shape."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    source_task_shape_ref: str
    proposed_skill_definition: SkillProposalDraft
    evidence_refs: list[str] = Field(default_factory=list)
    proposer_policy_id: str = ""
    proposed_at: str = ""


def _shape_field(shape: Any, field: str) -> Any:
    if isinstance(shape, Mapping):
        return shape.get(field)
    return getattr(shape, field, None)


def _titleize(value: str) -> str:
    parts = [part for part in str(value or "").replace("-", "_").split("_") if part]
    return " ".join(part.capitalize() for part in parts)


def _strategy_tokens(*values: str) -> set[str]:
    suffixes = ("-skill", "_skill")
    tokens: set[str] = set()
    for raw_value in values:
        value = str(raw_value or "").strip()
        if not value:
            continue
        normalized = slugify(value, fallback="")
        for candidate in (value, normalized):
            token = slugify(candidate, fallback="")
            if not token:
                continue
            tokens.add(token)
            for suffix in suffixes:
                if token.endswith(suffix):
                    stripped = token[: -len(suffix)].strip("-_")
                    if stripped:
                        tokens.add(stripped)
    return tokens


def _catalog_duplicate_signatures(
    current_catalog: Iterable[Any],
) -> set[tuple[str, str, str]]:
    """Build ``(strategy-ish name, capability tag, intent)`` signatures."""

    signatures: set[tuple[str, str, str]] = set()
    for item in current_catalog or []:
        if isinstance(item, Mapping):
            skill_id = str(item.get("skill_id") or item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            tags = normalize_text_list(item.get("tags"))
            applies_to = item.get("applies_to")
        else:
            skill_id = str(getattr(item, "skill_id", "") or "").strip()
            name = str(getattr(item, "name", "") or "").strip()
            tags = normalize_text_list(getattr(item, "tags", []))
            applies_to = getattr(item, "applies_to", None)
        intents = []
        if isinstance(applies_to, Mapping):
            intents = normalize_text_list(applies_to.get("intents"))
        strategy_tokens = sorted(_strategy_tokens(skill_id, name))
        for strategy_token in strategy_tokens:
            for capability in tags:
                for intent in intents:
                    signatures.add((strategy_token, capability, intent))
    return signatures


def _draft_from_shape(shape: Any) -> SkillProposalDraft:
    strategy_id = str(_shape_field(shape, "strategy_id") or "").strip()
    capability_category = str(_shape_field(shape, "capability_category") or "").strip()
    intent_category = str(_shape_field(shape, "intent_category") or "").strip()
    display_name = (
        f"{_titleize(strategy_id)} {_titleize(intent_category)} Playbook".strip()
    )
    return SkillProposalDraft(
        name=slugify(display_name),
        display_name=display_name,
        short_description=(
            f"Proposed from recurring {strategy_id} task-shape evidence for "
            f"{intent_category}."
        ).strip(),
        tools=[],
        tags=normalize_text_list([strategy_id, capability_category, intent_category]),
        risk_class=RISK_CLASS_LOW,
        applies_to={"intents": normalize_text_list([intent_category]), "steps": []},
        inputs_schema=[],
        verification_rules=[],
    )


def propose_skills_from_task_shapes(
    shapes: Iterable[Any],
    *,
    current_catalog: Iterable[Any],
    policy_id: str,
) -> list[SkillProposal]:
    """Project proposals from recurring task-shapes."""

    duplicate_signatures = _catalog_duplicate_signatures(current_catalog)
    proposals: list[SkillProposal] = []
    for shape in shapes or []:
        source_task_shape_ref = str(_shape_field(shape, "task_shape_ref") or "").strip()
        strategy_id = str(_shape_field(shape, "strategy_id") or "").strip()
        capability_category = str(
            _shape_field(shape, "capability_category") or ""
        ).strip()
        intent_category = str(_shape_field(shape, "intent_category") or "").strip()
        if (
            not source_task_shape_ref
            or not strategy_id
            or not capability_category
            or not intent_category
        ):
            continue
        candidate_tokens = _strategy_tokens(strategy_id)
        if any(
            (candidate_token, capability_category, intent_category)
            in duplicate_signatures
            for candidate_token in candidate_tokens
        ):
            continue
        draft = _draft_from_shape(shape)
        evidence_refs = normalize_text_list(
            list(_shape_field(shape, "performance_entry_refs") or [])
            + list(_shape_field(shape, "failure_pattern_refs") or [])
            + list(_shape_field(shape, "knowledge_record_refs") or [])
        )
        proposal_id = stable_hash(
            {
                "source_task_shape_ref": source_task_shape_ref,
                "proposer_policy_id": str(policy_id or ""),
                "draft": draft.model_dump(mode="json"),
                "evidence_refs": evidence_refs,
            }
        )
        proposals.append(
            SkillProposal(
                proposal_id=proposal_id,
                source_task_shape_ref=source_task_shape_ref,
                proposed_skill_definition=draft,
                evidence_refs=evidence_refs,
                proposer_policy_id=str(policy_id or "").strip(),
                proposed_at="",
            )
        )

    proposals.sort(key=lambda item: item.proposal_id)
    return proposals


__all__ = (
    "SkillProposal",
    "SkillProposalDraft",
    "propose_skills_from_task_shapes",
)
