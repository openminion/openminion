import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Callable


def consume_telemetry_task(task: "asyncio.Task[Any]", *, logger: Any) -> None:
    try:
        task.result()
    except Exception:
        logger.warning("telemetry emit failed", exc_info=True)


def run_telemetry_result(
    result: Any,
    *,
    logger: Any,
    consume_task_callback: Callable[["asyncio.Task[Any]"], None] | None = None,
) -> bool:
    callback = consume_task_callback or (
        lambda task: consume_telemetry_task(task, logger=logger)
    )
    if asyncio.isfuture(result):
        try:
            result.add_done_callback(callback)
        except Exception:
            logger.warning("telemetry emit failed", exc_info=True)
            return False
        return True
    if inspect.isawaitable(result):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                asyncio.run(result)
            except Exception:
                logger.warning("telemetry emit failed", exc_info=True)
                return False
            return True
        task = loop.create_task(result)
        task.add_done_callback(callback)
        return True
    return True


def emit_module_telemetry(
    telemetryctl: Any,
    method_name: str,
    *args: Any,
    logger: Any,
    run_telemetry_result_fn: Callable[[Any], bool] | None = None,
    **kwargs: Any,
) -> bool:
    ctl = telemetryctl
    if ctl is None:
        return False
    emitter = getattr(ctl, method_name, None)
    if not callable(emitter):
        return False
    try:
        result = emitter(*args, **kwargs)
    except Exception:
        logger.warning("telemetry emit failed", exc_info=True)
        return False
    if run_telemetry_result_fn is not None:
        return run_telemetry_result_fn(result)
    return run_telemetry_result(result, logger=logger)


def emit_module_operation(
    *,
    emit_module_telemetry_fn: Callable[..., bool],
    session_id: str,
    turn_id: str,
    module_id: str,
    operation: str,
    count: int = 1,
    status: str = "ok",
    extra: dict[str, Any] | None = None,
) -> bool:
    op = str(operation or "").strip()
    if not op:
        return False
    if int(count) <= 0:
        return False
    return emit_module_telemetry_fn(
        "emit_module_operation",
        session_id,
        turn_id,
        module_id,
        op,
        count=int(count),
        status=status,
        extra=extra,
    )


def emit_module_counter(
    *,
    emit_module_telemetry_fn: Callable[..., bool],
    session_id: str,
    turn_id: str,
    module_id: str,
    counter_name: str,
    value: float,
    status: str = "ok",
    extra: dict[str, Any] | None = None,
) -> bool:
    name = str(counter_name or "").strip()
    if not name:
        return False
    return emit_module_telemetry_fn(
        "emit_module_counter",
        session_id,
        turn_id,
        module_id,
        name,
        float(value),
        status=status,
        extra=extra,
    )


@dataclass(frozen=True)
class ModuleEmitters:
    """Bound emitter triple for one module's telemetry surface."""

    emit_module_telemetry: Callable[..., bool]
    emit_operation: Callable[..., bool]
    emit_counter: Callable[..., bool]


def make_module_emitters(
    *,
    module_id: str,
    allowed_operations: frozenset[str],
    logger: Any,
) -> ModuleEmitters:
    """Build module-scoped telemetry emitters."""

    def emit_module_telemetry_local(
        telemetryctl: Any,
        method_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        return emit_module_telemetry(
            telemetryctl,
            method_name,
            *args,
            logger=logger,
            **kwargs,
        )

    def emit_operation(
        *,
        telemetryctl: Any,
        session_id: str,
        turn_id: str,
        operation: str,
        count: int = 1,
        status: str = "ok",
        extra: dict[str, Any] | None = None,
    ) -> bool:
        normalized = str(operation or "").strip().lower()
        if normalized not in allowed_operations:
            return False
        return emit_module_operation(
            emit_module_telemetry_fn=lambda *a, **kw: emit_module_telemetry_local(
                telemetryctl,
                *a,
                **kw,
            ),
            session_id=session_id,
            turn_id=turn_id,
            module_id=module_id,
            operation=normalized,
            count=count,
            status=status,
            extra=extra,
        )

    def emit_counter(
        *,
        telemetryctl: Any,
        session_id: str,
        turn_id: str,
        counter_name: str,
        value: float,
        status: str = "ok",
        extra: dict[str, Any] | None = None,
    ) -> bool:
        return emit_module_counter(
            emit_module_telemetry_fn=lambda *a, **kw: emit_module_telemetry_local(
                telemetryctl,
                *a,
                **kw,
            ),
            session_id=session_id,
            turn_id=turn_id,
            module_id=module_id,
            counter_name=counter_name,
            value=value,
            status=status,
            extra=extra,
        )

    return ModuleEmitters(
        emit_module_telemetry=emit_module_telemetry_local,
        emit_operation=emit_operation,
        emit_counter=emit_counter,
    )


__all__ = [
    "consume_telemetry_task",
    "run_telemetry_result",
    "emit_module_telemetry",
    "emit_module_operation",
    "emit_module_counter",
    "ModuleEmitters",
    "make_module_emitters",
]
