from typing import Any


def _coerce_int(
    raw_value: Any,
    *,
    default_value: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        parsed = int(default_value)
    return max(minimum, min(maximum, parsed))


def _coerce_bool(raw_value: Any, *, default_value: bool) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, (int, float)):
        return bool(raw_value)
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n"}:
            return False
    return bool(default_value)


def _normalize_search_depth(raw_value: Any) -> str:
    normalized = str(raw_value or "").strip().lower()
    if normalized in {"basic", "advanced"}:
        return normalized
    return "basic"


def _normalize_results(raw_results: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_results, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        title = str(item.get("title", "")).strip()
        content = str(item.get("content", "")).strip()
        snippet = " ".join(content.split())
        if len(snippet) > 400:
            snippet = snippet[:400].rstrip() + "..."
        record: dict[str, Any] = {
            "title": title,
            "url": url,
            "snippet": snippet,
        }
        if isinstance(item.get("score"), (int, float)):
            record["score"] = float(item.get("score"))
        normalized.append(record)
    return normalized
