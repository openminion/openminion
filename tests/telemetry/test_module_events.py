import asyncio
import logging


from openminion.modules.telemetry.events.module import (
    consume_telemetry_task,
    emit_module_telemetry,
    run_telemetry_result,
)


def test_consume_telemetry_task_logs_warning_on_sink_failure() -> None:
    async def _boom() -> None:
        raise RuntimeError("boom")

    async def _case() -> None:
        logger = logging.getLogger("openminion.tests.telemetry.events.module")
        task = asyncio.create_task(_boom())
        await asyncio.sleep(0)
        handler = _ListHandler()
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
        try:
            consume_telemetry_task(task, logger=logger)
        finally:
            logger.removeHandler(handler)
        assert any("telemetry emit failed" in msg for msg in handler.messages)

    asyncio.run(_case())


def test_run_telemetry_result_logs_warning_on_awaitable_failure() -> None:
    async def _boom() -> None:
        raise RuntimeError("boom")

    logger = logging.getLogger("openminion.tests.telemetry.events.module")
    handler = _ListHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        ok = run_telemetry_result(_boom(), logger=logger)
    finally:
        logger.removeHandler(handler)

    assert ok is False
    assert any("telemetry emit failed" in msg for msg in handler.messages)


def test_emit_module_telemetry_logs_warning_when_emitter_raises() -> None:
    class _ExplodingEmitter:
        def emit_module_operation(self, *_args, **_kwargs) -> None:
            raise RuntimeError("boom")

    logger = logging.getLogger("openminion.tests.telemetry.events.module")
    handler = _ListHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        ok = emit_module_telemetry(
            _ExplodingEmitter(),
            "emit_module_operation",
            "sess",
            "turn",
            "openminion-context",
            "pack_build",
            logger=logger,
        )
    finally:
        logger.removeHandler(handler)

    assert ok is False
    assert any("telemetry emit failed" in msg for msg in handler.messages)


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())
