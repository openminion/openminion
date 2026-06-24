"""Google Workspace tool runtime helpers."""

import hashlib
import json
import re
from typing import Any, Dict, Mapping, Optional, cast

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(token|secret|password|authorization|credential)", re.IGNORECASE
)
_EMAIL_PATTERN = re.compile(r"\b([^@\s]+)@([^\s@]+\.[^\s@]+)\b")


def parse_ndjson(text: str) -> Optional[list[Any]]:
    rows: list[Any] = []
    for line in text.splitlines():
        token = line.strip()
        if not token:
            continue
        try:
            rows.append(json.loads(token))
        except json.JSONDecodeError:
            return None
    if not rows:
        return None
    return rows


def parse_json_or_ndjson(
    text: str, *, prefer_ndjson: bool
) -> tuple[Any, Optional[str]]:
    stripped = str(text or "").strip()
    if not stripped:
        return None, None

    if prefer_ndjson:
        parsed_lines = parse_ndjson(text)
        if parsed_lines is not None:
            return parsed_lines, "ndjson"
        try:
            return json.loads(stripped), "json"
        except json.JSONDecodeError:
            return None, None

    try:
        return json.loads(stripped), "json"
    except json.JSONDecodeError:
        pass

    parsed_lines = parse_ndjson(text)
    if parsed_lines is not None:
        return parsed_lines, "ndjson"
    return None, None


def extract_error_payload(
    parsed_data: Any, stderr_text: str, *, timed_out: bool, exit_code: int
) -> Dict[str, Any]:
    if timed_out:
        return {
            "code": "TIMEOUT",
            "message": "gws command timed out",
            "details": {"exit_code": exit_code},
        }

    if isinstance(parsed_data, Mapping):
        payload = dict(parsed_data)
        error_obj = payload.get("error")
        if isinstance(error_obj, Mapping):
            code = str(error_obj.get("code", "GWS_ERROR"))
            message = str(error_obj.get("message", "gws command failed"))
            return {
                "code": code or "GWS_ERROR",
                "message": message,
                "details": {"error": dict(error_obj)},
            }
        if isinstance(error_obj, str) and error_obj.strip():
            return {
                "code": "GWS_ERROR",
                "message": error_obj.strip(),
                "details": {"error": error_obj.strip()},
            }

    err_text = str(stderr_text or "").strip()
    if err_text:
        summary = err_text.splitlines()[-1].strip()
        if summary:
            return {
                "code": "GWS_ERROR",
                "message": summary,
                "details": {"exit_code": exit_code},
            }
    return {
        "code": "GWS_ERROR",
        "message": "gws command failed",
        "details": {"exit_code": exit_code},
    }


def hash_jsonable(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str).encode(
        "utf-8", errors="replace"
    )
    return hashlib.sha256(encoded).hexdigest()


def mask_email(value: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        local = str(match.group(1))
        domain = str(match.group(2))
        if not local:
            return f"***@{domain}"
        return f"{local[0]}***@{domain}"

    return _EMAIL_PATTERN.sub(_replace, value)


def gws_redacted_credential_placeholder(ref: "CredentialRef") -> str:
    """Canonical credential redaction placeholder for GWS event emission."""
    from openminion.modules.runtime.credentials import (
        redacted_credential_ref,
    )

    return redacted_credential_ref(ref)


if False:  # pragma: no cover - typing-only import
    from openminion.modules.runtime.credentials import CredentialRef


def redact_basic(value: Any, *, key_hint: str = "") -> Any:
    if isinstance(value, Mapping):
        out: Dict[str, Any] = {}
        for key, child in value.items():
            key_str = str(key)
            if _SENSITIVE_KEY_PATTERN.search(key_str):
                out[key_str] = "[REDACTED]"
                continue
            out[key_str] = redact_basic(child, key_hint=key_str)
        return out
    if isinstance(value, list):
        return [redact_basic(item) for item in value]
    if isinstance(value, str):
        if _SENSITIVE_KEY_PATTERN.search(key_hint):
            return "[REDACTED]"
        return mask_email(value)
    return value


def result_for_event(result: Dict[str, Any], *, redaction_mode: str) -> Dict[str, Any]:
    mode = str(redaction_mode or "basic")
    if mode == "none":
        return dict(result)
    if mode == "strict":
        payload: Dict[str, Any] = {
            "ok": bool(result.get("ok", False)),
            "source": str(result.get("source", "gws")),
            "content": str(result.get("content", "")),
            "metrics": dict(result.get("metrics", {})),
        }
        if result.get("error") is not None:
            payload["error"] = redact_basic(result.get("error"))
        if result.get("data") is not None:
            payload["data_sha256"] = hash_jsonable(result.get("data"))
        if result.get("raw_stdout") is not None:
            payload["raw_stdout_sha256"] = hashlib.sha256(
                str(result.get("raw_stdout", "")).encode("utf-8", errors="replace")
            ).hexdigest()
        if result.get("raw_stderr") is not None:
            payload["raw_stderr_sha256"] = hashlib.sha256(
                str(result.get("raw_stderr", "")).encode("utf-8", errors="replace")
            ).hexdigest()
        return payload
    return cast(Dict[str, Any], redact_basic(result))


def base_request_payload(
    *,
    tool: str,
    command: list[str],
    request: Mapping[str, Any],
    auth_env: Dict[str, bool],
) -> Dict[str, Any]:
    return {
        "tool": tool,
        "source": "gws",
        "command": command,
        "request": dict(request),
        "auth_env": dict(auth_env),
    }


def summarize_data(data: Any, *, data_format: Optional[str]) -> str:
    if data is None:
        return "no parsed data"
    if isinstance(data, list):
        prefix = "ndjson pages" if data_format == "ndjson" else "items"
        return f"{len(data)} {prefix}"
    if isinstance(data, Mapping):
        keys = sorted([str(key) for key in data.keys()])[:8]
        if keys:
            return f"keys={','.join(keys)}"
    return f"type={type(data).__name__}"
