"""Compatibility exports for context segment assembly helpers."""

from __future__ import annotations

from ..mode_ranking import normalize_mode_name
from .evidence import append_evidence_and_turn_input_segments
from .messages import (
    assistant_tail_for_recent_window as _assistant_tail_for_recent_window,
    map_turn_role,
    protected_decide_recent_turn_indexes,
    segments_to_messages,
)
from .prefix import (
    append_prefix_and_mission_segments,
    tool_inventory_lines as _tool_inventory_lines,
)
from .prefix_optional import (
    render_budget_telemetry_block as _render_budget_telemetry_block,
)
from .recent import append_recent_window_segments
from .runtime import (
    _SegmentAssemblyRuntime,
    _content_hash,
    inject_context_drop_visibility_note,
    make_segment,
    render_context_drop_visibility_note,
)

__all__ = [
    "_SegmentAssemblyRuntime",
    "_assistant_tail_for_recent_window",
    "_content_hash",
    "_render_budget_telemetry_block",
    "_tool_inventory_lines",
    "append_evidence_and_turn_input_segments",
    "append_prefix_and_mission_segments",
    "append_recent_window_segments",
    "inject_context_drop_visibility_note",
    "make_segment",
    "map_turn_role",
    "normalize_mode_name",
    "protected_decide_recent_turn_indexes",
    "render_context_drop_visibility_note",
    "segments_to_messages",
]
