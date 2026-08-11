from typing import Any

from openminion.modules.brain.loop.tools import DirectToolTurnContext


def stage_required_write_direct_tool(
    loop_state: Any,
    *,
    allowed_tools: frozenset[str],
) -> None:
    if getattr(loop_state, "direct_tool_turn", None) is not None:
        return
    requested_name = "file.write" if "file.write" in allowed_tools else "code.patch"
    loop_state.direct_tool_turn = DirectToolTurnContext(
        requested_tool_names=(requested_name,),
        requested_batch_signature="",
        match_by_name_only=True,
    )
    loop_state.scratchpad["coding.required_write_direct_tool"] = requested_name
