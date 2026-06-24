"""Segment-building and rendering helpers for context assembly."""

from .core import (
    _SegmentAssemblyRuntime,
    _content_hash,
    _content_hash as _segment_hash,
    _render_budget_telemetry_block,
    _tool_inventory_lines,
    append_evidence_and_turn_input_segments,
    append_prefix_and_mission_segments,
    append_recent_window_segments,
    append_summary_segments,
    inject_context_drop_visibility_note,
    make_segment,
    map_turn_role,
    protected_decide_recent_turn_indexes,
    render_context_drop_visibility_note,
    segments_to_messages,
)
from .core import normalize_mode_name  # pass-through

__all__ = [
    "_SegmentAssemblyRuntime",
    "_content_hash",
    "_segment_hash",
    "_render_budget_telemetry_block",
    "_tool_inventory_lines",
    "append_evidence_and_turn_input_segments",
    "append_prefix_and_mission_segments",
    "append_recent_window_segments",
    "append_summary_segments",
    "inject_context_drop_visibility_note",
    "make_segment",
    "map_turn_role",
    "normalize_mode_name",
    "protected_decide_recent_turn_indexes",
    "render_context_drop_visibility_note",
    "segments_to_messages",
]
