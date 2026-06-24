from .model_ids import ALL_MODEL_TOOL_IDS_SET

TOOL_WRAPPER_PREFIXES: tuple[str, ...] = (
    "tool.",
    "tools.",
    "function.",
    "functions.",
)

_MODEL_TOOL_IDS_BY_LOWER: dict[str, str] = {
    model_tool_id.lower(): model_tool_id for model_tool_id in ALL_MODEL_TOOL_IDS_SET
}


def strip_tool_wrapper_prefix(raw_name: str) -> str:
    token = str(raw_name or "").strip()
    if not token:
        return token

    lowered = token.lower()
    if lowered in _MODEL_TOOL_IDS_BY_LOWER:
        return token

    for prefix in TOOL_WRAPPER_PREFIXES:
        if lowered.startswith(prefix):
            return token[len(prefix) :].strip()
    return token


def normalize_raw_model_tool_name(raw_name: str) -> str | None:
    """Normalize raw tool name to canonical model-facing ID."""
    token = str(raw_name or "").strip()
    if not token:
        return None

    if token in ALL_MODEL_TOOL_IDS_SET:
        return token

    direct = _MODEL_TOOL_IDS_BY_LOWER.get(token.lower())
    if direct:
        return direct

    token = strip_tool_wrapper_prefix(token)
    if not token:
        return None
    if token in ALL_MODEL_TOOL_IDS_SET:
        return token
    return _MODEL_TOOL_IDS_BY_LOWER.get(token.lower())
