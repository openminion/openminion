from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from openminion.modules.session.project.binding import (
    ProjectSessionInheritance,
    resolve_inheritance_for_project,
)
from openminion.modules.session.project.storage.base import ProjectStore


@dataclass(frozen=True)
class CronProjectBinding:
    """Typed cron→project resolution for one scheduled delivery."""

    cron_entry_id: str
    project_id: str
    inheritance: ProjectSessionInheritance


def resolve_cron_project_binding(
    store: ProjectStore, cron_entry: Mapping[str, Any]
) -> CronProjectBinding | None:
    """Resolve a cron entry referencing `project_id` to a typed binding.

    Returns ``None`` when the cron entry doesn't reference a project or
    when the referenced project no longer exists (the caller treats this
    as "no project context for this delivery").
    """

    project_id = str(cron_entry.get("project_id") or "").strip()
    if not project_id:
        return None
    cron_entry_id = str(cron_entry.get("entry_id") or "").strip()
    inheritance = resolve_inheritance_for_project(store, project_id=project_id)
    if inheritance is None:
        return None
    return CronProjectBinding(
        cron_entry_id=cron_entry_id,
        project_id=project_id,
        inheritance=inheritance,
    )


__all__ = ["CronProjectBinding", "resolve_cron_project_binding"]
