from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from .models import (
    SkillPackage,
    SkillMatch,
    ToolRecipe,
    WorkflowCatalog,
    WorkflowCatalogEntry,
)


SKILL_INTERFACE_VERSION = "v1"


def ensure_skill_interface_compatibility(actual_version: str) -> bool:
    if actual_version != SKILL_INTERFACE_VERSION:
        raise ValueError(
            f"Skill interface version mismatch: expected {SKILL_INTERFACE_VERSION}, got {actual_version}"
        )
    return True


ArtifactIngestor = Callable[[str, str], str]
ArtifactLoader = Callable[[str], str | bytes]
SkillEventCallback = Callable[[str, dict[str, Any]], None]
StatusFilter = list[str] | str | None

SkillAuthorityClass = Literal[
    "runtime_untrusted",
    "local_operator",
    "authenticated_api_operator",
    "internal_service",
]
SkillSourceKind = Literal["local", "remote"]


@dataclass(frozen=True)
class SkillIngestAuthority:
    authority_class: SkillAuthorityClass
    surface: str
    source_kind: SkillSourceKind
    principal_id: str | None = None

    def __post_init__(self) -> None:
        surface = self.surface.strip()
        principal_id = str(self.principal_id or "").strip() or None
        if not surface:
            raise ValueError("surface must be non-empty")
        if self.authority_class != "runtime_untrusted" and principal_id is None:
            raise ValueError("privileged skill authority requires principal_id")
        object.__setattr__(self, "surface", surface)
        object.__setattr__(self, "principal_id", principal_id)

    @classmethod
    def runtime(
        cls, *, surface: str, source_kind: SkillSourceKind
    ) -> "SkillIngestAuthority":
        return cls(
            authority_class="runtime_untrusted",
            surface=surface,
            source_kind=source_kind,
        )

    @classmethod
    def local_operator(
        cls, *, surface: str, principal_id: str, source_kind: SkillSourceKind = "local"
    ) -> "SkillIngestAuthority":
        return cls(
            authority_class="local_operator",
            surface=surface,
            source_kind=source_kind,
            principal_id=principal_id,
        )

    @property
    def can_admit(self) -> bool:
        return self.authority_class in {
            "local_operator",
            "authenticated_api_operator",
            "internal_service",
        }


@dataclass(frozen=True)
class SkillVerificationEvidence:
    check: str
    result: Literal["passed"]
    evidence_ref: str

    def __post_init__(self) -> None:
        check = self.check.strip()
        evidence_ref = self.evidence_ref.strip()
        if not check or not evidence_ref or self.result != "passed":
            raise ValueError(
                "verification evidence requires check, passed result, and evidence_ref"
            )
        object.__setattr__(self, "check", check)
        object.__setattr__(self, "evidence_ref", evidence_ref)


class SkillContract(Protocol):
    def __init__(
        self,
        config: Any = ...,
        *,
        home_root: Path | None = ...,
        artifact_ingestor: ArtifactIngestor | None = ...,
        artifact_loader: ArtifactLoader | None = ...,
        known_tools: Iterable[str] | None = ...,
        event_callback: SkillEventCallback | None = ...,
    ) -> None: ...

    def close(self) -> None: ...

    def ingest_text(
        self,
        name: str,
        markdown: str,
        scope: str = ...,
        agent_id: str | None = ...,
        authority: SkillIngestAuthority | None = ...,
    ) -> tuple[str, str, list[str]]: ...

    def ingest_file(
        self,
        path: str | Path,
        *,
        name: str | None = ...,
        scope: str = ...,
        agent_id: str | None = ...,
        authority: SkillIngestAuthority | None = ...,
    ) -> tuple[str, str, list[str]]: ...

    def ingest_artifact(
        self,
        source_artifact_ref: str,
        *,
        name: str,
        scope: str = ...,
        agent_id: str | None = ...,
        authority: SkillIngestAuthority | None = ...,
    ) -> tuple[str, str, list[str]]: ...

    def ingest_url(
        self,
        url: str,
        *,
        name: str | None = ...,
        scope: str = ...,
        agent_id: str | None = ...,
        authority: SkillIngestAuthority | None = ...,
    ) -> tuple[str, str, list[str]]: ...

    def admit_skill_version(
        self,
        *,
        skill_id: str,
        version_hash: str,
        expected_active_version_hash: str | None,
        target_status: str,
        reason: str,
        authority: SkillIngestAuthority,
        verification_evidence: SkillVerificationEvidence | None = ...,
    ) -> dict[str, Any]: ...

    def rollback_skill_version(
        self,
        *,
        skill_id: str,
        to_version_hash: str,
        expected_active_version_hash: str,
        reason: str,
        authority: SkillIngestAuthority,
    ) -> dict[str, Any]: ...

    def match(
        self,
        intent_text: str,
        step_hint: dict[str, Any] | None,
        agent_id: str,
        k: int = ...,
        status_filter: StatusFilter = ...,
    ) -> list[SkillMatch]: ...

    def catalog_summaries(
        self,
        agent_id: str,
        status_filter: StatusFilter = ...,
    ) -> list[dict[str, Any]]: ...

    def get_skill(
        self, skill_id: str, version_hash: str | None = ...
    ) -> SkillPackage: ...

    def list_skills(
        self, filters: dict[str, Any] | None = ...
    ) -> list[dict[str, Any]]: ...

    def render_snippet(
        self,
        skill_id: str,
        version_hash: str | None,
        purpose: str,
        max_tokens: int,
        mode_name: str | None = None,
    ) -> tuple[str, str]: ...

    def get_recipe(
        self, skill_id: str, version_hash: str | None = ...
    ) -> ToolRecipe | None: ...

    def workflow_catalog(
        self,
        *,
        agent_id: str | None = ...,
        status_filter: StatusFilter = ...,
        scope: str | None = ...,
    ) -> WorkflowCatalog: ...

    def get_workflow(
        self,
        workflow_id: str,
        *,
        agent_id: str | None = ...,
        status_filter: StatusFilter = ...,
        scope: str | None = ...,
    ) -> WorkflowCatalogEntry: ...

    def lint(
        self, skill_id: str, version_hash: str | None = ...
    ) -> dict[str, list[dict[str, Any]]]: ...

    def log_run(
        self,
        session_id: str,
        agent_id: str,
        skill_id: str,
        version_hash: str,
        used_for: str,
        outcome: str,
        evidence_refs: list[str] | None = ...,
    ) -> str: ...
