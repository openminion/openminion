from openminion.base.time import utc_now_iso as iso_now  # noqa: F401

import json
from datetime import datetime, timedelta, timezone
from typing import Any


def iso_after(seconds: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=max(0, int(seconds)))
    ).isoformat()


def iso_ago(seconds: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(seconds=max(0, int(seconds)))
    ).isoformat()


def json_dump(value: Any) -> str:
    if value is None:
        return "{}"
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def json_load(raw: str | None) -> dict[str, Any]:
    if raw in (None, "", "null"):
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}
