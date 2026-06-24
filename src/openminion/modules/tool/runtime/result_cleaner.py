from typing import Any

_NOISE_FIELDS: frozenset[str] = frozenset(
    {
        "source",
        "license_note",
        "endpoints",
        "provider",
    }
)


def _clean_value(value: Any) -> Any:
    if isinstance(value, dict):
        return strip_tool_result_noise(value)
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    return value


def strip_tool_result_noise(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _clean_value(value)
        for key, value in data.items()
        if key not in _NOISE_FIELDS
    }
