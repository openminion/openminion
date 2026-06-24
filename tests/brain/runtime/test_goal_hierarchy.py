from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.runtime.goal.hierarchy import (
    GoalHierarchyNode,
    compute_structural_depth,
    get_goal_ancestors,
    list_child_goals,
    list_descendant_goals,
    project_records_to_nodes,
)
from openminion.modules.brain.schemas.decisions import GoalDeclaration


def _node(
    goal_id: str,
    *,
    parent: str | None = None,
    depth: int = 0,
    goal: str = "",
) -> GoalHierarchyNode:
    return GoalHierarchyNode(
        goal_id=goal_id,
        parent_goal_id=parent,
        depth=depth,
        goal=goal or f"goal-{goal_id}",
    )


# --- GoalHierarchyNode.from_record_content ---------------------------------


def test_from_record_content_projects_full_declared_goal_content() -> None:
    node = GoalHierarchyNode.from_record_content(
        {
            "goal_id": "g-root",
            "parent_goal_id": None,
            "depth": 0,
            "goal": "monitor deployment health",
            "trigger": "recent failures",
            "priority": "high",
            "action_type": "watch",
        }
    )
    assert node is not None
    assert node.goal_id == "g-root"
    assert node.parent_goal_id is None
    assert node.depth == 0
    assert node.goal == "monitor deployment health"
    assert node.priority == "high"
    assert node.action_type == "watch"


def test_from_record_content_returns_none_when_goal_id_missing() -> None:
    assert GoalHierarchyNode.from_record_content({"depth": 1}) is None
    assert GoalHierarchyNode.from_record_content({"goal_id": ""}) is None
    assert GoalHierarchyNode.from_record_content({"goal_id": "   "}) is None
    assert GoalHierarchyNode.from_record_content(None) is None


def test_from_record_content_normalizes_blank_parent_to_none_and_coerces_depth() -> (
    None
):
    node = GoalHierarchyNode.from_record_content(
        {"goal_id": "g-1", "parent_goal_id": "   ", "depth": "3"}
    )
    assert node is not None
    assert node.parent_goal_id is None
    assert node.depth == 3


def test_from_record_content_falls_back_on_corrupt_literals() -> None:
    node = GoalHierarchyNode.from_record_content(
        {
            "goal_id": "g-1",
            "priority": "ultra-high",  # not in {low, medium, high}
            "action_type": "destroy",  # not in {watch, task, suggest, none}
        }
    )
    assert node is not None
    assert node.priority == "medium"
    assert node.action_type == "suggest"


# --- project_records_to_nodes (bridge) -------------------------------------


def test_project_records_to_nodes_accepts_objects_with_content_attr() -> None:
    records = [
        SimpleNamespace(content={"goal_id": "g-a", "depth": 0}),
        SimpleNamespace(
            content={"goal_id": "g-b", "parent_goal_id": "g-a", "depth": 1}
        ),
        SimpleNamespace(content=None),  # malformed → skipped
        SimpleNamespace(),  # no content attr → skipped
    ]
    nodes = project_records_to_nodes(records)
    assert [n.goal_id for n in nodes] == ["g-a", "g-b"]
    assert nodes[1].parent_goal_id == "g-a"


def test_project_records_to_nodes_accepts_plain_mappings() -> None:
    records = [
        {"content": {"goal_id": "g-a", "depth": 0}},
        {"goal_id": "g-b", "parent_goal_id": "g-a", "depth": 1},
    ]
    nodes = project_records_to_nodes(records)
    assert [n.goal_id for n in nodes] == ["g-a", "g-b"]


def test_project_records_to_nodes_dedups_on_goal_id() -> None:
    records = [
        SimpleNamespace(content={"goal_id": "g-a", "depth": 0}),
        SimpleNamespace(content={"goal_id": "g-a", "depth": 99}),  # duplicate
    ]
    nodes = project_records_to_nodes(records)
    assert len(nodes) == 1
    assert nodes[0].depth == 0  # first occurrence wins


# --- list_child_goals ------------------------------------------------------


def test_list_child_goals_returns_only_matching_parent() -> None:
    nodes = [
        _node("g-root"),
        _node("g-a", parent="g-root", depth=1),
        _node("g-b", parent="g-root", depth=1),
        _node("g-c", parent="g-a", depth=2),
    ]
    children = list_child_goals(nodes, "g-root")
    assert [n.goal_id for n in children] == ["g-a", "g-b"]


def test_list_child_goals_empty_for_unknown_or_blank_parent() -> None:
    nodes = [_node("g-a", parent="g-root")]
    assert list_child_goals(nodes, "") == []
    assert list_child_goals(nodes, "g-not-here") == []


# --- get_goal_ancestors ----------------------------------------------------


def test_get_goal_ancestors_returns_root_to_direct_parent_order() -> None:
    nodes = [
        _node("g-root"),
        _node("g-a", parent="g-root", depth=1),
        _node("g-b", parent="g-a", depth=2),
        _node("g-c", parent="g-b", depth=3),
    ]
    ancestors = get_goal_ancestors(nodes, "g-c")
    assert [n.goal_id for n in ancestors] == ["g-root", "g-a", "g-b"]


def test_get_goal_ancestors_stops_at_missing_parent() -> None:
    nodes = [
        _node("g-b", parent="g-a", depth=2),  # g-a not in nodes
        _node("g-c", parent="g-b", depth=3),
    ]
    ancestors = get_goal_ancestors(nodes, "g-c")
    assert [n.goal_id for n in ancestors] == ["g-b"]


def test_get_goal_ancestors_stops_at_cycle() -> None:
    nodes = [
        _node("g-a", parent="g-b"),
        _node("g-b", parent="g-a"),
    ]
    ancestors = get_goal_ancestors(nodes, "g-a")
    # g-a -> parent g-b (added) -> parent g-a (cycle, stop)
    assert [n.goal_id for n in ancestors] == ["g-b"]


def test_get_goal_ancestors_respects_max_depth_bound() -> None:
    chain = [_node("g-0")]
    for i in range(1, 6):
        chain.append(_node(f"g-{i}", parent=f"g-{i - 1}", depth=i))
    ancestors = get_goal_ancestors(chain, "g-5", max_depth=2)
    # Walks at most 2 hops, in root→parent order.
    assert [n.goal_id for n in ancestors] == ["g-3", "g-4"]


# --- list_descendant_goals -------------------------------------------------


def test_list_descendant_goals_bfs_order() -> None:
    nodes = [
        _node("g-root"),
        _node("g-a", parent="g-root", depth=1),
        _node("g-b", parent="g-root", depth=1),
        _node("g-c", parent="g-a", depth=2),
        _node("g-d", parent="g-b", depth=2),
    ]
    descendants = list_descendant_goals(nodes, "g-root")
    assert [n.goal_id for n in descendants] == ["g-a", "g-b", "g-c", "g-d"]


def test_list_descendant_goals_excludes_root_and_unrelated_subtrees() -> None:
    nodes = [
        _node("g-root"),
        _node("g-a", parent="g-root", depth=1),
        _node("g-other-root"),
        _node("g-x", parent="g-other-root", depth=1),
    ]
    descendants = list_descendant_goals(nodes, "g-root")
    assert [n.goal_id for n in descendants] == ["g-a"]


def test_list_descendant_goals_respects_max_depth() -> None:
    chain = [_node("g-0")]
    for i in range(1, 5):
        chain.append(_node(f"g-{i}", parent=f"g-{i - 1}", depth=i))
    descendants = list_descendant_goals(chain, "g-0", max_depth=2)
    assert [n.goal_id for n in descendants] == ["g-1", "g-2"]


# --- compute_structural_depth ----------------------------------------------


def test_compute_structural_depth_matches_persisted_for_well_formed_chain() -> None:
    nodes = [
        _node("g-0", depth=0),
        _node("g-1", parent="g-0", depth=1),
        _node("g-2", parent="g-1", depth=2),
        _node("g-3", parent="g-2", depth=3),
    ]
    for node in nodes:
        assert compute_structural_depth(nodes, node.goal_id) == node.depth


def test_compute_structural_depth_returns_minus_one_for_unknown_id() -> None:
    assert compute_structural_depth([_node("g-0")], "") == -1
    assert compute_structural_depth([_node("g-0")], "g-missing") == -1


def test_compute_structural_depth_handles_cycle_without_inflating() -> None:
    nodes = [_node("g-a", parent="g-b"), _node("g-b", parent="g-a")]
    # g-a -> g-b (depth=1) -> g-a (cycle, stop)
    assert compute_structural_depth(nodes, "g-a") == 1


# --- contract bridge to GoalDeclaration ------------------------------------


def test_goal_declaration_round_trips_through_goal_hierarchy_node() -> None:
    declaration = GoalDeclaration(
        goal_id="g-decl-1",
        parent_goal_id="g-parent",
        depth=2,
        goal="monitor deployment health daily",
        trigger="recent tool failures",
        priority="high",
        action_type="watch",
    )
    # Memory record content shape mirrors what ``memory_writer.stage_declared_goal``
    # actually writes (see ``content={...}`` in that helper).
    record_content = {
        "goal_id": declaration.goal_id,
        "parent_goal_id": declaration.parent_goal_id,
        "depth": declaration.depth,
        "goal": declaration.goal,
        "trigger": declaration.trigger,
        "priority": declaration.priority,
        "action_type": declaration.action_type,
        "suggested_schedule": declaration.suggested_schedule,
    }
    node = GoalHierarchyNode.from_record_content(record_content)
    assert node is not None
    assert node.goal_id == "g-decl-1"
    assert node.parent_goal_id == "g-parent"
    assert node.depth == 2
    assert node.priority == "high"
    assert node.action_type == "watch"
    assert node.goal == "monitor deployment health daily"


def test_goal_declaration_contract_pins_hierarchy_field_names() -> None:
    fields = set(GoalDeclaration.model_fields.keys())
    assert "goal_id" in fields
    assert "parent_goal_id" in fields
    assert "depth" in fields
