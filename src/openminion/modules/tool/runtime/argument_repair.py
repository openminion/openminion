import re
from typing import Any, Mapping

from ..contracts.model_ids import (
    MODEL_LOCATION,
    MODEL_TIME,
    MODEL_WEATHER,
    MODEL_WEB_SEARCH,
)

_WEATHER_MULTI_LOCATION_MARKERS: tuple[str, ...] = (
    " across ",
    " different cities",
    " major cities",
    " multiple cities",
    " several cities",
    " compare ",
    " and ",
    ",",
    " cities ",
    " cities?",
)
_WEATHER_LOCATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:weather|forecast)(?:[^?.!]*)\b(?:in|at|for)\s+(.+?)(?:\?|$)",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:in|at|for)\s+(.+?)(?:\?|$)", re.IGNORECASE),
)
_WEATHER_TRAILING_TOKENS_RE = re.compile(
    r"\b(?:right now|now|today|currently|please|briefly)\b[\s?.!,:;-]*$",
    re.IGNORECASE,
)
_WEATHER_LEADING_FILLER_RE = re.compile(r"^(?:the\s+)", re.IGNORECASE)


def tool_family_for_argument_repair(tool_name: str) -> str | None:
    lowered = str(tool_name or "").strip().lower()
    if not lowered:
        return None
    if lowered == MODEL_TIME or lowered.startswith("time."):
        return MODEL_TIME
    if lowered == MODEL_LOCATION or lowered.startswith("location."):
        return MODEL_LOCATION
    if (
        lowered == MODEL_WEB_SEARCH
        or lowered.startswith("search.")
        or lowered.endswith(".search")
    ):
        return MODEL_WEB_SEARCH
    if lowered == MODEL_WEATHER or "weather" in lowered:
        return MODEL_WEATHER
    return None


def simple_required_fields(tool_name: str) -> tuple[str, ...]:
    family = tool_family_for_argument_repair(tool_name)
    if family in {MODEL_TIME, MODEL_LOCATION}:
        return ()
    if family == MODEL_WEB_SEARCH:
        return ("query",)
    if family == MODEL_WEATHER:
        return ("location",)
    return ()


def missing_simple_required_fields(
    *,
    tool_name: str,
    arguments: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    family = tool_family_for_argument_repair(tool_name)
    args = dict(arguments or {})
    if family == MODEL_WEB_SEARCH:
        for key in ("query", "q"):
            if str(args.get(key, "")).strip():
                return ()
        return ("query",)
    if family == MODEL_WEATHER:
        for key in ("location", "city", "query", "place"):
            if str(args.get(key, "")).strip():
                return ()
        return ("location",)
    return ()


def synthesize_simple_tool_arguments(
    *,
    tool_name: str,
    user_input: str,
    existing_args: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    family = tool_family_for_argument_repair(tool_name)
    if family is None:
        return None

    normalized_args = dict(existing_args or {})
    if family in {MODEL_TIME, MODEL_LOCATION}:
        return normalized_args or {}

    if family == MODEL_WEB_SEARCH:
        query = str(normalized_args.get("query", "")).strip()
        if not query:
            query = str(normalized_args.get("q", "")).strip()
            if query:
                normalized_args["query"] = query
        if (
            "max_results" not in normalized_args
            and normalized_args.get("count") is not None
        ):
            normalized_args["max_results"] = normalized_args.get("count")
        if query:
            return normalized_args
        query = _normalize_space(user_input)
        if not query:
            return None
        normalized_args["query"] = query
        return normalized_args

    if family == MODEL_WEATHER:
        if not missing_simple_required_fields(
            tool_name=tool_name,
            arguments=normalized_args,
        ):
            return normalized_args
        location = extract_single_weather_location(user_input)
        if not location:
            return None
        normalized_args["location"] = location
        return normalized_args

    return None


def repair_structured_tool_arguments(
    arguments: dict[str, Any] | str | bytes,
    *,
    channel_name: str,
    alias_map: Mapping[str, str] | None = None,
    type_coercions: Mapping[str, Any] | None = None,
) -> dict[str, Any] | str | bytes:
    # Lazy import to break tool -> brain -> tool circular import (CQRC-161).
    from openminion.modules.brain.runtime.recovery import (
        TCRPContext,
        normalize_payload,
    )

    normalized, _events = normalize_payload(
        arguments,
        ctx=TCRPContext(channel_name=channel_name),
        alias_map=alias_map,
        type_coercions=type_coercions,
    )
    return normalized


def extract_single_weather_location(user_input: str) -> str:
    text = _normalize_space(user_input)
    if not text:
        return ""
    lowered = f" {text.lower()} "
    if any(marker in lowered for marker in _WEATHER_MULTI_LOCATION_MARKERS):
        return ""

    for pattern in _WEATHER_LOCATION_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = _sanitize_weather_location_candidate(match.group(1))
        if candidate:
            return candidate
    return ""


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _sanitize_weather_location_candidate(raw: str) -> str:
    candidate = _normalize_space(raw)
    if not candidate:
        return ""
    while True:
        trimmed = _WEATHER_TRAILING_TOKENS_RE.sub("", candidate).strip(" \t\n\r?.!,:;-")
        if trimmed == candidate:
            break
        candidate = trimmed
    candidate = _WEATHER_LEADING_FILLER_RE.sub("", candidate).strip(" \t\n\r?.!,:;-")
    lowered = candidate.lower()
    if not candidate:
        return ""
    if lowered.startswith(
        (
            "how about ",
            "what about ",
            "show me ",
            "tell me ",
            "give me ",
            "weather ",
            "forecast ",
        )
    ):
        return ""
    if any(marker in f" {lowered} " for marker in _WEATHER_MULTI_LOCATION_MARKERS):
        return ""
    if lowered in {"weather", "forecast", "today", "right now", "now", "currently"}:
        return ""
    return candidate


__all__ = [
    "extract_single_weather_location",
    "missing_simple_required_fields",
    "repair_structured_tool_arguments",
    "simple_required_fields",
    "synthesize_simple_tool_arguments",
    "tool_family_for_argument_repair",
]
