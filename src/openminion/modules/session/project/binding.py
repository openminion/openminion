"""Resolve project context inherited by sessions and cron deliveries."""

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
]
