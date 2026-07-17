from __future__ import annotations

from openminion.modules.tool.contracts import (
    DEFAULT_VISIBLE_MODEL_TOOL_IDS_SET,
    MODEL_CONTROL_TOOL_IDS,
    is_dynamic_model_tool_id,
)


def requires_explicit_exposure_profile(tool_name: str) -> bool:
    name = str(tool_name or "").strip()
    if not name:
        return True
    if name in MODEL_CONTROL_TOOL_IDS:
        return False
    return (
        name not in DEFAULT_VISIBLE_MODEL_TOOL_IDS_SET
        and not is_dynamic_model_tool_id(name)
    )


__all__ = ["requires_explicit_exposure_profile"]
