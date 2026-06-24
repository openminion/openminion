from __future__ import annotations

import logging

from openminion.modules.telemetry.events.module import make_module_emitters

_LOGGER = logging.getLogger(__name__)
_MODULE_ID = "openminion-brain"
_ALLOWED_OPERATIONS = frozenset(
    {
        "turn_start",
        "llm_pack",
        "tool_loop",
        "retry",
        "turn_finish",
    }
)

_emitters = make_module_emitters(
    module_id=_MODULE_ID,
    allowed_operations=_ALLOWED_OPERATIONS,
    logger=_LOGGER,
)
emit_module_telemetry = _emitters.emit_module_telemetry
emit_brain_operation = _emitters.emit_operation
