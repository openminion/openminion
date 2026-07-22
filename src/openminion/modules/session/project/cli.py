import uuid
from typing import Any
from collections.abc import Sequence

from openminion.modules.session.project.schemas import Project
from openminion.modules.session.project.storage.base import ProjectStore


_PROJECT_CLI_USAGE = (
    "/project list | show <id> | create <name> [--instruction TEXT] "
    "[--skill ID]* [--trigger CRON_ID]* | bind-session <project_id> <session_id>"
)


def dispatch_project_command(
    store: ProjectStore,
    argv: Sequence[str],
) -> dict[str, Any]:
    """Dispatch a `/project ...` subcommand and return a structural dict."""

    if not argv:
        return {"ok": False, "error": "usage", "usage": _PROJECT_CLI_USAGE}
    sub, rest = argv[0], list(argv[1:])
    handler = _DISPATCH.get(sub)
    if handler is None:
        return {
            "ok": False,
            "error": "unknown_subcommand",
            "subcommand": sub,
            "usage": _PROJECT_CLI_USAGE,
        }
    return handler(store, rest)


def _cmd_list(store: ProjectStore, _rest: list[str]) -> dict[str, Any]:
    return {"ok": True, "projects": [p.model_dump() for p in store.list()]}


def _cmd_show(store: ProjectStore, rest: list[str]) -> dict[str, Any]:
    if not rest:
        return {"ok": False, "error": "missing_project_id"}
    project = store.get(rest[0])
    if project is None:
        return {"ok": False, "error": "not_found", "project_id": rest[0]}
    bindings = store.list_bindings_for_project(project.project_id)
    return {
        "ok": True,
        "project": project.model_dump(),
        "bindings": [b.model_dump() for b in bindings],
    }


def _cmd_create(store: ProjectStore, rest: list[str]) -> dict[str, Any]:
    if not rest:
        return {"ok": False, "error": "missing_name"}
    name = rest[0]
    instruction = ""
    skill_set: list[str] = []
    triggers: list[str] = []
    i = 1
    while i < len(rest):
        flag = rest[i]
        if flag == "--instruction" and i + 1 < len(rest):
            instruction = rest[i + 1]
            i += 2
        elif flag == "--skill" and i + 1 < len(rest):
            skill_set.append(rest[i + 1])
            i += 2
        elif flag == "--trigger" and i + 1 < len(rest):
            triggers.append(rest[i + 1])
            i += 2
        else:
            return {"ok": False, "error": "unknown_flag", "flag": flag}
    project = Project(
        project_id=f"proj-{uuid.uuid4().hex[:12]}",
        name=name,
        master_instruction=instruction,
        skill_set=skill_set,
        scheduled_triggers=triggers,
    )
    store.create(project)
    return {"ok": True, "project": project.model_dump()}


def _cmd_bind_session(store: ProjectStore, rest: list[str]) -> dict[str, Any]:
    if len(rest) < 2:
        return {
            "ok": False,
            "error": "missing_args",
            "usage": "bind-session <project_id> <session_id>",
        }
    project_id, session_id = rest[0], rest[1]
    if store.get(project_id) is None:
        return {"ok": False, "error": "project_not_found", "project_id": project_id}
    binding = store.bind_session(project_id, session_id)
    return {"ok": True, "binding": binding.model_dump()}


_DISPATCH = {
    "list": _cmd_list,
    "show": _cmd_show,
    "create": _cmd_create,
    "bind-session": _cmd_bind_session,
}


__all__ = ["dispatch_project_command"]
