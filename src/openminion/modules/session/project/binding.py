"""Resolve project context inherited by sessions and cron deliveries."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

from openminion.modules.session.project.schemas import Project
from openminion.modules.session.project.storage.base import ProjectStore


@dataclass(frozen=True)
class ProjectSessionInheritance:
    """Typed inheritance payload for a session bound to a project."""

    project_id: str
    master_instruction: str
    skill_set: tuple[str, ...]
    scope_key: str
    scheduled_triggers: tuple[str, ...] = field(default_factory=tuple)


def resolve_inheritance(
    store: ProjectStore, *, session_id: str
) -> ProjectSessionInheritance | None:
    """Return typed inheritance for `session_id`, or None when unbound."""

    project = store.project_for_session(session_id)
    if project is None:
        return None
    return _project_to_inheritance(project)


def resolve_inheritance_for_project(
    store: ProjectStore, *, project_id: str
) -> ProjectSessionInheritance | None:
    """Return typed inheritance for `project_id` directly (cron + ad-hoc)."""

    project = store.get(project_id)
    if project is None:
        return None
    return _project_to_inheritance(project)


def resolve_installed_project_skills(
    inheritance: ProjectSessionInheritance,
    *,
    installed_skill_ids: Collection[str],
    requested_skill_ids: Sequence[str] = (),
) -> tuple[str, ...]:
    """Validate installed project skills and keep child requests in scope."""

    installed = {str(item).strip() for item in installed_skill_ids if str(item).strip()}
    missing = [item for item in inheritance.skill_set if item not in installed]
    if missing:
        raise ValueError(f"project references uninstalled skills: {', '.join(missing)}")
    allowed = tuple(inheritance.skill_set)
    requested = tuple(dict.fromkeys(str(item).strip() for item in requested_skill_ids))
    outside_project = [item for item in requested if item and item not in allowed]
    if outside_project:
        raise ValueError(
            f"requested skills exceed project scope: {', '.join(outside_project)}"
        )
    return requested or allowed


def _project_to_inheritance(project: Project) -> ProjectSessionInheritance:
    return ProjectSessionInheritance(
        project_id=project.project_id,
        master_instruction=project.master_instruction,
        skill_set=tuple(project.skill_set),
        scope_key=project.memory_scope_key(),
        scheduled_triggers=tuple(project.scheduled_triggers),
    )


__all__ = [
    "ProjectSessionInheritance",
    "resolve_inheritance",
    "resolve_inheritance_for_project",
    "resolve_installed_project_skills",
]
