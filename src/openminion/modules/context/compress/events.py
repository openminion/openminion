import logging

from openminion.modules.telemetry.events.module import make_module_emitters

_LOGGER = logging.getLogger(__name__)
_MODULE_ID = "context.compress"
_ALLOWED_OPERATIONS = frozenset(
    {
        "summary_create",
        "summary_refresh",
        "summary_skip",
        "summary_error",
    }
)

_emitters = make_module_emitters(
    module_id=_MODULE_ID,
    allowed_operations=_ALLOWED_OPERATIONS,
    logger=_LOGGER,
)
emit_module_telemetry = _emitters.emit_module_telemetry
emit_compress_operation = _emitters.emit_operation
emit_compress_counter = _emitters.emit_counter
