import json
from pathlib import Path
from typing import Any, Mapping

from openminion.base.config.env import EnvironmentConfig, resolve_environment_config

from .layout import build_trace_file_path, resolve_trace_root


def trace_requests_enabled(
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> bool:
    return bool(resolve_environment_config(env=env).openminion_trace_requests)


def trace_context_payload(
    *,
    session_id: str,
    turn_id: str,
    inference_step: int,
    label: str,
    trace_id: str = "",
    agent_id: str = "",
    run_id: str = "",
    provider: str = "",
    model: str = "",
    home_root: Path | None = None,
) -> dict[str, Any]:
    trace_root = resolve_trace_root(home_root=home_root)
    _, http_rel = build_trace_file_path(
        trace_root,
        session_id=session_id,
        turn_id=turn_id,
        inference_step=inference_step,
        label=label,
        suffix="-http.json",
    )
    _, http_response_rel = build_trace_file_path(
        trace_root,
        session_id=session_id,
        turn_id=turn_id,
        inference_step=inference_step,
        label=label,
        suffix="-http-response.json",
    )
    _, structured_rel = build_trace_file_path(
        trace_root,
        session_id=session_id,
        turn_id=turn_id,
        inference_step=inference_step,
        label=label,
        suffix="-structured.json",
    )
    return {
        "session_id": str(session_id or ""),
        "turn_id": str(turn_id or ""),
        "inference_step": int(inference_step),
        "label": str(label or ""),
        "trace_id": str(trace_id or ""),
        "agent_id": str(agent_id or ""),
        "run_id": str(run_id or ""),
        "provider": str(provider or ""),
        "model": str(model or ""),
        "home_root": str(home_root) if home_root is not None else "",
        "http_trace_filename": http_rel,
        "http_response_trace_filename": http_response_rel,
        "structured_trace_filename": structured_rel,
    }


def write_structured_trace(
    *,
    trace_context: Mapping[str, Any] | None,
    patch: Mapping[str, Any],
) -> str | None:
    if not trace_requests_enabled():
        return None
    trace_meta = dict(trace_context or {})
    session_id = str(trace_meta.get("session_id") or "").strip()
    turn_id = str(trace_meta.get("turn_id") or "").strip()
    label = str(trace_meta.get("label") or "").strip()
    if not session_id or not turn_id or not label:
        return None

    try:
        inference_step = int(trace_meta.get("inference_step") or 0)
    except (TypeError, ValueError):
        inference_step = 0

    home_root_raw = str(trace_meta.get("home_root") or "").strip()
    home_root = Path(home_root_raw) if home_root_raw else None
    trace_root = resolve_trace_root(home_root=home_root)
    trace_path, relative = build_trace_file_path(
        trace_root,
        session_id=session_id,
        turn_id=turn_id,
        inference_step=inference_step,
        label=label,
        suffix="-structured.json",
    )
    trace_path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {}
    if trace_path.exists():
        try:
            loaded = json.loads(trace_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except Exception:
            payload = {}

    trace_patch = dict(patch or {})
    trace_patch.setdefault(
        "trace",
        {
            "session_id": session_id,
            "turn_id": turn_id,
            "inference_step": inference_step,
            "label": label,
            "trace_id": str(trace_meta.get("trace_id") or ""),
            "agent_id": str(trace_meta.get("agent_id") or ""),
            "run_id": str(trace_meta.get("run_id") or ""),
        },
    )
    if str(trace_meta.get("provider") or "").strip():
        trace_patch.setdefault("provider", str(trace_meta.get("provider") or ""))
    if str(trace_meta.get("model") or "").strip():
        trace_patch.setdefault("model", str(trace_meta.get("model") or ""))

    merged = _merge_dicts(payload, trace_patch)
    trace_path.write_text(
        json.dumps(merged, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return relative


def _merge_dicts(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in dict(patch).items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            merged[key] = _merge_dicts(current, value)
            continue
        merged[key] = value
    return merged


__all__ = [
    "trace_requests_enabled",
    "trace_context_payload",
    "write_structured_trace",
]
