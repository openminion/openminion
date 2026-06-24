"""Gateway call helpers for runtime ingress execution."""

import asyncio
import inspect
import threading
from typing import Any, Callable, Coroutine


async def run_gateway_once_impl(
    *,
    gateway: Any,
    channel: str,
    target: str,
    message: str,
    session_id: str | None,
    idempotency_key: str | None,
    request_id: str | None,
    inbound_metadata: dict[str, str] | None,
    deliver: bool,
    forced_tools: list[str] | None = None,
    capability_category: str | None = None,
    progress_callback: Callable[[object], None] | None = None,
    approval_callback: Any | None = None,
) -> Any:
    run_once_kwargs: dict[str, Any] = {
        "channel": channel,
        "target": target,
        "message": message,
        "session_id": session_id,
        "idempotency_key": idempotency_key,
        "inbound_metadata": inbound_metadata,
        "deliver": bool(deliver),
        "forced_tools": list(forced_tools or []),
        "capability_category": capability_category,
    }
    if progress_callback is not None:
        run_once_kwargs["progress_callback"] = progress_callback
    if approval_callback is not None:
        run_once_kwargs["approval_callback"] = approval_callback
    if inbound_metadata is None:
        run_once_kwargs.pop("inbound_metadata", None)
    if request_id is not None:
        run_once_kwargs["request_id"] = request_id

    filtered_kwargs = _filter_run_once_kwargs(gateway.run_once, run_once_kwargs)
    return await gateway.run_once(**filtered_kwargs)


def _run_coro_sync(
    build_coro: Callable[[], Coroutine[Any, Any, Any]],
    *,
    timeout: float,
) -> Any:
    del timeout
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(build_coro())

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result["value"] = loop.run_until_complete(build_coro())
        except BaseException as inner_exc:  # noqa: BLE001
            error["exc"] = inner_exc
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()

    if "exc" in error:
        raise error["exc"]
    return result.get("value")


def _filter_run_once_kwargs(run_once: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        signature = inspect.signature(run_once)
    except (TypeError, ValueError):
        return kwargs

    for param in signature.parameters.values():
        if param.kind == param.VAR_KEYWORD:
            return kwargs

    return {key: value for key, value in kwargs.items() if key in signature.parameters}
