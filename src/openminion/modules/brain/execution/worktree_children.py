"""Worktree isolation bridge for code-bearing orchestrate children."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openminion.modules.brain.constants import STATE_KEY_MODULE_STATE
from openminion.modules.brain.execution.child_tasks import SubtaskSpec
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.loop.rollouts import WorktreeIsolator
from openminion.modules.brain.schemas import WorkingState

_MODULE_STATE_KEY = "worktree_children"
_CHILD_STATE_KEY = "worktree_child"


@dataclass(slots=True)
class ChildWorktreeLease:
    isolator: WorktreeIsolator
    worktree: Path
    subtask_id: str
    base_revision: str


def _module_bucket(state: WorkingState) -> dict[str, Any]:
    module_state = getattr(state, STATE_KEY_MODULE_STATE, None)
    if not isinstance(module_state, dict):
        module_state = {}
        setattr(state, STATE_KEY_MODULE_STATE, module_state)
    bucket = module_state.get(_MODULE_STATE_KEY)
    if not isinstance(bucket, dict):
        bucket = {"version": 1, "children": [], "conflicts": []}
        module_state[_MODULE_STATE_KEY] = bucket
    bucket.setdefault("children", [])
    bucket.setdefault("conflicts", [])
    return bucket


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _status_paths(worktree: Path) -> list[str]:
    result = _git(worktree, "status", "--short")
    paths: list[str] = []
    for line in result.stdout.splitlines():
        path = line[3:].strip()
        if path:
            paths.append(path)
    return sorted(dict.fromkeys(paths))


def _diff_text(worktree: Path) -> str:
    result = _git(worktree, "diff", "--")
    return result.stdout if result.returncode == 0 else ""


def _record_conflicts(bucket: dict[str, Any]) -> None:
    by_path: dict[str, list[str]] = {}
    for child in list(bucket.get("children", []) or []):
        child_id = str(child.get("subtask_id") or "")
        for path in list(child.get("touched_paths", []) or []):
            by_path.setdefault(str(path), []).append(child_id)
    bucket["conflicts"] = [
        {"path": path, "subtask_ids": ids}
        for path, ids in sorted(by_path.items())
        if len(ids) > 1
    ]


def allocate_child_worktree(
    *,
    subtask: SubtaskSpec,
    child_state: WorkingState,
) -> ChildWorktreeLease | None:
    inputs = subtask.inputs if isinstance(subtask.inputs, dict) else {}
    if not bool(inputs.get("code_bearing") or inputs.get("worktree_required")):
        return None
    workspace_root = str(inputs.get("workspace_root") or "").strip()
    if not workspace_root:
        raise ValueError("code-bearing orchestrate subtask requires inputs.workspace_root")
    revision = str(inputs.get("base_revision") or "HEAD").strip() or "HEAD"
    isolator = WorktreeIsolator(parent_root=Path(workspace_root), revision=revision)
    worktree = isolator.allocate(1)[0]
    base_result = _git(Path(workspace_root), "rev-parse", revision)
    base_revision = base_result.stdout.strip() if base_result.returncode == 0 else revision
    _module_bucket(child_state)[_CHILD_STATE_KEY] = {
        "workspace": str(worktree),
        "base_revision": base_revision,
        "subtask_id": subtask.subtask_id,
    }
    return ChildWorktreeLease(
        isolator=isolator,
        worktree=worktree,
        subtask_id=subtask.subtask_id,
        base_revision=base_revision,
    )


def finalize_child_worktree(
    ctx: ExecutionContext,
    *,
    lease: ChildWorktreeLease | None,
    status: str,
    validation: dict[str, Any] | None = None,
) -> None:
    if lease is None:
        return
    touched_paths = _status_paths(lease.worktree)
    child_record = {
        "subtask_id": lease.subtask_id,
        "base_revision": lease.base_revision,
        "workspace": str(lease.worktree),
        "touched_paths": touched_paths,
        "diff": _diff_text(lease.worktree),
        "validation": dict(validation or {}),
        "status": status,
        "integration_status": "pending_parent_review" if touched_paths else "read_only",
    }
    lease.isolator.release()
    child_record["cleaned_up"] = not lease.worktree.exists()
    bucket = _module_bucket(ctx.state)
    bucket["children"].append(child_record)
    _record_conflicts(bucket)


__all__ = [
    "ChildWorktreeLease",
    "allocate_child_worktree",
    "finalize_child_worktree",
]
