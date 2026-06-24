from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Protocol

from .models import (
    SkillPackage,
    SkillMatch,
    ToolRecipe,
    WorkflowCatalog,
    WorkflowCatalogEntry,
)


SKILL_INTERFACE_VERSION = "v1"


def ensure_skill_interface_compatibility(actual_version: str) -> bool:
    """Validate that actual interface version is compatible with expected version."""
    if actual_version == SKILL_INTERFACE_VERSION:
        return True
    raise ValueError(
        f"Skill interface version mismatch: expected {SKILL_INTERFACE_VERSION}, got {actual_version}"
    )


ArtifactIngestor = Callable[[str, str], str]
ArtifactLoader = Callable[[str], str | bytes]
SkillEventCallback = Callable[[str, dict[str, Any]], None]
StatusFilter = list[str] | str | None


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
    ) -> tuple[str, str, list[str]]: ...

    def ingest_file(
        self,
        path: str | Path,
        *,
        name: str | None = ...,
        scope: str = ...,
        agent_id: str | None = ...,
    ) -> tuple[str, str, list[str]]: ...

    def ingest_artifact(
        self,
        source_artifact_ref: str,
        *,
        name: str,
        scope: str = ...,
        agent_id: str | None = ...,
    ) -> tuple[str, str, list[str]]: ...

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
