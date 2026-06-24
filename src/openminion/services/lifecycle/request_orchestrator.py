from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast
from openminion.services.runtime.ingress import (
    TurnRequestError,
    TurnTimeoutError,
    _build_turn_context as _build_turn_context_impl,
    _mutable_inbound_metadata as _mutable_inbound_metadata_impl,
    _run_gateway_once as _run_gateway_once_impl,
    apply_inbound_overrides as _apply_inbound_overrides_impl,
    apply_workspace_root as _apply_workspace_root_impl,
    parse_forced_tools as _parse_forced_tools_impl,
    parse_inbound_metadata as _parse_inbound_metadata_impl,
    resolve_capability_category as _resolve_capability_category_impl,
    resolve_timeout_seconds as _resolve_timeout_seconds_impl,
    run_turn_payload,
)

if TYPE_CHECKING:
    from openminion.api.runtime import APIRuntime as APIRuntimeType


class _APIRuntimeProxy:
    @staticmethod
    def from_config_path(config_path: str | None) -> "APIRuntime":
        from openminion.api.runtime import APIRuntime as _APIRuntime

        return _APIRuntime.from_config_path(config_path)


# Keep a patchable APIRuntime symbol without importing openminion.api at module load.
APIRuntime: Any = _APIRuntimeProxy


def run_turn(
    *,
    config_path: str | None,
    payload: dict[str, Any],
    runtime: "APIRuntimeType | None" = None,
    request_id: str | None = None,
    progress_callback: Callable[[object], None] | None = None,
    approval_callback: Any | None = None,
) -> dict[str, Any]:
    own_runtime = runtime is None
    active_runtime = runtime or APIRuntime.from_config_path(config_path)

    try:
        return run_turn_payload(
            runtime=active_runtime,
            payload=payload,
            request_id=request_id,
            progress_callback=progress_callback,
            approval_callback=approval_callback,
        )
    finally:
        if own_runtime:
            active_runtime.close()


_apply_workspace_root = _apply_workspace_root_impl
_build_turn_context = _build_turn_context_impl
_mutable_inbound_metadata = _mutable_inbound_metadata_impl
_resolve_timeout_seconds = _resolve_timeout_seconds_impl
_parse_inbound_metadata = _parse_inbound_metadata_impl
_apply_inbound_overrides = _apply_inbound_overrides_impl
_parse_forced_tools = _parse_forced_tools_impl
_resolve_capability_category = _resolve_capability_category_impl
_run_gateway_once = _run_gateway_once_impl


def _normalize_result_to_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result

    if hasattr(result, "metadata"):
        metadata = result.metadata
        if isinstance(metadata, dict):
            stats = getattr(result, "stats", None)
            return {
                "metadata": metadata,
                "id": getattr(result, "id", ""),
                "channel": getattr(result, "channel", ""),
                "target": getattr(result, "target", ""),
                "body": getattr(result, "body", ""),
                "stats": (
                    stats.as_payload()
                    if stats is not None and hasattr(stats, "as_payload")
                    else {}
                ),
            }

    try:
        return cast(dict[str, Any], dict(result))
    except (TypeError, ValueError):
        pass

    return {"metadata": {}, "_warning": "Could not normalize result to dict"}


__all__ = [
    "APIRuntime",
    "TurnRequestError",
    "TurnTimeoutError",
    "_apply_inbound_overrides",
    "_apply_workspace_root",
    "_build_turn_context",
    "_mutable_inbound_metadata",
    "_normalize_result_to_dict",
    "_parse_forced_tools",
    "_parse_inbound_metadata",
    "_resolve_capability_category",
    "_resolve_timeout_seconds",
    "_run_gateway_once",
    "run_turn",
]
