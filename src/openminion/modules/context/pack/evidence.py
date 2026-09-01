"""Complete-item mapping and packing for memory and knowledge evidence."""

from typing import Any

from openminion.modules.context.knowledge.models import (
    GraphContextItem,
    GraphPathEvidence,
    GraphQueryResult,
    GraphSourceRef,
)
from openminion.modules.context.pack.budgeting import _estimate_tokens
from openminion.modules.context.schemas import (
    ContextBudgets,
    ContextEvidenceItem,
    ContextEvidenceOmission,
    ContextEvidencePack,
    default_budgets_for,
)
from openminion.modules.memory.surfacing.evidence import (
    MemoryRetrievalEvidenceSelection,
)
from openminion.modules.prompting.context_blocks import (
    DYNAMIC_MEMORY_BLOCK_HEADER,
    THIRD_BRAIN_GRAPH_CONTEXT_HEADER,
)


def pack_evidence_items(
    *,
    items: tuple[ContextEvidenceItem, ...],
    source_omissions: tuple[ContextEvidenceOmission, ...] = (),
    budgets: ContextBudgets | None = None,
) -> ContextEvidencePack:
    resolved_budgets = budgets or default_budgets_for("act")
    source_caps = {
        "memory": resolved_budgets.memory_tokens,
        "knowledge": resolved_budgets.artifact_tokens,
    }
    source_header_tokens = {
        "memory": _estimate_tokens(DYNAMIC_MEMORY_BLOCK_HEADER),
        "knowledge": _estimate_tokens(THIRD_BRAIN_GRAPH_CONTEXT_HEADER),
    }
    total_cap = min(resolved_budgets.total_max_tokens, sum(source_caps.values()))
    ordered = sorted(
        items,
        key=lambda item: (
            0 if item.source_kind == "memory" else 1,
            item.source_rank,
            item.item_id,
        ),
    )
    packed: list[ContextEvidenceItem] = []
    omissions = list(source_omissions)
    seen_provenance: set[str] = set()
    source_tokens = {"memory": 0, "knowledge": 0}
    packed_sources: set[str] = set()
    total_tokens = 0
    for item in ordered:
        provenance = set(item.provenance_ids)
        if provenance and provenance & seen_provenance:
            omissions.append(
                ContextEvidenceOmission(
                    source_kind=item.source_kind,
                    item_id=item.item_id,
                    provenance_ids=item.provenance_ids,
                    reason="duplicate",
                )
            )
            continue
        item_tokens = _estimate_tokens(item.rendered_text)
        header_tokens = (
            0
            if item.source_kind in packed_sources
            else source_header_tokens[item.source_kind]
        )
        packed_item_tokens = header_tokens + item_tokens
        if (
            source_tokens[item.source_kind] + packed_item_tokens
            > source_caps[item.source_kind]
            or total_tokens + packed_item_tokens > total_cap
        ):
            omissions.append(
                ContextEvidenceOmission(
                    source_kind=item.source_kind,
                    item_id=item.item_id,
                    provenance_ids=item.provenance_ids,
                    reason="budget",
                )
            )
            continue
        packed.append(item)
        packed_sources.add(item.source_kind)
        seen_provenance.update(provenance)
        source_tokens[item.source_kind] += packed_item_tokens
        total_tokens += packed_item_tokens
    return ContextEvidencePack(
        items=tuple(packed),
        omissions=tuple(omissions),
        estimated_tokens=total_tokens,
    )


def map_memory_evidence(
    selection: MemoryRetrievalEvidenceSelection,
) -> tuple[tuple[ContextEvidenceItem, ...], tuple[ContextEvidenceOmission, ...]]:
    items = tuple(
        ContextEvidenceItem(
            source_kind="memory",
            item_id=item.item_id,
            provenance_ids=item.provenance_ids,
            citation_ids=item.citation_ids,
            rendered_text=item.rendered_text,
            estimated_tokens=item.estimated_tokens,
            source_rank=item.source_rank,
            eligibility_facts=item.eligibility_facts,
        )
        for item in selection.items
    )
    omissions = tuple(
        ContextEvidenceOmission(
            source_kind="memory",
            item_id=omission.item_id,
            provenance_ids=omission.provenance_ids,
            reason=omission.reason,
            count=omission.count,
        )
        for omission in selection.omissions
    )
    return items, omissions


def _source_ref_text(source_ref: GraphSourceRef) -> str:
    path = str(source_ref.path or "").strip()
    if source_ref.line is not None:
        return f"{path}:L{source_ref.line}" if path else f"L{source_ref.line}"
    if source_ref.page is not None:
        return f"{path}:p{source_ref.page}" if path else f"p{source_ref.page}"
    if source_ref.span is not None:
        start, end = source_ref.span
        return f"{path}:{start}-{end}" if path else f"{start}-{end}"
    return path


def _graph_item_line(item: GraphContextItem) -> str:
    snippet = str(item.snippet or "").strip()
    source = _source_ref_text(item.source_ref)
    suffix = f" ({source})" if source else ""
    node_id = str(item.node_or_edge_id or "").strip()
    if snippet and node_id:
        return f"- {node_id}: {snippet}{suffix}"
    if snippet:
        return f"- {snippet}{suffix}"
    if node_id:
        return f"- {node_id}{suffix}"
    return ""


def _graph_path_line(path: GraphPathEvidence) -> str:
    node_ids = [
        str(node.node_or_edge_id or "").strip()
        for node in path.nodes
        if str(node.node_or_edge_id or "").strip()
    ]
    explanation = str(path.explanation or "").strip()
    if node_ids and explanation:
        return f"- path {' -> '.join(node_ids)}: {explanation}"
    if node_ids:
        return f"- path {' -> '.join(node_ids)}"
    if explanation:
        return f"- path: {explanation}"
    return ""


def map_knowledge_evidence(
    results: tuple[GraphQueryResult, ...],
) -> tuple[tuple[ContextEvidenceItem, ...], tuple[ContextEvidenceOmission, ...]]:
    items: list[ContextEvidenceItem] = []
    omissions: list[ContextEvidenceOmission] = []
    source_rank = 0
    for result in results:
        provider_header = f"Provider: {result.provider}"
        if result.tags:
            provider_header = f"{provider_header} ({', '.join(result.tags)})"
        for item in result.items:
            rendered_line = _graph_item_line(item)
            if not rendered_line:
                continue
            provenance = tuple(
                dict.fromkeys(
                    value
                    for value in (
                        str(item.node_or_edge_id or "").strip(),
                        _source_ref_text(item.source_ref),
                    )
                    if value
                )
            )
            item_id = (
                f"{result.provider}:{item.node_or_edge_id}"
                if item.node_or_edge_id
                else f"{result.provider}:{source_rank}"
            )
            rendered = f"{provider_header}\n{rendered_line}"
            items.append(
                ContextEvidenceItem(
                    source_kind="knowledge",
                    item_id=item_id,
                    provenance_ids=provenance or (item_id,),
                    citation_ids=provenance or (item_id,),
                    rendered_text=rendered,
                    estimated_tokens=max(1, len(rendered) // 4),
                    source_rank=source_rank,
                    eligibility_facts=("source_selected",),
                )
            )
            source_rank += 1
        for path in result.paths:
            rendered_line = _graph_path_line(path)
            if not rendered_line:
                continue
            node_ids = tuple(
                str(node.node_or_edge_id or "").strip()
                for node in path.nodes
                if str(node.node_or_edge_id or "").strip()
            )
            path_id = f"path:{result.provider}:{'->'.join(node_ids) or source_rank}"
            rendered = f"{provider_header}\n{rendered_line}"
            items.append(
                ContextEvidenceItem(
                    source_kind="knowledge",
                    item_id=path_id,
                    provenance_ids=(path_id,),
                    citation_ids=node_ids or (path_id,),
                    rendered_text=rendered,
                    estimated_tokens=max(1, len(rendered) // 4),
                    source_rank=source_rank,
                    eligibility_facts=("source_selected",),
                )
            )
            source_rank += 1
        omissions.extend(
            ContextEvidenceOmission(
                source_kind="knowledge",
                item_id=f"{result.provider}:{omission.node_or_edge_id}",
                provenance_ids=(omission.node_or_edge_id,)
                if omission.node_or_edge_id
                else (),
                reason=(
                    "source_budget"
                    if omission.reason == "budget"
                    else omission.reason or "relevance"
                ),
            )
            for omission in result.omitted
        )
    return tuple(items), tuple(omissions)


def pack_evidence_context(turn_context: Any) -> None:
    from openminion.modules.context.service import ContextCtlService

    turn_context.evidence_pack = ContextCtlService.pack_evidence_items(
        items=turn_context.evidence_items,
        source_omissions=turn_context.evidence_source_omissions,
    )
    memory_text = turn_context.evidence_pack.render_source("memory")
    knowledge_text = turn_context.evidence_pack.render_source("knowledge")
    if turn_context.memory_evidence_typed:
        turn_context.memory_retrieval_context = (
            f"{DYNAMIC_MEMORY_BLOCK_HEADER}\n{memory_text}" if memory_text else ""
        )
    if turn_context.knowledge_evidence_typed:
        turn_context.knowledge_graph_context = (
            f"{THIRD_BRAIN_GRAPH_CONTEXT_HEADER}\n{knowledge_text}"
            if knowledge_text
            else ""
        )
    duplicate_count = sum(
        omission.count
        for omission in turn_context.evidence_pack.omissions
        if omission.reason == "duplicate"
    )
    budget_count = sum(
        omission.count
        for omission in turn_context.evidence_pack.omissions
        if omission.reason == "budget"
    )
    if turn_context.memory_evidence_typed:
        turn_context.memory_retrieval_meta.update(
            evidence_duplicate_omissions=str(duplicate_count),
            evidence_budget_omissions=str(budget_count),
        )
    if turn_context.knowledge_evidence_typed:
        turn_context.knowledge_graph_meta.update(
            evidence_duplicate_omissions=str(duplicate_count),
            evidence_budget_omissions=str(budget_count),
        )


__all__ = [
    "map_knowledge_evidence",
    "map_memory_evidence",
    "pack_evidence_context",
    "pack_evidence_items",
]
