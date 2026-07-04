from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.task.autonomy import now_ms


GAP_ASSESSMENT_REF = (
    "docs/specs/openminion-capability-surface-gap-assessment-2026-07-03-spec.md"
)
CPACK_SPEC_REF = "docs/specs/openminion-capability-pack-framework-2026-07-02-spec.md"
CPACK_TRACKER_REF = (
    "docs/trackers/wip/openminion-capability-pack-framework-2026-07-02-tracker.md"
)


class _StrictCapabilityModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCapabilityArea(StrEnum):
    CODE_EDITS = "code_edits"
    TESTS_AND_LINTERS = "tests_and_linters"
    DEEP_RESEARCH = "deep_research"
    BROWSER_RESEARCH = "browser_research"
    LOGGED_IN_BROWSER = "logged_in_browser"
    WEBSITE_APP_BUILD = "website_app_build"
    SPREADSHEET_DOCUMENTS = "spreadsheet_documents"
    GOOGLE_WORKSPACE = "google_workspace"
    EMAIL = "email"
    DESKTOP_APPS = "desktop_apps"
    TTS = "tts"
    IMAGE_INPUT = "image_input"
    SOCIAL_RESEARCH = "social_research"
    EXTERNAL_APIS = "external_apis"
    PACKAGE_MANAGERS = "package_managers"
    LONG_RUNNING_PROCESS = "long_running_process"


class ProjectCapabilitySupport(StrEnum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    MISSING = "missing"
    UNKNOWN = "unknown"


class ProjectCapabilityDisposition(StrEnum):
    AVAILABLE = "available"
    NOT_REQUIRED_FOR_PILOT = "not_required_for_pilot"
    BLOCKER = "blocked-capability-gap"
    DEFER_OWNED = "defer-owned"
    ALTERNATE_PLAN = "alternate-plan"
    PROBE_REQUIRED = "probe-required"


class ProjectCapabilityRow(_StrictCapabilityModel):
    area: ProjectCapabilityArea
    support: ProjectCapabilitySupport
    disposition: ProjectCapabilityDisposition
    needed_for_pilot: bool = False
    owner_ref: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    validation_plan: str = Field(min_length=1)
    blocker: str | None = None
    alternate_plan: str | None = None
    defer_owner: str | None = None
    risk: str | None = None


class ProjectCapabilityMatrix(_StrictCapabilityModel):
    project_run_id: str = Field(min_length=1)
    rows: tuple[ProjectCapabilityRow, ...] = Field(min_length=1)
    generated_at_ms: int = Field(default_factory=now_ms, ge=0)
    evidence_refs: tuple[str, ...] = (
        GAP_ASSESSMENT_REF,
        CPACK_SPEC_REF,
        CPACK_TRACKER_REF,
    )

    def row_for(self, area: ProjectCapabilityArea) -> ProjectCapabilityRow:
        for row in self.rows:
            if row.area == area:
                return row
        raise KeyError(area)


_GAP_ASSESSMENT_ROWS: dict[ProjectCapabilityArea, tuple[str, ProjectCapabilitySupport]] = {
    ProjectCapabilityArea.WEBSITE_APP_BUILD: (
        "Focus and tools can compose app builds, but there is no dedicated app-build loop owner.",
        ProjectCapabilitySupport.PARTIAL,
    ),
    ProjectCapabilityArea.DESKTOP_APPS: (
        "Native GUI automation has no first-class tool owner.",
        ProjectCapabilitySupport.MISSING,
    ),
    ProjectCapabilityArea.GOOGLE_WORKSPACE: (
        "The gws tool family exists, but depends on external setup and generic API workflows.",
        ProjectCapabilitySupport.PARTIAL,
    ),
    ProjectCapabilityArea.EMAIL: (
        "Gmail may be reachable through gws, but email is not a first-class workflow.",
        ProjectCapabilitySupport.PARTIAL,
    ),
    ProjectCapabilityArea.TTS: (
        "No first-class TTS tool family or audio output contract exists.",
        ProjectCapabilitySupport.MISSING,
    ),
    ProjectCapabilityArea.LOGGED_IN_BROWSER: (
        "Persistent browser profiles exist; logged-in research is still a composed workflow.",
        ProjectCapabilitySupport.PARTIAL,
    ),
    ProjectCapabilityArea.IMAGE_INPUT: (
        "Image path storage and ImageContentPart model wiring exist.",
        ProjectCapabilitySupport.SUPPORTED,
    ),
}

_PROJECT_ROWS: dict[ProjectCapabilityArea, tuple[str, ProjectCapabilitySupport]] = {
    ProjectCapabilityArea.CODE_EDITS: (
        "File edit, code search, and repo validation tools are first-class project-worker inputs.",
        ProjectCapabilitySupport.SUPPORTED,
    ),
    ProjectCapabilityArea.TESTS_AND_LINTERS: (
        "Focused pytest, Ruff, and Make gates are available for local project validation.",
        ProjectCapabilitySupport.SUPPORTED,
    ),
    ProjectCapabilityArea.DEEP_RESEARCH: (
        "Web/static research and local synthesis are available; authenticated/social flows need explicit routing.",
        ProjectCapabilitySupport.PARTIAL,
    ),
    ProjectCapabilityArea.BROWSER_RESEARCH: (
        "Browser automation substrate exists, with live-session workflow gaps tracked separately.",
        ProjectCapabilitySupport.PARTIAL,
    ),
    ProjectCapabilityArea.SPREADSHEET_DOCUMENTS: (
        "Document/spreadsheet file workflows exist; native desktop app automation is separate.",
        ProjectCapabilitySupport.PARTIAL,
    ),
    ProjectCapabilityArea.SOCIAL_RESEARCH: (
        "Social or X research requires browser/session policy and is not a standalone first-class lane.",
        ProjectCapabilitySupport.PARTIAL,
    ),
    ProjectCapabilityArea.EXTERNAL_APIS: (
        "External APIs can be reached through tools when credentials/policy are explicit.",
        ProjectCapabilitySupport.PARTIAL,
    ),
    ProjectCapabilityArea.PACKAGE_MANAGERS: (
        "Package-manager calls are possible through exec policy but need per-project permission.",
        ProjectCapabilitySupport.PARTIAL,
    ),
    ProjectCapabilityArea.LONG_RUNNING_PROCESS: (
        "Task lifecycle, checkpoints, and cron wakeups provide the durable process substrate.",
        ProjectCapabilitySupport.SUPPORTED,
    ),
}

_DEFAULT_PILOT_AREAS = frozenset(
    {
        ProjectCapabilityArea.CODE_EDITS,
        ProjectCapabilityArea.TESTS_AND_LINTERS,
        ProjectCapabilityArea.DEEP_RESEARCH,
        ProjectCapabilityArea.BROWSER_RESEARCH,
        ProjectCapabilityArea.IMAGE_INPUT,
        ProjectCapabilityArea.LONG_RUNNING_PROCESS,
    }
)

_CPACK_OWNED_AREAS = frozenset(
    {
        ProjectCapabilityArea.GOOGLE_WORKSPACE,
        ProjectCapabilityArea.EMAIL,
        ProjectCapabilityArea.EXTERNAL_APIS,
    }
)


def build_project_capability_matrix(
    project_run_id: str,
    *,
    pilot_areas: set[ProjectCapabilityArea] | None = None,
) -> ProjectCapabilityMatrix:
    active_pilot_areas = pilot_areas or set(_DEFAULT_PILOT_AREAS)
    rows: list[ProjectCapabilityRow] = []
    for area in ProjectCapabilityArea:
        truth, support = _capability_truth(area)
        needed = area in active_pilot_areas
        rows.append(
            ProjectCapabilityRow(
                area=area,
                support=support,
                disposition=_capability_disposition(
                    area,
                    support=support,
                    needed_for_pilot=needed,
                ),
                needed_for_pilot=needed,
                owner_ref=_capability_owner_ref(area),
                evidence_refs=_capability_evidence_refs(area),
                validation_plan=_capability_validation_plan(area, needed),
                blocker=_capability_blocker(area, support, needed),
                alternate_plan=_capability_alternate_plan(area, support, needed),
                defer_owner="CPACK" if area in _CPACK_OWNED_AREAS else None,
                risk=truth,
            )
        )
    return ProjectCapabilityMatrix(project_run_id=project_run_id, rows=tuple(rows))


def capability_rows_requiring_resolution(
    matrix: ProjectCapabilityMatrix,
) -> tuple[ProjectCapabilityRow, ...]:
    return tuple(
        row
        for row in matrix.rows
        if row.needed_for_pilot
        and row.disposition
        in {
            ProjectCapabilityDisposition.BLOCKER,
            ProjectCapabilityDisposition.DEFER_OWNED,
            ProjectCapabilityDisposition.PROBE_REQUIRED,
        }
    )


def render_project_capability_matrix(matrix: ProjectCapabilityMatrix) -> str:
    lines = [f"project_run_id: {matrix.project_run_id}", "capabilities:"]
    for row in matrix.rows:
        marker = "*" if row.needed_for_pilot else "-"
        suffix = f" -> {row.defer_owner}" if row.defer_owner else ""
        lines.append(
            f"{marker} {row.area.value}: {row.support.value} / "
            f"{row.disposition.value}{suffix}"
        )
        if row.blocker:
            lines.append(f"  blocker: {row.blocker}")
        if row.alternate_plan:
            lines.append(f"  alternate: {row.alternate_plan}")
    return "\n".join(lines)


def _capability_truth(
    area: ProjectCapabilityArea,
) -> tuple[str, ProjectCapabilitySupport]:
    return _GAP_ASSESSMENT_ROWS.get(area) or _PROJECT_ROWS.get(
        area,
        (
            "No current owner evidence was registered for this project capability.",
            ProjectCapabilitySupport.UNKNOWN,
        ),
    )


def _capability_disposition(
    area: ProjectCapabilityArea,
    *,
    support: ProjectCapabilitySupport,
    needed_for_pilot: bool,
) -> ProjectCapabilityDisposition:
    if not needed_for_pilot:
        return ProjectCapabilityDisposition.NOT_REQUIRED_FOR_PILOT
    if support == ProjectCapabilitySupport.SUPPORTED:
        return ProjectCapabilityDisposition.AVAILABLE
    if area in _CPACK_OWNED_AREAS:
        return ProjectCapabilityDisposition.DEFER_OWNED
    if support == ProjectCapabilitySupport.MISSING:
        return ProjectCapabilityDisposition.BLOCKER
    return ProjectCapabilityDisposition.ALTERNATE_PLAN


def _capability_owner_ref(area: ProjectCapabilityArea) -> str:
    if area in _GAP_ASSESSMENT_ROWS:
        return GAP_ASSESSMENT_REF
    if area in _CPACK_OWNED_AREAS:
        return CPACK_TRACKER_REF
    return "openminion.modules.task.project_capabilities"


def _capability_evidence_refs(area: ProjectCapabilityArea) -> tuple[str, ...]:
    refs = [GAP_ASSESSMENT_REF] if area in _GAP_ASSESSMENT_ROWS else []
    if area in _CPACK_OWNED_AREAS:
        refs.extend((CPACK_SPEC_REF, CPACK_TRACKER_REF))
    if not refs:
        refs.append("docs/specs/openminion-long-horizon-project-worker-v3-2026-07-03-spec.md")
    return tuple(refs)


def _capability_validation_plan(
    area: ProjectCapabilityArea,
    needed_for_pilot: bool,
) -> str:
    if not needed_for_pilot:
        return "Do not probe in this pilot; preserve explicit not-required disposition."
    if area in _CPACK_OWNED_AREAS:
        return "Route reusable workflow packaging to CPACK before live product claims."
    if area in _GAP_ASSESSMENT_ROWS:
        return "Use the capability gap assessment as baseline evidence and run a focused local probe only when this pilot needs it."
    return "Run focused local project-worker validation and attach evidence to the project report."


def _capability_blocker(
    area: ProjectCapabilityArea,
    support: ProjectCapabilitySupport,
    needed_for_pilot: bool,
) -> str | None:
    if not needed_for_pilot or support != ProjectCapabilitySupport.MISSING:
        return None
    return f"{area.value} is required for this pilot but has no first-class owner."


def _capability_alternate_plan(
    area: ProjectCapabilityArea,
    support: ProjectCapabilitySupport,
    needed_for_pilot: bool,
) -> str | None:
    if not needed_for_pilot or support == ProjectCapabilitySupport.SUPPORTED:
        return None
    if area in _CPACK_OWNED_AREAS:
        return "Use fixture/local proof or defer to CPACK-owned domain packaging."
    if area == ProjectCapabilityArea.DESKTOP_APPS:
        return "Use file or Google Workspace workflows instead of native GUI control."
    if area == ProjectCapabilityArea.TTS:
        return "Produce text/audio-artifact requirements only; do not claim speech output."
    return "Use the existing composed tool workflow and record the missing first-class lane."


__all__ = (
    "CPACK_SPEC_REF",
    "CPACK_TRACKER_REF",
    "GAP_ASSESSMENT_REF",
    "ProjectCapabilityArea",
    "ProjectCapabilityDisposition",
    "ProjectCapabilityMatrix",
    "ProjectCapabilityRow",
    "ProjectCapabilitySupport",
    "build_project_capability_matrix",
    "capability_rows_requiring_resolution",
    "render_project_capability_matrix",
)
