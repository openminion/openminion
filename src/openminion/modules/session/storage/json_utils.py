import json
from typing import Any


def to_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def parse_json(raw: str | None, fallback: Any) -> Any:
    if raw in {None, ""}:
        return fallback
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return fallback
    return parsed


def deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=True))
