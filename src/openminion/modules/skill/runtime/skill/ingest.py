from __future__ import annotations

import hashlib
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping, cast

from openminion.modules.skill.runtime.bundle_metadata import (
    BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL,
    BUNDLE_METADATA_TRUST_UNTRUSTED_REMOTE,
    agent_skills_conformance_warnings,
    agent_skills_metadata,
    companion_metadata_unavailable_warning,
    load_companion_metadata,
    resolve_bundle_metadata_trust,
)
from openminion.modules.skill.constants import (
    HIGH_RISK_CLASSES,
    RISK_CLASS_HIGH,
    RISK_CLASS_LOW,
    SKILL_BUNDLE_MAX_RESOURCES,
    SKILL_BUNDLE_MAX_RESOURCE_BYTES,
    SKILL_BUNDLE_MAX_TOTAL_RESOURCE_BYTES,
    SKILL_STATUSES,
    SKILL_STATUS_DRAFT,
    SKILL_TOOL_REGISTRY_UNAVAILABLE,
    VERIFIED_SKILL_STATUSES,
)
from openminion.modules.skill.errors import SkillError
from openminion.modules.skill.interfaces import SkillIngestAuthority
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


def _capability_metadata(front_matter: Mapping[str, Any]) -> dict[str, list[str]]:
    return {
        key: normalize_text_list(front_matter.get(key))
        for key in (
            "teaches",
            "requires_tools",
            "safe_for_domains",
            "forbidden_claims",
            "evidence_expectations",
        )
    }


def _package_tools(
    *,
    front_matter: Mapping[str, Any],
    sections: Mapping[str, str],
    companion_metadata: Mapping[str, Any],
    known_tools: Iterable[str],
) -> tuple[list[str], list[str]]:
    front_tools = normalize_text_list(front_matter.get("tools"))
    bundle_tools = normalize_text_list(
        (companion_metadata.get("dependency_hints") or {}).get("tools")
    )
    section_tools = [
        tool for value in sections.values() for tool in detect_tools(value)
    ]
    authoritative = _dedupe(front_tools + bundle_tools)
    promoted = [
        tool
        for tool in section_tools
        if _is_high_confidence_runtime_tool(
            tool,
            authoritative_tools=authoritative,
            known_tools=known_tools,
        )
    ]
    tools = _dedupe(authoritative + promoted)
    return tools, _dedupe([tool for tool in section_tools if tool not in set(tools)])


def _package_applies_to(front_matter: Mapping[str, Any]) -> dict[str, list[str]]:
    value = front_matter.get("applies_to")
    raw = cast(dict[str, Any], value if isinstance(value, dict) else {})
    return {
        "intents": normalize_text_list(raw.get("intents")),
        "steps": normalize_text_list(raw.get("steps")),
    }


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
        authority: SkillIngestAuthority | None = None,
    ) -> tuple[str, str, list[str]]:
        resolved_authority = authority or SkillIngestAuthority.runtime(
            surface="python.skill.ingest_text", source_kind="local"
        )
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
            authority=resolved_authority,
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
        authority: SkillIngestAuthority | None = None,
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
        resolved_authority = authority or SkillIngestAuthority.runtime(
            surface="python.skill.ingest_file", source_kind="local"
        )
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
                authority=resolved_authority,
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
        authority: SkillIngestAuthority | None = None,
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

        resolved_authority = authority or SkillIngestAuthority.runtime(
            surface="python.skill.ingest_artifact", source_kind="local"
        )
        return self._build_and_finalize_ingest(
            markdown=markdown,
            explicit_name=name,
            source_ref=source_artifact_ref,
            scope=scope,
            agent_id=agent_id,
            bundle_root=None,
            trust=trust,
            remote_source=False,
            authority=resolved_authority,
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
        authority: SkillIngestAuthority | None = None,
    ) -> tuple[str, str, list[str]]:
        resolved_authority = authority or SkillIngestAuthority.runtime(
            surface="python.skill.ingest_url", source_kind="remote"
        )
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
            source_url=url,
            authority=resolved_authority,
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
        authority: SkillIngestAuthority,
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
            authority=authority,
        )
        return self._finalize_ingest(
            package=package,
            parse_warnings=parse_warnings,
            source_ref=source_ref,
            scope=scope,
            markdown=markdown,
            source_url=source_url,
            authority=authority,
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

    def _collect_bundle_resources(
        self, bundle_root: Path | None
    ) -> tuple[list[dict[str, Any]], list[str]]:
        if bundle_root is None:
            return [], []
        root = bundle_root.resolve()
        resources: list[dict[str, Any]] = []
        warnings: list[str] = []
        total_bytes = 0
        for kind in ("references", "assets", "scripts"):
            resource_root = root / kind
            if not resource_root.is_dir():
                continue
            for path in sorted(resource_root.rglob("*")):
                if len(resources) >= SKILL_BUNDLE_MAX_RESOURCES:
                    warnings.append("bundle.resources.count_limit")
                    return resources, warnings
                if path.is_symlink() or not path.is_file():
                    if path.is_symlink():
                        warnings.append("bundle.resources.symlink_skipped")
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(root):
                    warnings.append("bundle.resources.path_escape_skipped")
                    continue
                size = resolved.stat().st_size
                if size > SKILL_BUNDLE_MAX_RESOURCE_BYTES:
                    warnings.append("bundle.resources.file_size_limit")
                    continue
                if total_bytes + size > SKILL_BUNDLE_MAX_TOTAL_RESOURCE_BYTES:
                    warnings.append("bundle.resources.total_size_limit")
                    return resources, warnings
                payload = resolved.read_bytes()
                media_type = (
                    mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
                )
                relative_path = resolved.relative_to(root).as_posix()
                ref = self._blob_store.put_bytes(
                    payload,
                    media_type=media_type,
                    ext=resolved.suffix.lstrip("."),
                    meta={"skill_resource_path": relative_path},
                )
                resources.append(
                    {
                        "path": relative_path,
                        "kind": kind,
                        "size_bytes": size,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "artifact_ref": f"artifact://sha256/{ref.hash}",
                        "executable": False,
                    }
                )
                total_bytes += size
        return resources, warnings

    def _persist_package(
        self,
        *,
        package: SkillPackage,
        index_keywords: list[str],
        admission_authority_class: str | None = None,
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
                content_fingerprint=package.to_content_fingerprint(),
            )
            self.store.upsert_skill_index(
                skill_id=package.skill_id,
                version_hash=package.version_hash,
                tags_json=canonical_json(package.tags),
                tools_json=canonical_json(package.tools),
                keywords_json=canonical_json(index_keywords),
                applies_to_json=canonical_json(package.applies_to),
            )
            if admission_authority_class is not None:
                self.store.stage_skill_version(
                    skill_id=package.skill_id,
                    version_hash=package.version_hash,
                    content_fingerprint=package.to_content_fingerprint(),
                    authority_class=admission_authority_class,
                    created_at=package.created_at,
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

    def _load_bundle_context(
        self,
        *,
        front_matter: Mapping[str, Any],
        bundle_root: Path | None,
        trust: str | None,
        remote_source: bool,
        authority: SkillIngestAuthority,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
        if authority.source_kind != ("remote" if remote_source else "local"):
            raise SkillError(
                "INVALID_ARGUMENT",
                "skill authority source_kind does not match ingest source",
            )
        if not authority.can_admit and trust is not None:
            raise SkillError(
                "SKILL_INGEST_AUTHORITY_OVERRIDE_REJECTED",
                "Runtime skill ingest cannot declare trust.",
                {"field_names": ["trust"], "surface": authority.surface},
            )
        resolved_trust = self._resolve_bundle_trust(
            trust=trust,
            remote_source=remote_source,
            authority=authority,
        )
        companion = load_companion_metadata(bundle_root, trust=resolved_trust)
        warnings: list[str] = []
        companion_warning = companion_metadata_unavailable_warning(companion)
        if companion_warning is not None:
            warnings.append(companion_warning)
        portable_metadata, metadata_warnings = agent_skills_metadata(front_matter)
        warnings.extend(metadata_warnings)
        if portable_metadata:
            companion.setdefault("bundle_metadata", {})["agent_skills"] = (
                portable_metadata
            )
        resources, resource_warnings = self._collect_bundle_resources(bundle_root)
        warnings.extend(resource_warnings)
        return companion, resources, warnings

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
        authority: SkillIngestAuthority,
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
        companion_metadata, resources, bundle_warnings = self._load_bundle_context(
            front_matter=front_matter,
            bundle_root=bundle_root,
            trust=trust,
            remote_source=remote_source,
            authority=authority,
        )
        parse_warnings.extend(bundle_warnings)
        if not str(sections.get("summary", "")).strip():
            summary_section = description or short_description
            if summary_section:
                sections["summary"] = summary_section

        if _summary_needs_fallback(summary):
            summary = short_description or description or summary

        name = str(front_matter.get("name", "")).strip() or explicit_name.strip()
        if not name:
            raise SkillError("INVALID_ARGUMENT", "Skill name must be non-empty")
        agent_skills_metadata = dict(
            companion_metadata.get("bundle_metadata", {}).get("agent_skills", {})
        )
        parse_warnings.extend(
            agent_skills_conformance_warnings(
                name=name,
                description=description,
                metadata=agent_skills_metadata,
                resources=resources,
            )
        )

        raw_skill_id = str(front_matter.get("id", "")).strip() or slugify(name)
        requested_status = normalize_status(
            str(front_matter.get("status", SKILL_STATUS_DRAFT))
        )
        if not authority.can_admit and requested_status != SKILL_STATUS_DRAFT:
            raise SkillError(
                "SKILL_INGEST_AUTHORITY_OVERRIDE_REJECTED",
                "Runtime skill ingest cannot declare catalog-visible status.",
                {"field_names": ["status"], "surface": authority.surface},
            )
        if "status" in front_matter:
            parse_warnings.append("frontmatter.status_non_authoritative")
        status = SKILL_STATUS_DRAFT
        risk_class = normalize_risk(str(front_matter.get("risk", RISK_CLASS_LOW)))

        scope_norm = (scope or "global").strip().lower()
        if scope_norm not in {"global", "agent", "project"}:
            raise SkillError(
                "INVALID_ARGUMENT", "scope must be one of: global, agent, project"
            )

        tools, reference_hints = _package_tools(
            front_matter=front_matter,
            sections=sections,
            companion_metadata=companion_metadata,
            known_tools=self._known_tools,
        )

        tags = normalize_text_list(front_matter.get("tags"))
        applies_to = _package_applies_to(front_matter)

        inputs_schema = [
            item for item in front_matter.get("inputs", []) if isinstance(item, dict)
        ]
        snippets = build_default_snippets(sections)

        recipe, recipe_warnings = build_recipe(
            front_matter=front_matter,
            skill_name=name,
            risk_class=risk_class,
            known_tools=list(self._known_tools),
        )
        parse_warnings.extend(recipe_warnings)

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
            resources=resources,
            **_capability_metadata(front_matter),
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
        if any(pattern.search(combined) for pattern in _DANGEROUS_PATTERNS):
            issues.append(
                LintIssue(
                    severity="warning",
                    code="command.dangerous_detected",
                    message="dangerous command patterns detected in procedure/rollback",
                )
            )

        has_verification = bool(
            package.verification_rules
            or (package.recipe and package.recipe.verification)
        )

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
        source_url: str | None = None,
        authority: SkillIngestAuthority,
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

        index_keywords = package.keyword_candidates()
        fingerprint = package.to_content_fingerprint()
        existing = self.store.find_skill_version_by_fingerprint(
            skill_id=package.skill_id,
            content_fingerprint=fingerprint,
        )
        if existing is not None:
            warning_msgs.append("admission.duplicate_content")
            return package.skill_id, str(existing["version_hash"]), warning_msgs
        warning_msgs.extend(
            self._persist_package(
                package=package,
                index_keywords=index_keywords,
                admission_authority_class=authority.authority_class,
            )
        )
        warning_msgs.append("admission.pending")

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
                "admission_state": "pending",
                "authority_class": authority.authority_class,
                "text": markdown,
            },
        )

        return package.skill_id, package.version_hash, warning_msgs

    def _resolve_bundle_trust(
        self,
        *,
        trust: str | None,
        remote_source: bool,
        authority: SkillIngestAuthority,
    ) -> str:
        if not authority.can_admit:
            return (
                BUNDLE_METADATA_TRUST_UNTRUSTED_REMOTE
                if remote_source
                else BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL
            )
        try:
            return resolve_bundle_metadata_trust(trust, remote=remote_source)
        except ValueError as exc:
            raise SkillError(
                "INVALID_ARGUMENT",
                str(exc),
                {"trust": trust, "remote_source": remote_source},
            ) from exc


def _source_ref_to_digest(ref: str) -> str | None:
    text = ref.strip()
    if not text.startswith(_ARTIFACT_REF_PREFIX):
        return None
    digest = text[len(_ARTIFACT_REF_PREFIX) :].strip()
    return digest or None


def _source_ref_from_payload(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    ref = str(payload.get("source_artifact_ref") or "").strip()
    return ref or None


def _extract_lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
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

    return "\n\n".join(blocks).strip()


def _front_matter_description(front_matter: dict[str, Any]) -> str:
    return str(front_matter.get("description", "")).strip()


def _front_matter_short_description(front_matter: dict[str, Any]) -> str:
    metadata = front_matter.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("short-description", "")).strip()


def _summary_needs_fallback(summary: str) -> bool:
    stripped = summary.strip()
    return (
        not stripped or "\n" not in stripped and bool(_BARE_HEADING_RE.match(stripped))
    )


def _is_probable_reference_hint(value: str) -> bool:
    text = value.strip()
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
    text = value.strip()
    if not text:
        return False
    authoritative = {item.strip() for item in authoritative_tools if item.strip()}
    if text in authoritative:
        return True
    if _is_probable_reference_hint(text):
        return False
    known = {item.strip() for item in known_tools if item.strip()}
    return text in known
