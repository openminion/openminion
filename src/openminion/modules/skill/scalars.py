from typing import Any


def parse_scalar(value: str) -> Any:
    text = (value or "").strip()
    if text == "":
        return ""

    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]

    lowered = text.lower()
    if lowered in {"true", "yes"}:
        return True
    if lowered in {"false", "no"}:
        return False
    if lowered in {"null", "none"}:
        return None

    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(part.strip()) for part in inner.split(",") if part.strip()]

    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text
