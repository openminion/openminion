import logging

from openminion.modules.telemetry.events.module import make_module_emitters

_LOGGER = logging.getLogger(__name__)
_MODULE_ID = "openminion-skill"
_ALLOWED_OPERATIONS = frozenset(
    {
        "shortlist",
        "expand",
        "select",
        "fallback",
        "untrusted_source_promotion",
    }
)

_emitters = make_module_emitters(
    module_id=_MODULE_ID,
    allowed_operations=_ALLOWED_OPERATIONS,
    logger=_LOGGER,
)
emit_module_telemetry = _emitters.emit_module_telemetry
emit_skill_operation = _emitters.emit_operation
emit_skill_counter = _emitters.emit_counter
