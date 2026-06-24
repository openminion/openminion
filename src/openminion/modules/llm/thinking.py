from typing import Any


def serialize_thinking_blocks(raw_blocks: list[Any] | None) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for item in raw_blocks or []:
        if isinstance(item, dict):
            payload.append(dict(item))
            continue
        payload.append(
            {
                "type": str(getattr(item, "type", "") or "thinking"),
                "content": str(getattr(item, "content", "") or ""),
                "signature": str(getattr(item, "signature", "") or "").strip() or None,
                "redacted": bool(getattr(item, "redacted", False)),
            }
        )
    return payload
