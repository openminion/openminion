from typing import TYPE_CHECKING, Any

from openminion.modules.brain.retry import (
    STRUCTURED_FAILURE_KIND_KEY,
    STRUCTURED_HAS_TOOL_CALLS_KEY,
    STRUCTURED_RETRYABLE_KEY,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from openminion.modules.brain.runner import BrainRunner


def _normalize_sub_intents(value: Any) -> list[str]:
    if isinstance(value, list):
        return [text for item in value if (text := str(item or "").strip())]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if "," in text:
            return [part.strip() for part in text.split(",") if part.strip()]
        return [text]
    return []


def _normalize_rationale(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_decision_payload(*, runner: "BrainRunner", raw: Any) -> Any:
    del runner
    if not isinstance(raw, dict):
        return raw

    normalized = dict(raw)
    normalized.pop(STRUCTURED_RETRYABLE_KEY, None)
    normalized.pop(STRUCTURED_FAILURE_KIND_KEY, None)
    normalized.pop(STRUCTURED_HAS_TOOL_CALLS_KEY, None)
    normalized["sub_intents"] = _normalize_sub_intents(normalized.get("sub_intents"))
    normalized["rationale"] = _normalize_rationale(normalized.get("rationale"))
    return normalized


__all__ = ["_normalize_sub_intents", "normalize_decision_payload"]
