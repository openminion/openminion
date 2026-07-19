from __future__ import annotations

from openminion.base.time import utc_now_iso as iso_now  # noqa: F401

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .constants import (
    RISK_CLASSES,
    RISK_CLASS_LOW,
    SKILL_SOURCES,
    SKILL_SOURCE_OPERATOR_DECLARED,
    SKILL_STATUSES,
    SKILL_STATUS_DRAFT,
)

_WORD_RE = re.compile(r"[a-z0-9_\-\.]+")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _unique_texts(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def normalize_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return _unique_texts(value)


def slugify(text: str, fallback: str = "skill") -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", text.strip().lower())
    normalized = normalized.strip("_")
    return normalized or fallback


def normalize_status(status: str | None) -> str:
    raw = (status or SKILL_STATUS_DRAFT).strip().lower()
    if raw not in SKILL_STATUSES:
        return SKILL_STATUS_DRAFT
    return raw


def normalize_source(source: str | None) -> str:
    raw = (source or SKILL_SOURCE_OPERATOR_DECLARED).strip().lower()
    if raw not in SKILL_SOURCES:
        return SKILL_SOURCE_OPERATOR_DECLARED
    return raw


def normalize_risk(risk: str | None) -> str:
    raw = (risk or RISK_CLASS_LOW).strip().lower()
    if raw not in RISK_CLASSES:
        return RISK_CLASS_LOW
    return raw


def tokenize(text: str) -> list[str]:
    return [token for token in _WORD_RE.findall((text or "").lower()) if token]


def first_sentence(text: str) -> str:
    compact = " ".join((text or "").split())
    if not compact:
        return ""
    idx = compact.find(".")
    if idx < 0:
        return compact
    return compact[: idx + 1]


@dataclass
class RecipeStep:
    step_id: str
    instruction: str
    tool_id: str | None = None
    input_schema: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "instruction": self.instruction,
            "tool_id": self.tool_id,
            "input_schema": self.input_schema,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RecipeStep":
        return cls(
            step_id=str(raw.get("step_id", "")),
            instruction=str(raw.get("instruction", "")),
            tool_id=str(raw.get("tool_id")) if raw.get("tool_id") else None,
            input_schema=raw.get("input_schema")
            if isinstance(raw.get("input_schema"), dict)
            else None,
        )


@dataclass
class WorkflowStep:
    step_id: str
    description: str
    tool_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "description": self.description,
            "tool_id": self.tool_id,
        }


@dataclass
class Workflow:
    workflow_id: str
    name: str
    objective: str
    steps: list[WorkflowStep] = field(default_factory=list)
    source_skill_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "objective": self.objective,
            "steps": [step.to_dict() for step in self.steps],
            "source_skill_id": self.source_skill_id,
        }


@dataclass
class WorkflowCatalogEntry:
    workflow: Workflow
    skill_id: str
    version_hash: str
    status: str
    scope: str
    agent_id: str | None
    risk_class: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow.to_dict(),
            "skill_id": self.skill_id,
            "version_hash": self.version_hash,
            "status": self.status,
            "scope": self.scope,
            "agent_id": self.agent_id,
            "risk_class": self.risk_class,
        }


@dataclass
class WorkflowCatalog:
    entries: list[WorkflowCatalogEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"entries": [entry.to_dict() for entry in self.entries]}

    def get(self, workflow_id: str) -> WorkflowCatalogEntry | None:
        needle = str(workflow_id or "").strip()
        if not needle:
            return None
        return next(
            (entry for entry in self.entries if entry.workflow.workflow_id == needle),
            None,
        )

    @classmethod
    def from_skill_packages(
        cls,
        packages: list["SkillPackage"],
    ) -> "WorkflowCatalog":
        seen: set[str] = set()
        entries: list[WorkflowCatalogEntry] = []
        for package in packages:
            entry = package.to_workflow_catalog_entry()
            if entry is None:
                continue
            workflow_id = entry.workflow.workflow_id
            if workflow_id in seen:
                continue
            seen.add(workflow_id)
            entries.append(entry)
        entries.sort(key=lambda item: item.workflow.workflow_id)
        return cls(entries=entries)


@dataclass
class ToolRecipe:
    objective: str
    preflight: list[str] = field(default_factory=list)
    steps: list[RecipeStep] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    rollback: list[str] = field(default_factory=list)
    stop_conditions: list[str] = field(default_factory=list)
    idempotency_notes: str | None = None
    safety_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "preflight": list(self.preflight),
            "steps": [item.to_dict() for item in self.steps],
            "verification": list(self.verification),
            "rollback": list(self.rollback),
            "stop_conditions": list(self.stop_conditions),
            "idempotency_notes": self.idempotency_notes,
            "safety_notes": list(self.safety_notes),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ToolRecipe":
        return cls(
            objective=str(raw.get("objective", "")),
            preflight=normalize_text_list(raw.get("preflight")),
            steps=[
                RecipeStep.from_dict(item)
                for item in raw.get("steps", [])
                if isinstance(item, dict)
            ],
            verification=normalize_text_list(raw.get("verification")),
            rollback=normalize_text_list(raw.get("rollback")),
            stop_conditions=normalize_text_list(raw.get("stop_conditions")),
            idempotency_notes=str(raw.get("idempotency_notes"))
            if raw.get("idempotency_notes")
            else None,
            safety_notes=normalize_text_list(raw.get("safety_notes")),
        )

    def to_workflow(
        self,
        *,
        workflow_id: str,
        name: str,
        source_skill_id: str | None,
    ) -> Workflow:
        steps = [
            WorkflowStep(
                step_id=str(item.step_id or f"step_{idx + 1}"),
                description=str(item.instruction or "").strip(),
                tool_id=item.tool_id,
            )
            for idx, item in enumerate(self.steps)
            if str(item.instruction or "").strip()
        ]
        return Workflow(
            workflow_id=workflow_id,
            name=name,
            objective=str(self.objective or "").strip() or name,
            steps=steps,
            source_skill_id=source_skill_id,
        )


@dataclass
class SkillPackage:
    skill_id: str
    name: str
    display_name: str | None
    short_description: str | None
    default_prompt: str | None
    dependency_hints: dict[str, Any]
    bundle_metadata: dict[str, Any]
    status: str
    version_hash: str
    source_artifact_ref: str
    tags: list[str]
    tools: list[str]
    reference_hints: list[str]
    risk_class: str
    applies_to: dict[str, list[str]]
    inputs_schema: list[dict[str, Any]]
    snippets: dict[str, str]
    recipe: ToolRecipe | None
    verification_rules: list[str]
    rollback_hints: list[str]
    summary: str
    sections: dict[str, str]
    scope: str
    agent_id: str | None
    source_version: str | None
    created_at: str
    updated_at: str
    source: str = SKILL_SOURCE_OPERATOR_DECLARED
    teaches: list[str] = field(default_factory=list)
    requires_tools: list[str] = field(default_factory=list)
    safe_for_domains: list[str] = field(default_factory=list)
    forbidden_claims: list[str] = field(default_factory=list)
    evidence_expectations: list[str] = field(default_factory=list)

    def to_catalog_summary(self) -> dict[str, Any]:
        title = str(self.display_name or self.name or "").strip() or self.name
        raw_summary = str(self.short_description or self.summary or "").strip()
        summary_lines = [
            line.strip() for line in raw_summary.splitlines() if line.strip()
        ]
        if summary_lines and summary_lines[0].startswith("#"):
            summary_lines = summary_lines[1:]
        one_liner = " ".join(summary_lines)
        one_liner = re.sub(r"^#+\s*", "", one_liner).strip()
        one_liner = re.sub(
            r"^(summary|overview)\s*[:\-]?\s*", "", one_liner, flags=re.IGNORECASE
        ).strip()
        if not one_liner:
            one_liner = title
        return {
            "id": self.skill_id,
            "name": title,
            "display_name": str(self.display_name or ""),
            "canonical_name": self.name,
            "short_description": str(self.short_description or ""),
            "one_liner": one_liner,
            "version_hash": self.version_hash,
            "tags": list(self.tags),
            "tools": list(self.tools),
            "reference_hints": list(self.reference_hints),
        }

    def compact_summary_text(self) -> str:
        if self.short_description:
            return str(self.short_description).strip()
        summary_source = str(
            self.sections.get("summary", "") or self.summary or ""
        ).strip()
        return first_sentence(summary_source)

    def to_workflow(self) -> Workflow | None:
        if self.recipe is None:
            return None
        return self.recipe.to_workflow(
            workflow_id=f"workflow.{self.skill_id}",
            name=str(self.display_name or self.name or self.skill_id).strip(),
            source_skill_id=self.skill_id,
        )

    def to_workflow_catalog_entry(self) -> WorkflowCatalogEntry | None:
        workflow = self.to_workflow()
        if workflow is None:
            return None
        return WorkflowCatalogEntry(
            workflow=workflow,
            skill_id=self.skill_id,
            version_hash=self.version_hash,
            status=self.status,
            scope=self.scope,
            agent_id=self.agent_id,
            risk_class=self.risk_class,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "display_name": self.display_name,
            "short_description": self.short_description,
            "default_prompt": self.default_prompt,
            "dependency_hints": dict(self.dependency_hints),
            "bundle_metadata": dict(self.bundle_metadata),
            "status": self.status,
            "version_hash": self.version_hash,
            "source_artifact_ref": self.source_artifact_ref,
            "tags": list(self.tags),
            "tools": list(self.tools),
            "reference_hints": list(self.reference_hints),
            "risk_class": self.risk_class,
            "applies_to": {
                "intents": normalize_text_list(self.applies_to.get("intents")),
                "steps": normalize_text_list(self.applies_to.get("steps")),
            },
            "inputs_schema": list(self.inputs_schema),
            "snippets": dict(self.snippets),
            "recipe": None if self.recipe is None else self.recipe.to_dict(),
            "verification_rules": list(self.verification_rules),
            "rollback_hints": list(self.rollback_hints),
            "summary": self.summary,
            "sections": dict(self.sections),
            "scope": self.scope,
            "agent_id": self.agent_id,
            "source_version": self.source_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source": self.source,
            "teaches": list(self.teaches),
            "requires_tools": list(self.requires_tools),
            "safe_for_domains": list(self.safe_for_domains),
            "forbidden_claims": list(self.forbidden_claims),
            "evidence_expectations": list(self.evidence_expectations),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SkillPackage":
        applies_raw = (
            raw.get("applies_to") if isinstance(raw.get("applies_to"), dict) else {}
        )
        recipe_raw = raw.get("recipe") if isinstance(raw.get("recipe"), dict) else None
        snippets_raw = (
            raw.get("snippets") if isinstance(raw.get("snippets"), dict) else {}
        )
        sections_raw = (
            raw.get("sections") if isinstance(raw.get("sections"), dict) else {}
        )
        inputs_raw = (
            raw.get("inputs_schema")
            if isinstance(raw.get("inputs_schema"), list)
            else []
        )
        return cls(
            skill_id=str(raw.get("skill_id", "")),
            name=str(raw.get("name", "")),
            display_name=str(raw.get("display_name"))
            if raw.get("display_name")
            else None,
            short_description=str(raw.get("short_description"))
            if raw.get("short_description")
            else None,
            default_prompt=str(raw.get("default_prompt"))
            if raw.get("default_prompt")
            else None,
            dependency_hints=(
                raw.get("dependency_hints")
                if isinstance(raw.get("dependency_hints"), dict)
                else {}
            ),
            bundle_metadata=(
                raw.get("bundle_metadata")
                if isinstance(raw.get("bundle_metadata"), dict)
                else {}
            ),
            status=normalize_status(str(raw.get("status", SKILL_STATUS_DRAFT))),
            version_hash=str(raw.get("version_hash", "")),
            source_artifact_ref=str(raw.get("source_artifact_ref", "")),
            tags=normalize_text_list(raw.get("tags")),
            tools=normalize_text_list(raw.get("tools")),
            reference_hints=normalize_text_list(raw.get("reference_hints")),
            risk_class=normalize_risk(str(raw.get("risk_class", RISK_CLASS_LOW))),
            applies_to={
                "intents": normalize_text_list(applies_raw.get("intents")),
                "steps": normalize_text_list(applies_raw.get("steps")),
            },
            inputs_schema=[item for item in inputs_raw if isinstance(item, dict)],
            snippets={
                str(key): str(value)
                for key, value in snippets_raw.items()
                if isinstance(value, str)
            },
            recipe=ToolRecipe.from_dict(recipe_raw) if recipe_raw is not None else None,
            verification_rules=normalize_text_list(raw.get("verification_rules")),
            rollback_hints=normalize_text_list(raw.get("rollback_hints")),
            summary=str(raw.get("summary", "")).strip(),
            sections={
                str(key): str(value)
                for key, value in sections_raw.items()
                if isinstance(value, str)
            },
            scope=str(raw.get("scope", "global") or "global"),
            agent_id=str(raw.get("agent_id")) if raw.get("agent_id") else None,
            source_version=str(raw.get("source_version"))
            if raw.get("source_version")
            else None,
            created_at=str(raw.get("created_at", "")),
            updated_at=str(raw.get("updated_at", "")),
            source=normalize_source(
                str(raw.get("source", SKILL_SOURCE_OPERATOR_DECLARED))
            ),
            teaches=normalize_text_list(raw.get("teaches")),
            requires_tools=normalize_text_list(raw.get("requires_tools")),
            safe_for_domains=normalize_text_list(raw.get("safe_for_domains")),
            forbidden_claims=normalize_text_list(raw.get("forbidden_claims")),
            evidence_expectations=normalize_text_list(
                raw.get("evidence_expectations")
            ),
        )

    def to_version_hash(self) -> str:
        payload = self.to_dict()
        payload["version_hash"] = ""
        return stable_hash(payload)

    def keyword_candidates(self) -> list[str]:
        tokens: list[str] = []
        tokens.extend(tokenize(self.skill_id))
        tokens.extend(tokenize(self.name))
        tokens.extend(tokenize(self.display_name or ""))
        tokens.extend(tokenize(self.short_description or ""))
        tokens.extend(tokenize(self.summary))
        for item in self.tags:
            tokens.extend(tokenize(item))
        for item in self.tools:
            tokens.extend(tokenize(item))
        for item in self.teaches:
            tokens.extend(tokenize(item))
        for item in self.applies_to.get("intents", []):
            tokens.extend(tokenize(item))
        for item in self.applies_to.get("steps", []):
            tokens.extend(tokenize(item))
        for value in self.sections.values():
            tokens.extend(tokenize(value))
        return _unique_texts(tokens)


@dataclass
class SkillMatch:
    skill_id: str
    version_hash: str
    name: str
    status: str
    score: float
    reasons: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    risk_class: str = RISK_CLASS_LOW

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version_hash": self.version_hash,
            "name": self.name,
            "status": self.status,
            "score": float(self.score),
            "reasons": list(self.reasons),
            "tags": list(self.tags),
            "tools": list(self.tools),
            "risk_class": self.risk_class,
        }


@dataclass
class LintIssue:
    severity: str
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
