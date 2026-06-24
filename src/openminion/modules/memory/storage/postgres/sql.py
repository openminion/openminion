import json
import re
from typing import Any

from ...models import MemoryType

_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_FTS_PREFIX_RE = re.compile(r"[A-Za-z0-9]+\*")


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _json_loads(raw: Any, fallback: Any) -> Any:
    if raw is None:
        return fallback
    if isinstance(raw, (dict, list, int, float, bool)):
        return raw
    text = str(raw or "").strip()
    if not text:
        return fallback
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return fallback if fallback is not None else text


def _record_content_text(content: dict[str, Any] | str) -> str:
    if isinstance(content, dict):
        return _json_dumps(content)
    return str(content or "")


def _build_search_text(
    *,
    scope: str,
    record_type: MemoryType,
    key: str | None,
    title: str | None,
    content: dict[str, Any] | str,
    tags: list[str],
    entities: list[str],
) -> str:
    parts = [
        str(scope or "").strip(),
        str(record_type or "").strip(),
        str(title or "").strip(),
        str(key or "").strip(),
        _record_content_text(content).strip(),
        " ".join(str(item or "").strip() for item in tags if str(item or "").strip()),
        " ".join(
            str(item or "").strip() for item in entities if str(item or "").strip()
        ),
    ]
    return " ".join(part for part in parts if part)


def _named_params(prefix: str, values: list[Any]) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {}
    placeholders: list[str] = []
    for idx, value in enumerate(values):
        key = f"{prefix}_{idx}"
        placeholders.append(f":{key}")
        params[key] = value
    return ", ".join(placeholders), params


__all__ = [
    "_FTS_PREFIX_RE",
    "_FTS_TOKEN_RE",
    "_build_search_text",
    "_clamp01",
    "_json_dumps",
    "_json_loads",
    "_named_params",
    "_record_content_text",
]
