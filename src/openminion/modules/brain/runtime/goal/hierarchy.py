"""Project and traverse structural goal hierarchies."""

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ...schemas.decisions import GoalActionType, GoalPriority


class GoalHierarchyNode(BaseModel):
    """Typed projection of one declared-goal hierarchy record."""

    model_config = ConfigDict(extra="forbid")

    goal_id: str
    parent_goal_id: str | None = None
    depth: int = Field(default=0, ge=0)
    goal: str = ""
    action_type: GoalActionType = "suggest"
    priority: GoalPriority = "medium"

    @classmethod
    def from_record_content(
        cls, content: Mapping[str, Any] | None
    ) -> "GoalHierarchyNode | None":
        """Project a declared-goal content mapping into a typed node."""
        if not isinstance(content, Mapping):
            return None
        raw_goal_id = str(content.get("goal_id") or "").strip()
        if not raw_goal_id:
            return None
        raw_parent = str(content.get("parent_goal_id") or "").strip()
        parent_goal_id: str | None = raw_parent or None
        try:
            depth = max(0, int(content.get("depth") or 0))
        except (TypeError, ValueError):
            depth = 0
        action_type = str(content.get("action_type") or "suggest").strip() or "suggest"
        priority = str(content.get("priority") or "medium").strip() or "medium"
        if action_type not in {"watch", "task", "suggest", "none"}:
            action_type = "suggest"
        if priority not in {"low", "medium", "high"}:
            priority = "medium"
        return cls(
            goal_id=raw_goal_id,
            parent_goal_id=parent_goal_id,
            depth=depth,
            goal=str(content.get("goal") or "").strip(),
            action_type=action_type,  # type: ignore[arg-type]
            priority=priority,  # type: ignore[arg-type]
        )


def project_records_to_nodes(records: Iterable[Any]) -> list[GoalHierarchyNode]:
    """Project typed memory records into hierarchy nodes."""
    seen: set[str] = set()
    nodes: list[GoalHierarchyNode] = []
    for record in records or []:
        content: Mapping[str, Any] | None
        if isinstance(record, Mapping):
            inner = record.get("content")
            content = inner if isinstance(inner, Mapping) else record
        else:
            inner = getattr(record, "content", None)
            content = inner if isinstance(inner, Mapping) else None
        node = GoalHierarchyNode.from_record_content(content)
        if node is None:
            continue
        if node.goal_id in seen:
            continue
        seen.add(node.goal_id)
        nodes.append(node)
    return nodes


def list_child_goals(
    nodes: Iterable[GoalHierarchyNode], parent_goal_id: str
) -> list[GoalHierarchyNode]:
    """Return nodes whose `parent_goal_id` matches the requested parent."""
    target = str(parent_goal_id or "").strip()
    if not target:
        return []
    return [node for node in nodes if (node.parent_goal_id or "") == target]


def get_goal_ancestors(
    nodes: Iterable[GoalHierarchyNode],
    goal_id: str,
    *,
    max_depth: int = 32,
) -> list[GoalHierarchyNode]:
    """Walk the `parent_goal_id` chain from `goal_id` upward."""
    target = str(goal_id or "").strip()
    if not target:
        return []
    by_id: dict[str, GoalHierarchyNode] = {node.goal_id: node for node in nodes}
    current = by_id.get(target)
    if current is None:
        return []
    ancestors: list[GoalHierarchyNode] = []
    seen: set[str] = {current.goal_id}
    for _ in range(max(0, int(max_depth))):
        if not current.parent_goal_id:
            break
        parent_id = current.parent_goal_id
        if parent_id in seen:
            break
        parent = by_id.get(parent_id)
        if parent is None:
            break
        ancestors.append(parent)
        seen.add(parent_id)
        current = parent
    ancestors.reverse()
    return ancestors


def list_descendant_goals(
    nodes: Iterable[GoalHierarchyNode],
    root_goal_id: str,
    *,
    max_depth: int = 16,
) -> list[GoalHierarchyNode]:
    """Run BFS over children of `root_goal_id` and their descendants."""
    target = str(root_goal_id or "").strip()
    if not target:
        return []
    materialized = list(nodes)
    children_by_parent: dict[str, list[GoalHierarchyNode]] = {}
    for node in materialized:
        if node.parent_goal_id:
            children_by_parent.setdefault(node.parent_goal_id, []).append(node)
    descendants: list[GoalHierarchyNode] = []
    visited: set[str] = {target}
    frontier = list(children_by_parent.get(target, []))
    depth = 0
    while frontier and depth < max(0, int(max_depth)):
        next_frontier: list[GoalHierarchyNode] = []
        for node in frontier:
            if node.goal_id in visited:
                continue
            visited.add(node.goal_id)
            descendants.append(node)
            next_frontier.extend(children_by_parent.get(node.goal_id, []))
        frontier = next_frontier
        depth += 1
    return descendants


def compute_structural_depth(
    nodes: Iterable[GoalHierarchyNode],
    goal_id: str,
    *,
    max_depth: int = 32,
) -> int:
    """Compute the structural depth of `goal_id` by walking its parents."""
    target = str(goal_id or "").strip()
    if not target:
        return -1
    by_id: dict[str, GoalHierarchyNode] = {node.goal_id: node for node in nodes}
    current = by_id.get(target)
    if current is None:
        return -1
    depth = 0
    seen: set[str] = {current.goal_id}
    for _ in range(max(0, int(max_depth))):
        if not current.parent_goal_id:
            return depth
        parent = by_id.get(current.parent_goal_id)
        if parent is None or parent.goal_id in seen:
            return depth
        depth += 1
        seen.add(parent.goal_id)
        current = parent
    return depth


__all__ = [
    "GoalHierarchyNode",
    "project_records_to_nodes",
    "list_child_goals",
    "get_goal_ancestors",
    "list_descendant_goals",
    "compute_structural_depth",
]
