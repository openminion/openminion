import json
from typing import Any

_WRITE_CAPABLE_TOOL_NAMES = frozenset(
    {
        "file.write",
        "file.delete",
        "file.copy",
        "file.move",
        "file.edit",
        "exec.run",
        "cmd.run",
    }
)


def watch_write_audit_entries(
    *,
    metadata: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    raw_results = metadata.get("tool_results")
    if not raw_results:
        return ()
    try:
        parsed = json.loads(str(raw_results))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    if not isinstance(parsed, list):
        return ()
    entries: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name", "") or "").strip()
        if tool_name not in _WRITE_CAPABLE_TOOL_NAMES:
            continue
        entries.append(
            {
                "tool_name": tool_name,
                "ok": bool(item.get("ok", False)),
                "call_id": str(item.get("call_id", "") or "").strip(),
            }
        )
    return tuple(entries)
