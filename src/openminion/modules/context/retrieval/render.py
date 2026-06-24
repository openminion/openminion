"""Segment appenders for retrieval assembly."""

from typing import Any

from ..input_boundaries import emit_boundary_event as _pidf_emit_boundary_event
from ..schemas import BuildConstraints

from .bundle import _SegmentAssemblyRetrievalBundle
from .cards import (
    _render_decision_memory_cards,
    _render_improvement_note_cards,
    _render_post_completion_critique_cards,
    _render_strategy_outcome_cards,
)


def _render_low_progress_signal(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict) or not payload:
        return ""
    return "\n".join(
        [
            f"- iterations_without_new_typed_record={int(payload.get('iterations_without_new_typed_record', 0) or 0)}",
            f"- repeated_arg_signature_count={int(payload.get('repeated_arg_signature_count', 0) or 0)}",
            f"- unique_tool_call_count_delta={int(payload.get('unique_tool_call_count_delta', 0) or 0)}",
        ]
    )


def _append_text_segment(
    runtime: Any,
    *,
    segment_id: str,
    section_key: str,
    header: str,
    text: str,
    token_limit: int,
    refs: list[str] | None = None,
    boundary_kind: str | None = None,
    seam_id: str | None = None,
) -> None:
    fitted = runtime.fit_section(section_key, text, token_limit)
    if not fitted.strip():
        return
    if boundary_kind and seam_id:
        _pidf_emit_boundary_event(boundary_kind, fitted, seam_id=seam_id)
    runtime.segments.append(
        runtime.make(
            segment_id,
            "retrieval",
            f"{header}\n{fitted}",
            refs=refs,
        )
    )


def _append_rlm_and_vector_segments(
    runtime: Any,
    bundle: _SegmentAssemblyRetrievalBundle,
) -> None:
    if bundle.rlm_summary:
        _append_text_segment(
            runtime,
            segment_id="retrieval:rlm_refresh",
            section_key="retrieval_rlm",
            header="[RLM REFRESH]",
            text=bundle.rlm_summary,
            token_limit=200,
        )
    if not bundle.vector_results:
        return
    vec_lines = ["Semantic matches:"]
    for vec_id, score, meta in bundle.vector_results[:5]:
        vec_lines.append(f"- [{score:.2f}] {meta.get('record_id', vec_id)}")
    _append_text_segment(
        runtime,
        segment_id="retrieval:vector",
        section_key="retrieval_vector",
        header="[SEMANTIC SEARCH]",
        text="\n".join(vec_lines),
        token_limit=300,
        refs=[
            meta.get("record_id", vec_id)
            for vec_id, _, meta in bundle.vector_results[:5]
        ],
    )


def _append_fact_and_memory_segments(
    runtime: Any,
    bundle: _SegmentAssemblyRetrievalBundle,
) -> None:
    if bundle.capped_facts:
        fact_lines = ["Facts:"] + [
            f"- ({fact.record_id}) {fact.text}" for fact in bundle.capped_facts
        ]
        _append_text_segment(
            runtime,
            segment_id="retrieval:facts",
            section_key="retrieval_facts",
            header="[FACTS TABLE]",
            text="\n".join(fact_lines),
            token_limit=runtime.budgets.facts_tokens,
            refs=[fact.record_id for fact in bundle.capped_facts],
        )
    if bundle.capped_memory:
        mem_lines = ["Memory cards:"] + [
            f"- ({item.record_type}{'  pinned' if item.pinned else ''}) ({item.record_id}) {item.text}"
            for item in bundle.capped_memory
        ]
        _append_text_segment(
            runtime,
            segment_id="retrieval:memory",
            section_key="retrieval_memory",
            header="[MEMORY CARDS]",
            text="\n".join(mem_lines),
            token_limit=runtime.budgets.memory_tokens,
            refs=[item.record_id for item in bundle.capped_memory],
            boundary_kind="memory_recall",
            seam_id="modules.context.segment_assembly.memory_cards",
        )


def _append_special_memory_segments(
    runtime: Any,
    bundle: _SegmentAssemblyRetrievalBundle,
) -> None:
    special_segments = [
        (
            bundle.capped_decision_memory,
            "retrieval:decisions",
            "retrieval_decisions",
            "[DECISION MEMORY]",
            _render_decision_memory_cards,
            "modules.context.segment_assembly.decision_memory",
        ),
        (
            bundle.capped_improvement_notes,
            "retrieval:improvement_notes",
            "retrieval_improvement_notes",
            "[IMPROVEMENT NOTES]",
            _render_improvement_note_cards,
            "modules.context.segment_assembly.improvement_notes",
        ),
        (
            bundle.capped_strategy_outcomes,
            "retrieval:strategy_outcomes",
            "retrieval_strategy_outcomes",
            "[STRATEGY OUTCOMES]",
            _render_strategy_outcome_cards,
            "modules.context.segment_assembly.strategy_outcomes",
        ),
        (
            bundle.capped_post_completion_critiques,
            "retrieval:post_completion_critiques",
            "retrieval_post_completion_critiques",
            "[POST-COMPLETION CRITIQUES]",
            _render_post_completion_critique_cards,
            "modules.context.segment_assembly.post_completion_critique",
        ),
    ]
    for cards, segment_id, section_key, header, render, seam_id in special_segments:
        if cards:
            _append_text_segment(
                runtime,
                segment_id=segment_id,
                section_key=section_key,
                header=header,
                text=render(cards),
                token_limit=runtime.budgets.memory_tokens,
                refs=[card.record_id for card in cards],
                boundary_kind="memory_recall",
                seam_id=seam_id,
            )


def _append_low_progress_segment(
    runtime: Any,
    bundle: _SegmentAssemblyRetrievalBundle,
) -> None:
    if bundle.low_progress_signal:
        _append_text_segment(
            runtime,
            segment_id="retrieval:low_progress_signal",
            section_key="retrieval_low_progress_signal",
            header="[LOW PROGRESS SIGNAL]",
            text=_render_low_progress_signal(bundle.low_progress_signal),
            token_limit=runtime.budgets.memory_tokens,
        )


def _append_skill_or_procedure_segment(
    runtime: Any,
    *,
    constraints: BuildConstraints,
    skill_snippet_text: str | None,
    procedure: Any,
) -> None:
    if skill_snippet_text:
        _append_text_segment(
            runtime,
            segment_id=f"retrieval:skill:{constraints.skill_id}",
            section_key="retrieval_skill",
            header="[SKILL SNIPPET]",
            text=skill_snippet_text,
            token_limit=runtime.budgets.skills_tokens,
            boundary_kind="skill_prompt",
            seam_id="modules.context.segment_assembly.skill_snippet",
        )
        return
    if not procedure:
        return
    proc_lines = [f"Procedure: {procedure.title} ({procedure.procedure_id})"]
    if getattr(procedure, "steps", None):
        proc_lines.extend(
            f"{index + 1}. {step}" for index, step in enumerate(procedure.steps[:10])
        )
    _append_text_segment(
        runtime,
        segment_id="retrieval:procedure",
        section_key="retrieval_procedure",
        header="[PROCEDURE SNIPPET]",
        text="\n".join(proc_lines),
        token_limit=runtime.budgets.skills_tokens,
    )


def append_retrieval_segments(
    runtime: Any,
    *,
    constraints: BuildConstraints,
    bundle: _SegmentAssemblyRetrievalBundle,
    skill_snippet_text: str | None,
    procedure: Any,
) -> None:
    runtime.bucket_stats["retrieval"] = {
        "total_available": bundle.retrieval_total,
        "dropped": 0,
    }
    _append_rlm_and_vector_segments(runtime, bundle)
    _append_fact_and_memory_segments(runtime, bundle)
    _append_special_memory_segments(runtime, bundle)
    _append_low_progress_segment(runtime, bundle)
    _append_skill_or_procedure_segment(
        runtime,
        constraints=constraints,
        skill_snippet_text=skill_snippet_text,
        procedure=procedure,
    )
