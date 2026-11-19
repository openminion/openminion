import json
import re
from collections.abc import Collection, Mapping
from typing import Any

from pydantic import BaseModel

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
    if family in {MODEL_TIME, MODEL_LOCATION, MODEL_WEATHER}:
        return ()
    if family == MODEL_WEB_SEARCH:
        return ("query",)
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
        if any(
            str(normalized_args.get(key, "")).strip()
            for key in ("location", "city", "query", "place")
        ):
            return normalized_args
        location = extract_single_weather_location(user_input)
        if location:
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


def repair_model_tool_arguments(
    arguments: dict[str, Any],
    *,
    args_model: Any,
    channel_name: str,
) -> dict[str, Any] | str | bytes:
    return repair_structured_tool_arguments(
        arguments,
        channel_name=channel_name,
        alias_map=_model_alias_map(args_model),
        type_coercions=_model_type_coercions(args_model),
    )


def validate_model_tool_args(
    arguments: dict[str, Any], args_model: Any, tool_name: str
) -> dict[str, Any]:
    repaired = repair_model_tool_arguments(
        arguments,
        args_model=args_model,
        channel_name=f"model_tool:{tool_name}.args",
    )
    validated = args_model.model_validate(repaired)
    return accepted_tool_args(validated, repaired)


def accepted_tool_args(
    validated: BaseModel,
    accepted: Any,
    *,
    input_keys: Collection[str] | None = None,
) -> dict[str, Any]:
    """Return a canonical dump only when accepted input keys and shape stay valid."""
    dumped = validated.model_dump()
    if not isinstance(accepted, Mapping):
        return dumped
    if not _model_dump_accepts_input_keys(validated):
        return dict(accepted)
    if input_keys is not None and not set(dumped) <= set(input_keys):
        return dict(accepted)
    return dumped


def _model_config_accepts_field_names(model_type: Any) -> bool:
    config = getattr(model_type, "model_config", {}) or {}
    validate_by_name = config.get("validate_by_name")
    if validate_by_name is not None:
        return bool(validate_by_name)
    return bool(config.get("populate_by_name"))


def _field_accepts_input_key(
    model_type: Any, field_name: str, field_info: Any, key: str
) -> bool:
    config = getattr(model_type, "model_config", {}) or {}
    validation_alias = getattr(field_info, "validation_alias", None)
    if validation_alias is None:
        validation_alias = getattr(field_info, "alias", None)

    if config.get("validate_by_alias") is not False and _validation_alias_contains_key(
        validation_alias, key
    ):
        return True
    if key != field_name:
        return False
    has_renamed_alias = validation_alias is not None and not (
        _validation_alias_contains_key(validation_alias, field_name)
    )
    return not has_renamed_alias or _model_config_accepts_field_names(model_type)


def _validation_alias_contains_key(validation_alias: Any, key: str) -> bool:
    if validation_alias == key:
        return True
    choices = getattr(validation_alias, "choices", ())
    if any(_validation_alias_contains_key(choice, key) for choice in choices):
        return True
    path = getattr(validation_alias, "path", ())
    return len(path) == 1 and path[0] == key


def _model_dump_accepts_input_keys(
    model: BaseModel, seen: set[int] | None = None
) -> bool:
    # RootModel serializes its root value directly; preserve the accepted input shape.
    if getattr(type(model), "__pydantic_root_model__", False):
        return False
    visited = seen if seen is not None else set()
    identity = id(model)
    if identity in visited:
        return True
    visited.add(identity)

    config = getattr(type(model), "model_config", {}) or {}
    serialize_by_alias = bool(config.get("serialize_by_alias"))
    for field_name, field_info in type(model).model_fields.items():
        serialized_key = field_name
        if serialize_by_alias:
            serialized_key = (
                getattr(field_info, "serialization_alias", None)
                or getattr(field_info, "alias", None)
                or field_name
            )
        if not _field_accepts_input_key(
            type(model), field_name, field_info, serialized_key
        ):
            return False
        if not _nested_models_accept_input_keys(getattr(model, field_name), visited):
            return False
    return True


def _nested_models_accept_input_keys(value: Any, seen: set[int]) -> bool:
    if isinstance(value, BaseModel):
        return _model_dump_accepts_input_keys(value, seen)
    if isinstance(value, Mapping):
        return all(
            _nested_models_accept_input_keys(item, seen) for item in value.values()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return all(_nested_models_accept_input_keys(item, seen) for item in value)
    return True


def _model_alias_map(args_model: Any) -> dict[str, str]:
    fields = getattr(args_model, "model_fields", None)
    if not isinstance(fields, Mapping):
        return {}
    # Do not turn a disabled alias input into an accepted field-name input.
    config = getattr(args_model, "model_config", None) or {}
    if config.get("validate_by_alias") is False:
        return {}
    if not _model_config_accepts_field_names(args_model):
        return {}
    alias_map: dict[str, str] = {}
    for field_name, field_info in fields.items():
        alias = str(getattr(field_info, "alias", "") or "").strip()
        canonical = str(field_name or "").strip()
        if alias and canonical and alias != canonical:
            alias_map[alias] = canonical
    return alias_map


def _model_type_coercions(args_model: Any) -> dict[str, Any]:
    schema_builder = getattr(args_model, "model_json_schema", None)
    if not callable(schema_builder):
        return {}
    schema = schema_builder()
    definitions = schema.get("$defs", {}) if isinstance(schema, Mapping) else {}
    properties: dict[str, Any] = {}
    for candidate in (schema, *(schema.get("anyOf", []) or [])):
        resolved = _resolve_schema(candidate, definitions)
        if isinstance(resolved, Mapping):
            properties.update(resolved.get("properties", {}) or {})
    return {
        str(name): _decode_json_container
        for name, field_schema in properties.items()
        if _structured_schema_field(field_schema, definitions)
    }


def _resolve_schema(schema: Any, definitions: Mapping[str, Any]) -> Any:
    if not isinstance(schema, Mapping):
        return schema
    ref = str(schema.get("$ref", "") or "")
    prefix = "#/$defs/"
    if ref.startswith(prefix):
        return definitions.get(ref[len(prefix) :], schema)
    return schema


def _structured_schema_field(schema: Any, definitions: Mapping[str, Any]) -> bool:
    resolved = _resolve_schema(schema, definitions)
    return isinstance(resolved, Mapping) and resolved.get("type") in {
        "array",
        "object",
    }


def _decode_json_container(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped.startswith(("{", "[")):
        return value
    decoded = json.loads(stripped)
    return decoded if isinstance(decoded, (dict, list)) else value


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
    "accepted_tool_args",
    "extract_single_weather_location",
    "missing_simple_required_fields",
    "repair_model_tool_arguments",
    "repair_structured_tool_arguments",
    "simple_required_fields",
    "synthesize_simple_tool_arguments",
    "tool_family_for_argument_repair",
    "validate_model_tool_args",
]
