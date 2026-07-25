from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, cast

from openminion.cli.commands.daemon import ensure_daemon_running
from openminion.cli.transport.daemon_client import daemon_request

RuntimeSourcePolicy = str


@dataclass(frozen=True)
class RuntimeSourceResult:
    source: str
    payload: dict[str, Any]
    fallback_reason: str = ""


def resolve_runtime_source_policy(args: object) -> RuntimeSourcePolicy:
    value = str(getattr(args, "runtime_source", "auto") or "auto").strip().lower()
    if value not in {"auto", "daemon", "inproc"}:
        raise RuntimeError("--runtime-source must be one of: auto, daemon, inproc")
    return value


def call_daemon_or_inproc(
    *,
    args: object,
    auto_start: bool,
    daemon_call: Callable[[Any], tuple[int, dict[str, Any]]],
    inproc_call: Callable[[], dict[str, Any]],
) -> RuntimeSourceResult:
    policy = resolve_runtime_source_policy(args)
    if policy == "inproc":
        return RuntimeSourceResult(source="inproc", payload=inproc_call())

    try:
        endpoint = ensure_daemon_running(
            getattr(args, "config", None),
            auto_start=auto_start,
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
        )
        status, payload = daemon_call(endpoint)
        if status < 400:
            return RuntimeSourceResult(source="daemon", payload=payload)
        message = _format_daemon_status_error(payload, status)
    except RuntimeError as exc:
        message = str(exc)

    if policy == "daemon":
        raise RuntimeError(f"daemon runtime source unavailable: {message}")
    payload = inproc_call()
    payload.setdefault("runtime_source", "inproc")
    payload.setdefault("runtime_fallback_reason", message)
    return RuntimeSourceResult(
        source="inproc",
        payload=payload,
        fallback_reason=message,
    )


def daemon_get(
    endpoint: Any, *, path: str, timeout_s: float = 10
) -> tuple[int, dict[str, Any]]:
    return cast(
        tuple[int, dict[str, Any]],
        daemon_request(endpoint=endpoint, method="GET", path=path, timeout_s=timeout_s),
    )


def _format_daemon_status_error(payload: dict[str, Any], status: int) -> str:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = str(error.get("message", "") or "").strip()
        if message:
            return f"daemon request failed ({status}): {message}"
    return f"daemon request failed ({status})"
