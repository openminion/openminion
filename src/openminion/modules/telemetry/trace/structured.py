import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from openminion.base.config.env import EnvironmentConfig, resolve_environment_config
from openminion.modules.telemetry.constants import LLM_TRACE_FORMAT_VERSION

from .layout import (
    build_trace_file_path,
    resolve_trace_root,
    write_protected_trace_file,
)
from .metadata import apply_content_policy, warn_trace_write_failure


_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class TraceArtifactPublication:
    paths: tuple[str, ...] = ()
    complete: bool = True

    def merge(self, other: "TraceArtifactPublication") -> "TraceArtifactPublication":
        return TraceArtifactPublication(
            paths=tuple(sorted(set(self.paths) | set(other.paths))),
            complete=self.complete and other.complete,
        )

    def event_fields(self, *, final: bool) -> dict[str, Any]:
        return {
            "trace_artifact_paths": list(self.paths),
            "trace_artifacts_complete": self.complete if final else False,
        }


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
    invocation_id: str = "",
    execution_id: str = "",
    provider: str = "",
    model: str = "",
    home_root: Path | None = None,
) -> dict[str, Any]:
    trace_root = resolve_trace_root(home_root=home_root)
    relative_paths: dict[str, str] = {}
    for field, suffix in (
        ("http_trace_filename", "-http.json"),
        ("http_response_trace_filename", "-http-response.json"),
        ("http_sse_response_trace_filename", "-http-sse-response.json"),
        ("structured_trace_filename", "-structured.json"),
    ):
        _, relative_paths[field] = build_trace_file_path(
            trace_root,
            session_id=session_id,
            turn_id=turn_id,
            inference_step=inference_step,
            label=label,
            suffix=suffix,
        )
    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "inference_step": inference_step,
        "label": label,
        "trace_id": trace_id,
        "agent_id": agent_id,
        "run_id": run_id,
        "invocation_id": invocation_id,
        "execution_id": execution_id,
        "provider": provider,
        "model": model,
        "home_root": str(home_root) if home_root is not None else "",
        **relative_paths,
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
    payload: dict[str, Any] = {}
    try:
        trace_exists = trace_path.exists()
    except OSError as exc:
        warn_trace_write_failure(_LOG, "structured_output", exc)
        return None
    if trace_exists:
        try:
            loaded = json.loads(trace_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warn_trace_write_failure(_LOG, "structured_output", exc)
            return None
        if not isinstance(loaded, dict):
            warn_trace_write_failure(
                _LOG,
                "structured_output",
                ValueError("structured trace root is not an object"),
            )
            return None
        payload = loaded

    trace_patch = dict(patch)
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
            "invocation_id": str(trace_meta.get("invocation_id") or ""),
            "execution_id": str(trace_meta.get("execution_id") or ""),
        },
    )
    if str(trace_meta.get("provider") or "").strip():
        trace_patch.setdefault("provider", str(trace_meta.get("provider") or ""))
    if str(trace_meta.get("model") or "").strip():
        trace_patch.setdefault("model", str(trace_meta.get("model") or ""))

    merged_payload = _merge_dicts(payload, trace_patch)
    merged_payload["trace_format_version"] = LLM_TRACE_FORMAT_VERSION
    merged_payload["artifact_kind"] = "structured_output"
    merged = apply_content_policy(
        merged_payload,
        allow_sensitive_content=True,
    )
    try:
        write_protected_trace_file(
            trace_path,
            json.dumps(merged, indent=2, sort_keys=True, default=str),
        )
    except (OSError, TypeError, ValueError) as exc:
        warn_trace_write_failure(_LOG, "structured_output", exc)
        return None
    return relative


def _merge_dicts(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            merged[key] = _merge_dicts(current, value)
            continue
        merged[key] = value
    return merged


__all__ = [
    "TraceArtifactPublication",
    "trace_requests_enabled",
    "trace_context_payload",
    "write_structured_trace",
]
