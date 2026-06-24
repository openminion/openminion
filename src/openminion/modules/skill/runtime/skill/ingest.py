from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Iterable, cast

from openminion.modules.skill.runtime.bundle_metadata import (
    BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL,
    BUNDLE_METADATA_TRUST_UNTRUSTED_REMOTE,
    companion_metadata_unavailable_warning,
    load_companion_metadata,
    resolve_bundle_metadata_trust,
)
from openminion.modules.skill.constants import (
    HIGH_RISK_CLASSES,
    RISK_CLASS_HIGH,
    RISK_CLASS_LOW,
    SKILL_STATUSES,
    SKILL_STATUS_BLESSED,
    SKILL_STATUS_DRAFT,
    SKILL_STATUS_VERIFIED,
    SKILL_TOOL_REGISTRY_UNAVAILABLE,
    VERIFIED_SKILL_STATUSES,
)
from openminion.modules.skill.errors import SkillError
from openminion.base.time import utc_now_iso as iso_now
from openminion.modules.skill.models import (
    LintIssue,
    SkillPackage,
    canonical_json,
    normalize_risk,
    normalize_status,
    normalize_text_list,
    slugify,
)
from openminion.modules.skill.runtime.parser import (
    build_default_snippets,
    build_recipe,
    detect_tools,
    front_matter_unknown_key_warnings,
    parse_markdown,
)
from openminion.modules.skill.proposal.review import _RUNTIME_REVIEWER_IDS

_DANGEROUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\breboot\b", re.IGNORECASE),
)
_BARE_HEADING_RE = re.compile(r"^#{1,3}\s+\S.*$")
_CANONICAL_SECTION_KEYS = frozenset(
    {
        "summary",
        "procedure",
        "preconditions",
        "verification",
        "rollback",
        "when_to_use",
        "pitfalls",
    }
)
_ARTIFACT_REF_PREFIX = "artifact://sha256/"

_REFERENCE_FILE_SUFFIXES = (
    ".md",
    ".txt",
    ".tsx",
    ".ts",
    ".jsx",
    ".js",
    ".py",
    ".toml",
    ".json",
    ".yaml",
    ".yml",
    ".ini",
)


class SkillIngestMixin:
    config: Any
    store: Any
    _artifact_ingestor: Any
    _artifact_loader: Any
    _blob_store: Any
    _hybrid_store: Any
    _known_tools: Any
    _known_tools_state: Any
    _emit_event: Any
    _emit_skill_operation: Any

    def _validate_path(self, path: Path) -> None:
        if not self.config.ingest_enabled:
            raise SkillError(
                "INGEST_DISABLED",
                "Skill ingest is disabled by policy",
                {"path": str(path)},
            )

        if ".." in path.parts:
            raise SkillError(
                "PATH_TRAVERSAL",
                "Path contains traversal sequences",
                {"path": str(path)},
            )

        if self.config.allowed_roots:
            resolved = path.resolve()
            allowed_resolved = [
                Path(p).expanduser().resolve() for p in self.config.allowed_roots
            ]
            if not any(
                str(resolved).startswith(str(allowed)) for allowed in allowed_resolved
            ):
                raise SkillError(
                    "PATH_NOT_ALLOWED",
                    "Path is outside allowed roots",
                    {"path": str(path), "allowed_roots": self.config.allowed_roots},
                )

        if not path.exists():
            raise SkillError(
                "PATH_NOT_FOUND",
                "File does not exist",
                {"path": str(path)},
            )

        if path.suffix.lower() != ".md":
            raise SkillError(
                "INVALID_FILE_TYPE",
                "Only .md files are supported",
                {"path": str(path), "suffix": path.suffix},
            )

    def ingest_text(
        self,
        name: str,
        markdown: str,
        scope: str = "global",
        agent_id: str | None = None,
        trust: str | None = None,
        promotion_path: str = "operator",
    ) -> tuple[str, str, list[str]]:
        source_ref = self._store_source(name=name, markdown=markdown)
        return self._build_and_finalize_ingest(
            markdown=markdown,
            explicit_name=name,
            source_ref=source_ref,
            scope=scope,
            agent_id=agent_id,
            bundle_root=None,
            trust=trust,
            remote_source=False,
            promotion_path=promotion_path,
        )

    def ingest_file(
        self,
        path: str | Path,
        *,
        name: str | None = None,
        scope: str = "global",
        agent_id: str | None = None,
        trust: str | None = None,
        promotion_path: str = "operator",
    ) -> tuple[str, str, list[str]]:
        src = Path(path).expanduser()
        try:
            self._validate_path(src)
        except SkillError as exc:
            self._emit_event(
                "skill.ingest_failed",
                {
                    "source_path": str(src),
                    "error_code": exc.code,
                    "error_detail": exc.message,
                },
            )
            raise
        text = src.read_text(encoding="utf-8")
        resolved_name = name or src.stem
        try:
            source_ref = self._store_source(name=resolved_name, markdown=text)
            return self._build_and_finalize_ingest(
                markdown=text,
                explicit_name=resolved_name,
                source_ref=source_ref,
                scope=scope,
                agent_id=agent_id,
                bundle_root=src.parent,
                trust=trust,
                remote_source=False,
                promotion_path=promotion_path,
            )
        except Exception as exc:
            self._emit_event(
                "skill.ingest_failed",
                {
                    "source_path": str(src),
                    "error_code": getattr(exc, "code", "UNKNOWN"),
                    "error_detail": str(exc),
                },
            )
            raise

    def ingest_artifact(
        self,
        source_artifact_ref: str,
        *,
        name: str,
        scope: str = "global",
        agent_id: str | None = None,
        trust: str | None = None,
        promotion_path: str = "operator",
    ) -> tuple[str, str, list[str]]:
        if self._artifact_loader is None:
            raise SkillError(
                "INVALID_ARGUMENT",
                "artifact_loader is not configured",
                {"source_artifact_ref": source_artifact_ref},
            )

        payload = self._artifact_loader(source_artifact_ref)
        if isinstance(payload, bytes):
            markdown = payload.decode("utf-8", errors="replace")
        else:
            markdown = str(payload)

        return self._build_and_finalize_ingest(
            markdown=markdown,
            explicit_name=name,
            source_ref=source_artifact_ref,
            scope=scope,
            agent_id=agent_id,
            bundle_root=None,
            trust=trust,
            remote_source=False,
            promotion_path=promotion_path,
        )

    def ingest_url(
        self,
        *,
        url: str,
        name: str,
        markdown: str,
        scope: str = "global",
        agent_id: str | None = None,
        trust: str | None = None,
        promotion_path: str = "runtime",
    ) -> tuple[str, str, list[str]]:
        source_ref = self._store_source(name=name, markdown=markdown)
        return self._build_and_finalize_ingest(
            markdown=markdown,
            explicit_name=name,
            source_ref=source_ref,
            scope=scope,
            agent_id=agent_id,
            bundle_root=None,
            trust=trust,
            remote_source=True,
            promotion_path=promotion_path,
            source_url=url,
        )

    def _build_and_finalize_ingest(
        self,
        *,
        markdown: str,
        explicit_name: str,
        source_ref: str,
        scope: str,
        agent_id: str | None,
        bundle_root: Path | None,
        trust: str | None,
        remote_source: bool,
        promotion_path: str,
        source_url: str | None = None,
    ) -> tuple[str, str, list[str]]:
        package, parse_warnings = self._build_package(
            markdown=markdown,
            explicit_name=explicit_name,
            source_artifact_ref=source_ref,
            scope=scope,
            agent_id=agent_id,
            bundle_root=bundle_root,
            trust=trust,
            remote_source=remote_source,
        )
        return self._finalize_ingest(
            package=package,
            parse_warnings=parse_warnings,
            source_ref=source_ref,
            scope=scope,
            markdown=markdown,
            promotion_path=promotion_path,
            source_url=source_url,
        )

    def _store_source(self, *, name: str, markdown: str) -> str:
        if self._artifact_ingestor is not None:
            try:
                ref = self._artifact_ingestor(name, markdown)
            except Exception as exc:
                raise SkillError(
                    "ARTIFACT_INGEST_FAILED",
                    "Artifact ingest failed",
                    {"error": str(exc)},
                ) from exc
            return str(ref)

        payload = markdown.encode("utf-8")
        ref = self._blob_store.put_bytes(
            payload,
            media_type="text/markdown",
            ext="md",
            meta={"name": name},
        )
        return f"artifact://sha256/{ref.hash}"

    def _persist_package(
        self, *, package: SkillPackage, index_keywords: list[str]
    ) -> list[str]:
        warnings: list[str] = []
        try:
            self.store.upsert_skill(
                skill_id=package.skill_id,
                name=package.name,
                status=package.status,
                scope=package.scope,
                agent_id=package.agent_id,
                ts=package.updated_at,
            )
            self.store.insert_skill_version(
                skill_id=package.skill_id,
                version_hash=package.version_hash,
                source_artifact_ref=package.source_artifact_ref,
                package_json=canonical_json(package.to_dict()),
                created_at=package.created_at,
            )
            self.store.upsert_skill_index(
                skill_id=package.skill_id,
                version_hash=package.version_hash,
                tags_json=canonical_json(package.tags),
                tools_json=canonical_json(package.tools),
                keywords_json=canonical_json(index_keywords),
                applies_to_json=canonical_json(package.applies_to),
            )
        except Exception as exc:
            self._hybrid_store.write_row(
                "skill_ingest",
                {
                    "row_id": str(uuid.uuid4()),
                    "skill_id": package.skill_id,
                    "version_hash": package.version_hash,
                    "status": package.status,
                    "scope": package.scope,
                    "agent_id": package.agent_id,
                    "source_artifact_ref": package.source_artifact_ref,
                    "package_json": canonical_json(package.to_dict()),
                    "ts": iso_now(),
                    "sqlite_error": str(exc),
                },
            )
            warnings.append("storage.fallback_sidecar")
            warnings.append(f"storage.sqlite_error:{exc}")
        return warnings

    def _build_package(
        self,
        *,
        markdown: str,
        explicit_name: str,
        source_artifact_ref: str,
        scope: str,
        agent_id: str | None,
        bundle_root: Path | None,
        trust: str | None,
        remote_source: bool,
    ) -> tuple[SkillPackage, list[str]]:
        front_matter, sections, summary, parse_warnings = parse_markdown(markdown)
        sections = dict(sections)
        parse_warnings = list(parse_warnings) + front_matter_unknown_key_warnings(
            front_matter
        )

        if "procedure" not in sections:
            promoted_procedure = _promote_procedure_from_freeform_sections(sections)
            if promoted_procedure:
                sections["procedure"] = promoted_procedure

        description = _front_matter_description(front_matter)
        short_description = _front_matter_short_description(front_matter)
        resolved_trust = self._resolve_bundle_trust(
            trust=trust,
            remote_source=remote_source,
        )
        companion_metadata = load_companion_metadata(
            bundle_root,
            trust=resolved_trust,
        )
        companion_warning = companion_metadata_unavailable_warning(companion_metadata)
        if companion_warning is not None:
            parse_warnings.append(companion_warning)
        if not str(sections.get("summary", "")).strip():
            summary_section = description or short_description
            if summary_section:
                sections["summary"] = summary_section

        if _summary_needs_fallback(summary):
            summary = short_description or description or summary

        name = str(front_matter.get("name", "")).strip() or explicit_name.strip()
        if not name:
            raise SkillError("INVALID_ARGUMENT", "Skill name must be non-empty")

        raw_skill_id = str(front_matter.get("id", "")).strip() or slugify(name)
        status = normalize_status(str(front_matter.get("status", SKILL_STATUS_DRAFT)))
        risk_class = normalize_risk(str(front_matter.get("risk", RISK_CLASS_LOW)))

        scope_norm = (scope or "global").strip().lower()
        if scope_norm not in {"global", "agent", "project"}:
            raise SkillError(
                "INVALID_ARGUMENT", "scope must be one of: global, agent, project"
            )

        front_tools = normalize_text_list(front_matter.get("tools"))
        bundle_tools = normalize_text_list(
            (companion_metadata.get("dependency_hints") or {}).get("tools")
        )
        section_tools: list[str] = []
        for value in sections.values():
            section_tools.extend(detect_tools(value))
        authoritative_tools = _dedupe(front_tools + bundle_tools)
        promoted_section_tools = [
            tool
            for tool in section_tools
            if _is_high_confidence_runtime_tool(
                tool,
                authoritative_tools=authoritative_tools,
                known_tools=self._known_tools,
            )
        ]
        tools = _dedupe(authoritative_tools + promoted_section_tools)
        reference_hints = _dedupe(
            [tool for tool in section_tools if tool not in set(tools)]
        )

        tags = normalize_text_list(front_matter.get("tags"))
        applies_to_value = front_matter.get("applies_to")
        applies_to_raw = cast(
            dict[str, Any],
            applies_to_value if isinstance(applies_to_value, dict) else {},
        )
        applies_to = {
            "intents": normalize_text_list(applies_to_raw.get("intents")),
            "steps": normalize_text_list(applies_to_raw.get("steps")),
        }

        inputs_schema = [
            item for item in front_matter.get("inputs", []) if isinstance(item, dict)
        ]
        snippets = build_default_snippets(sections)

        recipe = build_recipe(
            front_matter=front_matter,
            sections=sections,
            skill_name=name,
            risk_class=risk_class,
            known_tools=list(self._known_tools),
        )

        verification_rules = _dedupe(
            normalize_text_list(front_matter.get("verification"))
            + _extract_lines(sections.get("verification", ""))
        )
        rollback_hints = _dedupe(
            normalize_text_list(front_matter.get("rollback"))
            + _extract_lines(sections.get("rollback", ""))
        )

        now = iso_now()
        package = SkillPackage(
            skill_id=raw_skill_id,
            name=name,
            display_name=companion_metadata.get("display_name"),
            short_description=short_description
            or companion_metadata.get("short_description"),
            default_prompt=companion_metadata.get("default_prompt"),
            dependency_hints=dict(companion_metadata.get("dependency_hints") or {}),
            bundle_metadata=dict(companion_metadata.get("bundle_metadata") or {}),
            status=status,
            version_hash="",
            source_artifact_ref=source_artifact_ref,
            tags=tags,
            tools=tools,
            reference_hints=reference_hints,
            risk_class=risk_class,
            applies_to=applies_to,
            inputs_schema=inputs_schema,
            snippets=snippets,
            recipe=recipe,
            verification_rules=verification_rules,
            rollback_hints=rollback_hints,
            summary=summary,
            sections=sections,
            scope=scope_norm,
            agent_id=agent_id,
            source_version=str(front_matter.get("version"))
            if front_matter.get("version")
            else None,
            created_at=now,
            updated_at=now,
        )
        package.version_hash = package.to_version_hash()
        return package, parse_warnings

    def _lint_package(self, package: SkillPackage) -> list[LintIssue]:
        issues: list[LintIssue] = []

        if package.status not in SKILL_STATUSES:
            issues.append(
                LintIssue(
                    severity="error",
                    code="status.invalid",
                    message=f"status must be one of {sorted(SKILL_STATUSES)}",
                )
            )

        if package.scope == "agent" and not package.agent_id:
            issues.append(
                LintIssue(
                    severity="warning",
                    code="scope.agent_id_missing",
                    message="agent-scoped skills should set agent_id for predictable retrieval",
                )
            )

        if package.tools and self._known_tools_state != SKILL_TOOL_REGISTRY_UNAVAILABLE:
            unknown = sorted(set(package.tools).difference(self._known_tools))
            for tool in unknown:
                issues.append(
                    LintIssue(
                        severity="warning",
                        code="tool.unknown",
                        message=f"referenced tool not found in configured registry: {tool}",
                    )
                )

        combined = "\n".join(package.sections.values())
        if package.recipe:
            combined += "\n" + "\n".join(
                step.instruction for step in package.recipe.steps
            )
            combined += "\n" + "\n".join(package.recipe.rollback)
        dangerous_hits: list[str] = []
        for pattern in _DANGEROUS_PATTERNS:
            if pattern.search(combined):
                dangerous_hits.append(pattern.pattern)
        if dangerous_hits:
            issues.append(
                LintIssue(
                    severity="warning",
                    code="command.dangerous_detected",
                    message="dangerous command patterns detected in procedure/rollback",
                )
            )

        has_verification = bool(package.verification_rules)
        if package.recipe and package.recipe.verification:
            has_verification = True

        if package.risk_class in HIGH_RISK_CLASSES and not has_verification:
            issues.append(
                LintIssue(
                    severity="error",
                    code="verification.required",
                    message="medium/high risk skills must include verification rules",
                )
            )

        if package.status in VERIFIED_SKILL_STATUSES and not has_verification:
            issues.append(
                LintIssue(
                    severity="error",
                    code="status.requires_verification",
                    message="verified/blessed skills require verification evidence",
                )
            )

        if (
            package.risk_class == RISK_CLASS_HIGH
            and package.status == SKILL_STATUS_DRAFT
        ):
            issues.append(
                LintIssue(
                    severity="warning",
                    code="risk.high_draft",
                    message="high-risk draft skills should require explicit confirmation before side effects",
                )
            )

        if package.recipe is None and not package.sections.get("procedure"):
            issues.append(
                LintIssue(
                    severity="warning",
                    code="skill.procedure_missing",
                    message=(
                        "skill has no procedure section and no recipe; "
                        "render_snippet will return skeleton only. "
                        "Add a '# Procedure' section or use canonical headings."
                    ),
                )
            )

        return issues

    def _finalize_ingest(
        self,
        *,
        package: SkillPackage,
        parse_warnings: list[str],
        source_ref: str,
        scope: str,
        markdown: str,
        promotion_path: str,
        source_url: str | None = None,
    ) -> tuple[str, str, list[str]]:
        lint_issues = self._lint_package(package)
        errors = [item for item in lint_issues if item.severity == "error"]
        warnings = [item for item in lint_issues if item.severity != "error"]

        warning_msgs = list(parse_warnings)
        warning_msgs.extend(
            f"lint.{item.severity}:{item.code}:{item.message}" for item in warnings
        )
        warning_msgs.extend(f"lint.error:{item.code}:{item.message}" for item in errors)

        if errors and package.status != SKILL_STATUS_DRAFT:
            package.status = SKILL_STATUS_DRAFT
            package.updated_at = iso_now()
            package.version_hash = package.to_version_hash()
            warning_msgs.append("lint.forced_status_draft")

        previous_status = self._baseline_previous_status(package.skill_id)
        self._assert_trust_promotion_allowed(
            package=package,
            previous_status=previous_status,
            new_status=package.status,
            promotion_path=promotion_path,
            reviewer_id=None,
        )

        index_keywords = package.keyword_candidates()
        warning_msgs.extend(
            self._persist_package(package=package, index_keywords=index_keywords)
        )
        self._emit_untrusted_promotion_audit(
            package=package,
            previous_status=previous_status,
            new_status=package.status,
            promotion_path=promotion_path,
        )

        self._emit_event(
            "skill.ingested",
            {
                "skill_id": package.skill_id,
                "version_hash": package.version_hash,
                "source_ref": source_ref,
                "source_url": source_url,
                "scope": scope,
                "title": package.display_name or package.name,
                "tags": list(package.tags),
                "trust": str(package.bundle_metadata.get("trust") or ""),
                "text": markdown,
            },
        )

        return package.skill_id, package.version_hash, warning_msgs

    def _resolve_bundle_trust(
        self,
        *,
        trust: str | None,
        remote_source: bool,
    ) -> str:
        try:
            return resolve_bundle_metadata_trust(trust, remote=remote_source)
        except ValueError as exc:
            raise SkillError(
                "INVALID_ARGUMENT",
                str(exc),
                {"trust": trust, "remote_source": remote_source},
            ) from exc

    def _baseline_previous_status(self, skill_id: str) -> str:
        existing = self.store.get_skill_package(skill_id=skill_id, version_hash=None)
        if existing is None:
            return SKILL_STATUS_DRAFT
        return SkillPackage.from_dict(existing).status

    def _assert_trust_promotion_allowed(
        self,
        *,
        package: SkillPackage,
        previous_status: str,
        new_status: str,
        promotion_path: str,
        reviewer_id: str | None,
    ) -> None:
        if not _is_catalog_visible_promotion(previous_status, new_status):
            return
        trust = _bundle_trust(package)
        if trust != BUNDLE_METADATA_TRUST_UNTRUSTED_REMOTE:
            return
        normalized_path = str(promotion_path or "").strip().lower() or "runtime"
        if normalized_path in {"operator", "api"}:
            return
        normalized_reviewer = str(reviewer_id or "").strip().lower()
        if normalized_reviewer and normalized_reviewer not in _RUNTIME_REVIEWER_IDS:
            return
        raise SkillError(
            "INVALID_ARGUMENT",
            "reviewer_id must be operator-supplied",
            {
                "skill_id": package.skill_id,
                "trust": trust,
                "previous_status": previous_status,
                "new_status": new_status,
                "promotion_path": normalized_path,
            },
        )

    def _emit_untrusted_promotion_audit(
        self,
        *,
        package: SkillPackage,
        previous_status: str,
        new_status: str,
        promotion_path: str,
    ) -> None:
        if not _is_catalog_visible_promotion(previous_status, new_status):
            return
        trust = _bundle_trust(package)
        if trust not in {
            BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL,
            BUNDLE_METADATA_TRUST_UNTRUSTED_REMOTE,
        }:
            return
        self._emit_skill_operation(
            operation="untrusted_source_promotion",
            status="ok",
            extra={
                "skill_id": package.skill_id,
                "version_hash": package.version_hash,
                "trust": trust,
                "previous_status": previous_status,
                "new_status": new_status,
                "promotion_path": str(promotion_path or "").strip().lower()
                or "runtime",
            },
        )


def _source_ref_to_digest(ref: str) -> str | None:
    text = str(ref or "").strip()
    if not text.startswith(_ARTIFACT_REF_PREFIX):
        return None
    digest = text[len(_ARTIFACT_REF_PREFIX) :].strip()
    if not digest:
        return None
    return digest


def _source_ref_from_payload(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    ref = str(payload.get("source_artifact_ref") or "").strip()
    return ref or None


def _extract_lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("- ") or line.startswith("* "):
            line = line[2:].strip()
        out.append(line)
    return out


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _promote_procedure_from_freeform_sections(sections: dict[str, str]) -> str:
    blocks: list[str] = []

    body = str(sections.get("body", "")).strip()
    if body:
        blocks.append(body)

    for key, value in sections.items():
        if key == "body" or key in _CANONICAL_SECTION_KEYS:
            continue
        text = str(value).strip()
        if not text:
            continue
        blocks.append(f"{key.replace('_', ' ').title()}:\n{text}")

    return "\n\n".join(block for block in blocks if block.strip()).strip()


def _front_matter_description(front_matter: dict[str, Any]) -> str:
    return str(front_matter.get("description", "")).strip()


def _front_matter_short_description(front_matter: dict[str, Any]) -> str:
    metadata = front_matter.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("short-description", "")).strip()


def _summary_needs_fallback(summary: str) -> bool:
    stripped = str(summary or "").strip()
    if not stripped:
        return True
    if "\n" in stripped:
        return False
    return bool(_BARE_HEADING_RE.match(stripped))


def _is_probable_reference_hint(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    lowered = text.lower()
    if lowered in {"e.g", "i.e"}:
        return True
    if any(lowered.endswith(suffix) for suffix in _REFERENCE_FILE_SUFFIXES):
        return True
    if "/" in text or "\\" in text:
        return True
    parts = [part for part in text.split(".") if part]
    if any(part[:1].isupper() for part in parts):
        return True
    return False


def _is_high_confidence_runtime_tool(
    value: str,
    *,
    authoritative_tools: Iterable[str],
    known_tools: Iterable[str],
) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    authoritative = {
        str(item).strip() for item in authoritative_tools if str(item).strip()
    }
    if text in authoritative:
        return True
    if _is_probable_reference_hint(text):
        return False
    known = {str(item).strip() for item in known_tools if str(item).strip()}
    return text in known


def _bundle_trust(package: SkillPackage) -> str:
    bundle_metadata = (
        package.bundle_metadata if isinstance(package.bundle_metadata, dict) else {}
    )
    return str(bundle_metadata.get("trust") or "").strip().lower()


def _is_catalog_visible_promotion(previous_status: str, new_status: str) -> bool:
    previous = normalize_status(previous_status)
    current = normalize_status(new_status)
    if previous == current:
        return False
    return (
        (previous == SKILL_STATUS_DRAFT and current == SKILL_STATUS_VERIFIED)
        or (previous == SKILL_STATUS_VERIFIED and current == SKILL_STATUS_BLESSED)
        or (previous == SKILL_STATUS_DRAFT and current == SKILL_STATUS_BLESSED)
    )
