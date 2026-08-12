import logging

from openminion.modules.telemetry.events.module import make_module_emitters

_ALLOWED_OPERATIONS = frozenset(
    {
        "summary_create",
        "summary_refresh",
        "summary_skip",
        "summary_error",
    }
)

_emitters = make_module_emitters(
    module_id="context.compress",
    allowed_operations=_ALLOWED_OPERATIONS,
    logger=logging.getLogger(__name__),
)
emit_module_telemetry = _emitters.emit_module_telemetry
emit_compress_operation = _emitters.emit_operation
emit_compress_counter = _emitters.emit_counter
