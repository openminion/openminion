from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, cast

from openminion.modules.context.input_boundaries import (
    emit_boundary_event as _pidf_emit_boundary_event,
)
from openminion.modules.skill.constants import RISK_CLASS_LOW
from openminion.modules.skill.errors import SkillError
from openminion.base.time import utc_now_iso as iso_now
from openminion.modules.skill.models import (
    SkillPackage,
    ToolRecipe,
    WorkflowCatalog,
    WorkflowCatalogEntry,
    canonical_json,
    normalize_status,
    normalize_text_list,
)
from openminion.modules.skill.runtime.parser import (
    normalize_render_purpose,
    purpose_to_section_keys,
)

from .ingest import _source_ref_from_payload, _source_ref_to_digest


class SkillCatalogMixin:
    config: Any
    store: Any
    _blob_store: Any
    _record_store: Any
    _assert_trust_promotion_allowed: Any
    _emit_event: Any
    _emit_skill_counter: Any
    _emit_skill_operation: Any
    _emit_untrusted_promotion_audit: Any
    _lint_package: Any
    _persist_package: Any
    _resolve_status_filter: Any

    def catalog_summaries(
        self,
        agent_id: str,
        status_filter: list[str] | str | None = None,
    ) -> list[dict[str, str]]:
        statuses = self._resolve_status_filter(status_filter, RISK_CLASS_LOW)
        rows = self.store.list_latest_skills(status_filter=statuses, agent_id=agent_id)
        return [
            SkillPackage.from_dict(row["package"]).to_catalog_summary() for row in rows
        ]

    def get_skill(self, skill_id: str, version_hash: str | None = None) -> SkillPackage:
        payload = self.store.get_skill_package(
            skill_id=skill_id, version_hash=version_hash
        )
        if payload is None:
            raise SkillError(
                "NOT_FOUND",
                "Skill not found",
                {"skill_id": skill_id, "version_hash": version_hash},
            )
        return SkillPackage.from_dict(payload)

    def set_skill_status(
        self,
        *,
        skill_id: str,
        new_status: str,
        version_hash: str | None = None,
        promotion_path: str = "runtime",
        reviewer_id: str | None = None,
    ) -> SkillPackage:
        package = self.get_skill(skill_id=skill_id, version_hash=version_hash)
        previous_status = package.status
        normalized_new_status = normalize_status(new_status)
        reviewer = str(reviewer_id or "").strip()
        self._assert_trust_promotion_allowed(
            package=package,
            previous_status=previous_status,
            new_status=normalized_new_status,
            promotion_path=promotion_path,
            reviewer_id=reviewer,
        )
        if previous_status == normalized_new_status:
            return package

        package.status = normalized_new_status
        package.updated_at = iso_now()
        package.version_hash = package.to_version_hash()
        self._persist_package(
            package=package,
            index_keywords=package.keyword_candidates(),
        )
        self._emit_untrusted_promotion_audit(
            package=package,
            previous_status=previous_status,
            new_status=normalized_new_status,
            promotion_path=promotion_path,
        )
        return package

    def list_skills(
        self, filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        filters = filters or {}
        status_filter = filters.get("status")
        if isinstance(status_filter, str):
            status_values = [status_filter]
        elif isinstance(status_filter, list):
            status_values = [str(item) for item in status_filter]
        else:
            status_values = None

        rows = self.store.list_skills(
            status_filter=[normalize_status(item) for item in status_values]
            if status_values
            else None,
            scope=str(filters.get("scope")) if filters.get("scope") else None,
            agent_id=str(filters.get("agent_id")) if filters.get("agent_id") else None,
        )

        tag_filter = str(filters.get("tag")) if filters.get("tag") else None
        tool_filter = str(filters.get("tool")) if filters.get("tool") else None

        out: list[dict[str, Any]] = []
        for row in rows:
            package = SkillPackage.from_dict(row["package"])
            if tag_filter and tag_filter not in package.tags:
                continue
            if tool_filter and tool_filter not in package.tools:
                continue
            out.append(
                {
                    "skill_id": package.skill_id,
                    "version_hash": package.version_hash,
                    "name": package.name,
                    "display_name": package.display_name,
                    "short_description": package.short_description,
                    "one_liner": package.to_catalog_summary()["one_liner"],
                    "status": package.status,
                    "scope": package.scope,
                    "agent_id": package.agent_id,
                    "risk_class": package.risk_class,
                    "tags": package.tags,
                    "tools": package.tools,
                    "updated_at": package.updated_at,
                }
            )
        return out

    def delete_skill(
        self,
        skill_id: str,
        version_hash: str | None = None,
    ) -> dict[str, int]:
        source_refs = self._collect_source_refs_for_delete(
            skill_id=skill_id, version_hash=version_hash
        )
        result = cast(
            dict[str, int],
            self.store.delete_skill(
                skill_id=skill_id,
                version_hash=version_hash,
            ),
        )
        self._handle_blob_retention_on_delete(
            skill_id=skill_id,
            version_hash=version_hash,
            source_refs=source_refs,
        )
        return result

    def _collect_source_refs_for_delete(
        self,
        *,
        skill_id: str,
        version_hash: str | None,
    ) -> list[str]:
        refs: list[str] = []
        if version_hash is not None:
            package_payload = self.store.get_skill_package(
                skill_id=skill_id, version_hash=version_hash
            )
            ref = _source_ref_from_payload(package_payload)
            if ref:
                refs.append(ref)
            return refs
        try:
            rows = self._record_store.query_dicts(
                "SELECT package_json FROM skill_versions WHERE skill_id = ?",
                (skill_id,),
            )
        except Exception:
            return refs
        for row in rows:
            try:
                payload = json.loads(str(row.get("package_json") or "{}"))
            except Exception:
                continue
            ref = _source_ref_from_payload(payload)
            if ref:
                refs.append(ref)
        return refs

    def _handle_blob_retention_on_delete(
        self,
        *,
        skill_id: str,
        version_hash: str | None,
        source_refs: list[str],
    ) -> None:
        policy = str(getattr(self.config, "skill_blob_retention", "retain")).strip()
        if policy not in {"retain", "gc"}:
            policy = "retain"
        if policy == "retain":
            self._emit_event(
                "skill.blob_retained_on_delete",
                {
                    "skill_id": skill_id,
                    "version_hash": version_hash,
                    "retention_policy": "retain",
                    "reason": "default_policy",
                    "source_refs": list(source_refs),
                },
            )
            return
        outcome = "gc_attempted"
        failed_refs: list[dict[str, str]] = []
        for ref in source_refs:
            digest = _source_ref_to_digest(ref)
            if digest is None:
                failed_refs.append(
                    {"source_ref": ref, "error": "unsupported_ref_scheme"}
                )
                continue
            try:
                self._blob_store.delete(digest)
            except Exception as exc:  # pragma: no cover — defensive
                failed_refs.append({"source_ref": ref, "error": str(exc)})
        if failed_refs:
            outcome = "gc_partial_failure"
        self._emit_event(
            "skill.blob_gc_on_delete",
            {
                "skill_id": skill_id,
                "version_hash": version_hash,
                "retention_policy": "gc",
                "outcome": outcome,
                "source_refs": list(source_refs),
                "failed_refs": failed_refs,
            },
        )

    def render_snippet(
        self,
        skill_id: str,
        version_hash: str | None,
        purpose: str,
        max_tokens: int,
        mode_name: str | None = None,
    ) -> tuple[str, str]:
        package = self.get_skill(skill_id=skill_id, version_hash=version_hash)
        normalized_purpose = normalize_render_purpose(purpose, mode_name=mode_name)
        if normalized_purpose not in {"plan", "respond", "act", "verify"}:
            raise SkillError(
                "INVALID_ARGUMENT",
                "purpose must be one of: plan, respond, act, verify",
            )
        render_extra = {
            "skill_id": package.skill_id,
            "purpose": normalized_purpose,
            "max_tokens": int(max_tokens),
        }
        self._emit_skill_operation(
            operation="select",
            status="ok",
            extra=render_extra,
        )

        lines: list[str] = [
            f"Skill: {package.display_name or package.name}",
            f"Skill ID: {package.skill_id}",
            f"Risk: {package.risk_class}",
        ]
        if package.tools:
            lines.append(f"Tools: {', '.join(package.tools)}")

        section_keys = purpose_to_section_keys(normalized_purpose)
        for key in section_keys:
            section_value = package.sections.get(key)
            if not section_value:
                continue
            label = key.replace("_", " ").title()
            lines.append(f"{label}:\n{section_value}")

        if normalized_purpose == "verify":
            if package.verification_rules:
                lines.append(
                    "Verification Rules:\n"
                    + "\n".join(f"- {item}" for item in package.verification_rules)
                )
        elif normalized_purpose == "act":
            if package.recipe and package.recipe.safety_notes:
                lines.append(
                    "Safety Notes:\n"
                    + "\n".join(f"- {item}" for item in package.recipe.safety_notes)
                )

        raw_snippet = "\n\n".join(lines).strip()
        trimmed = _trim_to_token_budget(raw_snippet, max_tokens=max_tokens)
        snippet_hash = hashlib.sha256(trimmed.encode("utf-8")).hexdigest()
        _pidf_emit_boundary_event(
            "skill_prompt",
            trimmed,
            seam_id="modules.skill.runtime.skill.render_snippet",
            provenance_ref=snippet_hash,
        )
        self._emit_skill_operation(
            operation="expand",
            status="ok",
            extra={**render_extra, "snippet_hash": snippet_hash},
        )
        self._emit_skill_counter(
            counter_name="selected_cards",
            value=1.0,
            extra=render_extra,
        )
        return trimmed, snippet_hash

    def get_recipe(
        self, skill_id: str, version_hash: str | None = None
    ) -> ToolRecipe | None:
        package = self.get_skill(skill_id=skill_id, version_hash=version_hash)
        return package.recipe

    def workflow_catalog(
        self,
        *,
        agent_id: str | None = None,
        status_filter: list[str] | str | None = None,
        scope: str | None = None,
    ) -> WorkflowCatalog:
        statuses = self._resolve_status_filter(status_filter, RISK_CLASS_LOW)
        rows = self.store.list_latest_skills(
            status_filter=statuses,
            agent_id=agent_id,
            scopes=[scope] if scope else None,
        )
        packages = [SkillPackage.from_dict(row["package"]) for row in rows]
        return WorkflowCatalog.from_skill_packages(packages)

    def get_workflow(
        self,
        workflow_id: str,
        *,
        agent_id: str | None = None,
        status_filter: list[str] | str | None = None,
        scope: str | None = None,
    ) -> WorkflowCatalogEntry:
        entry = self.workflow_catalog(
            agent_id=agent_id,
            status_filter=status_filter,
            scope=scope,
        ).get(workflow_id)
        if entry is None:
            raise SkillError(
                "NOT_FOUND",
                "Workflow not found",
                {
                    "workflow_id": workflow_id,
                    "agent_id": agent_id,
                    "scope": scope,
                },
            )
        return entry

    def lint(
        self, skill_id: str, version_hash: str | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        package = self.get_skill(skill_id=skill_id, version_hash=version_hash)
        issues = self._lint_package(package)
        warnings = [item.to_dict() for item in issues if item.severity != "error"]
        errors = [item.to_dict() for item in issues if item.severity == "error"]
        return {"warnings": warnings, "errors": errors}

    def log_run(
        self,
        session_id: str,
        agent_id: str,
        skill_id: str,
        version_hash: str,
        used_for: str,
        outcome: str,
        evidence_refs: list[str] | None = None,
    ) -> str:
        normalized_used_for = (used_for or "").strip().lower()
        if normalized_used_for not in {"plan", "act", "verify"}:
            raise SkillError(
                "INVALID_ARGUMENT", "used_for must be one of: plan, act, verify"
            )

        normalized_outcome = (outcome or "").strip().lower()
        if normalized_outcome not in {"success", "fail", "partial"}:
            raise SkillError(
                "INVALID_ARGUMENT", "outcome must be one of: success, fail, partial"
            )

        package = self.get_skill(skill_id=skill_id, version_hash=version_hash)
        if package.version_hash != version_hash:
            raise SkillError(
                "INVALID_ARGUMENT",
                "version hash mismatch for selected skill",
                {"skill_id": skill_id, "version_hash": version_hash},
            )

        run_id = str(uuid.uuid4())
        self.store.insert_skill_run(
            run_id=run_id,
            session_id=session_id,
            agent_id=agent_id,
            skill_id=skill_id,
            version_hash=version_hash,
            used_for=normalized_used_for,
            outcome=normalized_outcome,
            evidence_refs_json=canonical_json(normalize_text_list(evidence_refs or [])),
            created_at=iso_now(),
        )
        if normalized_outcome != "success":
            self._emit_skill_operation(
                operation="fallback",
                status="error" if normalized_outcome == "fail" else "ok",
                extra={
                    "skill_id": skill_id,
                    "used_for": normalized_used_for,
                    "outcome": normalized_outcome,
                },
            )
        return run_id


def _trim_to_token_budget(text: str, max_tokens: int) -> str:
    safe_tokens = max(32, int(max_tokens or 0))
    max_chars = safe_tokens * 4
    compact = (text or "").strip()
    if len(compact) <= max_chars:
        return compact

    clipped = compact[:max_chars]
    last_break = clipped.rfind("\n")
    if last_break > int(max_chars * 0.6):
        clipped = clipped[:last_break]
    return clipped.rstrip() + "\n\n[truncated]"
