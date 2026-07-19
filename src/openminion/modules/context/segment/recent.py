"""Recent-window segment assembly."""

from __future__ import annotations

from ..constants import CONTEXT_BUCKET_RECENT_WINDOW
from ..mode_ranking import _MODE_ACT, _MODE_RESPOND, normalize_mode_name
from ..schemas import BuildPackRequest, SessionSlice
from .messages import (
    assistant_tail_for_recent_window,
    map_turn_role,
    protected_decide_recent_turn_indexes,
)
from .runtime import _SegmentAssemblyRuntime


def _dedupe_current_query_turns(
    *, request: BuildPackRequest, session_slice: SessionSlice
) -> list:
    recent_turns = list(session_slice.recent_turns)
    current_query = str(request.query or "").strip()
    while recent_turns:
        last_turn = recent_turns[-1]
        last_role = map_turn_role(last_turn.role)
        last_content = str(last_turn.content or "").strip()
        if last_role == "user" and current_query and last_content == current_query:
            recent_turns.pop()
            continue
        break
    return recent_turns


def _recent_turn_budget(runtime: _SegmentAssemblyRuntime, request: BuildPackRequest) -> int:
    recent_turn_budget = runtime.budgets.recent_turn_tokens
    mode_name = normalize_mode_name(request.mode_name)
    if mode_name == _MODE_RESPOND:
        return max(160, min(recent_turn_budget, recent_turn_budget // 2))
    if mode_name == _MODE_ACT:
        return max(220, min(recent_turn_budget, recent_turn_budget - 120))
    return recent_turn_budget


def _trim_recent_turns_to_budget(
    runtime: _SegmentAssemblyRuntime,
    *,
    request: BuildPackRequest,
    recent_turns: list,
    recent_turn_contents: list[str],
) -> None:
    turn_tokens = sum(runtime.estimate_tokens(content) for content in recent_turn_contents)
    while recent_turns and turn_tokens > _recent_turn_budget(runtime, request):
        protected_indexes = protected_decide_recent_turn_indexes(
            recent_turns,
            purpose=request.purpose,
        )
        drop_index = next(
            (idx for idx in range(len(recent_turns)) if idx not in protected_indexes),
            None,
        )
        if drop_index is None:
            break
        recent_turns.pop(drop_index)
        recent_turn_contents.pop(drop_index)
        runtime.bucket_stats[CONTEXT_BUCKET_RECENT_WINDOW]["dropped"] += 1
        turn_tokens = sum(runtime.estimate_tokens(content) for content in recent_turn_contents)


def append_recent_window_segments(
    runtime: _SegmentAssemblyRuntime,
    *,
    request: BuildPackRequest,
    session_slice: SessionSlice,
) -> None:
    recent_turns = _dedupe_current_query_turns(
        request=request,
        session_slice=session_slice,
    )
    protected_indexes = protected_decide_recent_turn_indexes(
        recent_turns,
        purpose=request.purpose,
    )
    recent_turn_contents = [
        assistant_tail_for_recent_window(
            turn,
            purpose=request.purpose,
            estimate_tokens=runtime.estimate_tokens,
            preserve_full=idx in protected_indexes,
        )
        for idx, turn in enumerate(recent_turns)
    ]
    runtime.bucket_stats[CONTEXT_BUCKET_RECENT_WINDOW] = {
        "total_available": len(recent_turns),
        "dropped": 0,
    }
    _trim_recent_turns_to_budget(
        runtime,
        request=request,
        recent_turns=recent_turns,
        recent_turn_contents=recent_turn_contents,
    )
    protected_indexes = protected_decide_recent_turn_indexes(
        recent_turns,
        purpose=request.purpose,
    )
    for idx, (turn, content) in enumerate(zip(recent_turns, recent_turn_contents)):
        runtime.segments.append(
            runtime.make(
                f"turn:{turn.turn_id}",
                CONTEXT_BUCKET_RECENT_WINDOW,
                content,
                role=map_turn_role(turn.role),
                pinned=idx in protected_indexes,
            )
        )
