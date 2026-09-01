from dataclasses import FrozenInstanceError

import pytest

from openminion.modules.context.schemas import (
    ContextBudgets,
    ContextEvidenceItem,
    ContextEvidenceOmission,
    ContextEvidenceSource,
)
from openminion.modules.context.service import ContextCtlService


def _budgets() -> ContextBudgets:
    return ContextBudgets(
        total_max_tokens=120,
        identity_tokens=1,
        summary_tokens=1,
        recent_turn_tokens=1,
        facts_tokens=0,
        memory_tokens=40,
        skills_tokens=0,
        artifact_tokens=40,
        instructions_tokens=1,
    )


def _item(
    source_kind: ContextEvidenceSource,
    item_id: str,
    provenance_id: str,
    rendered_text: str,
    source_rank: int,
) -> ContextEvidenceItem:
    return ContextEvidenceItem(
        source_kind=source_kind,
        item_id=item_id,
        provenance_ids=(provenance_id,),
        citation_ids=(provenance_id,),
        rendered_text=rendered_text,
        estimated_tokens=max(1, len(rendered_text) // 4),
        source_rank=source_rank,
    )


def test_pack_evidence_keeps_complete_memory_winner_and_owned_omissions() -> None:
    memory = _item("memory", "memory-1", "shared", "記憶 evidence", 0)
    duplicate = _item("knowledge", "graph-1", "shared", "graph duplicate", 0)
    over_budget = _item("knowledge", "graph-2", "graph-2", "x" * 200, 1)
    authorization = ContextEvidenceOmission(
        source_kind="knowledge",
        item_id="graph-secret",
        provenance_ids=("graph-secret",),
        reason="authorization",
    )

    packed = ContextCtlService.pack_evidence_items(
        items=(duplicate, over_budget, memory),
        source_omissions=(authorization,),
        budgets=_budgets(),
    )

    assert packed.items == (memory,)
    assert packed.render_source("memory") == "記憶 evidence"
    assert packed.render_source("knowledge") == ""
    assert [(item.item_id, item.reason) for item in packed.omissions] == [
        ("graph-secret", "authorization"),
        ("graph-1", "duplicate"),
        ("graph-2", "budget"),
    ]
    assert packed.estimated_tokens <= 80
    with pytest.raises(FrozenInstanceError):
        setattr(memory, "item_id", "changed")


def test_pack_evidence_preserves_source_rank_without_cross_source_scores() -> None:
    items = (
        _item("knowledge", "knowledge-2", "knowledge-2", "knowledge two", 2),
        _item("memory", "memory-2", "memory-2", "memory two", 2),
        _item("knowledge", "knowledge-1", "knowledge-1", "knowledge one", 1),
        _item("memory", "memory-1", "memory-1", "memory one", 1),
    )

    packed = ContextCtlService.pack_evidence_items(items=items, budgets=_budgets())

    assert [item.item_id for item in packed.items] == [
        "memory-1",
        "memory-2",
        "knowledge-1",
        "knowledge-2",
    ]
