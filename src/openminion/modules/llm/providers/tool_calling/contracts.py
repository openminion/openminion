"""Tool-calling normalization and provider payload contracts."""

import html
import json
import re
from typing import Any, Iterable, List, Mapping, Sequence

from openminion.modules.llm.providers.base import ProviderToolCall, ProviderToolSpec
from openminion.modules.tool.contracts import (
    ALL_MODEL_TOOL_IDS_SET,
    strip_tool_wrapper_prefix,
)

_VALID_TOOL_CALL_STRATEGIES = {"off", "native", "fallback", "hybrid"}
_VALID_TOOL_CHOICES = {"auto", "none", "required"}

_JSON_BLOCK_RE = re.compile(
    r"```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```", re.IGNORECASE
)
_XML_BLOCK_RE = re.compile(
    r"<tool_calls?>\s*([\s\S]*?)\s*</tool_calls?>",
    re.IGNORECASE,
)
_MINIMAX_TOOL_CALL_RE = re.compile(
    r"<minimax:tool_call>[\s\S]*?</minimax:tool_call>", re.IGNORECASE
)
_MINIMAX_BRACKET_TOOL_CALL_RE = re.compile(
    r"\[TOOL_CALL\]([\s\S]*?)\[/TOOL_CALL\]", re.IGNORECASE
)
_MINIMAX_TOOL_NAME_RE = re.compile(
    r"<tool\s+name=[\"'](?P<name>[^\"']+)[\"']\s*>(?P<body>[\s\S]*?)</tool>",
    re.IGNORECASE,
)
_MINIMAX_FUNCTION_CALL_RE = re.compile(
    r"<functioncall>\s*(?P<body>[\s\S]*?)</functioncall>",
    re.IGNORECASE,
)
_MINIMAX_INVOKE_RE = re.compile(
    r"<invoke\s+name=[\"'](?P<name>[^\"']+)[\"']\s*>(?P<body>[\s\S]*?)</invoke>",
    re.IGNORECASE,
)
_MINIMAX_PARAMETER_RE = re.compile(
    r"<param(?:eter)?\s+name=[\"'](?P<name>[^\"']+)[\"']\s*>(?P<value>[\s\S]*?)</param(?:eter)?>",
    re.IGNORECASE,
)

_CHANNEL_ENVELOPE_RE = re.compile(
    r"<\|start\|>.*?<\|channel\|>.*?to=tool\.(?P<tool_name>[a-zA-Z0-9._-]+)\s*.*?<\|constrain\|>json\s*<\|message\|>(?P<json_args>\{[\s\S]*?\})\s*<\|call\|>",
    re.IGNORECASE,
)
_CHANNEL_ENVELOPE_GENERIC_RE = re.compile(
    r"<\|start\|>.*?<\|channel\|>.*?to=(?P<tool_target>[a-zA-Z0-9._-]+)\s*.*?(?:<\|constrain\|>json\s*)?<\|message\|>(?P<json_args>\{[\s\S]*?\})\s*<\|call\|>",
    re.IGNORECASE,
)
_CHANNEL_ENVELOPE_MALFORMED_RE = re.compile(
    r"<\|start\|>.*?<\|channel\|>.*?to=tool\.",
    re.IGNORECASE,
)
_RAW_ENVELOPE_RE = re.compile(
    r"<\|start\|>.*?<\|channel\|>.*?to=.*?<\|message\|>.*?<\|call\|>",
    re.IGNORECASE,
)
_RAW_TOOL_MARKUP_RE = re.compile(
    r"<minimax:tool_call>|</minimax:tool_call>|<tool_call>|</tool_call>|"
    r"<functioncall>|</functioncall>|"
    r"<invoke\s+name=|</invoke>|\[tool_call\]|\[/tool_call\]|<tool_code>|</tool_code>|"
    r"<tool\s+name=|</tool>|<param(?:eter)?\s+name=|</param(?:eter)?>",
    re.IGNORECASE,
)
_RAW_XML_TOOL_WRAPPER_RE = re.compile(
    r"<minimax:tool_call>|</minimax:tool_call>|<tool_call>|</tool_call>",
    re.IGNORECASE,
)

_ENVELOPE_TARGET_NAMESPACE_MAP: dict[str, str] = {}
_CANONICAL_MODEL_TOOL_ID_BY_LOWER = {
    model_tool_id.lower(): model_tool_id for model_tool_id in ALL_MODEL_TOOL_IDS_SET
}
_LEGACY_MODEL_TOOL_ALIASES = {
    "search": "web.search",
    "fetch": "web.fetch",
    "exec": "exec.run",
    "run_command": "exec.run",
    "lookup_weather": "weather",
}
_FAMILY_OPERATION_SUFFIX_ALIASES = {
    "ls": "list_dir",
    "list": "list_dir",
    "list_files": "list_dir",
    "read_file": "read",
    "read-range": "read_range",
    "delete": "trash",
    "remove": "trash",
}


def normalize_tool_call_strategy(raw_value: Any) -> str:
    normalized = str(raw_value or "").strip().lower()
    if normalized in _VALID_TOOL_CALL_STRATEGIES:
        return normalized
    return "hybrid"


def normalize_tool_choice(
    raw_value: Any,
    *,
    canonical_to_external: Mapping[str, str] | None = None,
) -> Any:
    return normalize_tool_choice_with_overrides(
        raw_value,
        canonical_to_external=canonical_to_external,
    )


def normalize_tool_choice_with_overrides(
    raw_value: Any,
    *,
    canonical_to_external: Mapping[str, str] | None = None,
) -> Any:
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in _VALID_TOOL_CHOICES:
            return normalized
        return "auto"
    if isinstance(raw_value, dict):
        name = str(raw_value.get("name", "")).strip()
        if name:
            external_name = (
                str(canonical_to_external.get(name, name)).strip()
                if canonical_to_external
                else name
            )
            return {"type": "function", "function": {"name": external_name}}
        function_payload = raw_value.get("function")
        if isinstance(function_payload, dict):
            name = str(function_payload.get("name", "")).strip()
            if name:
                external_name = (
                    str(canonical_to_external.get(name, name)).strip()
                    if canonical_to_external
                    else name
                )
                payload = dict(raw_value)
                payload["function"] = dict(function_payload)
                payload["function"]["name"] = external_name
                return payload
        return dict(raw_value)
    return "auto"


def supports_native_tool_calling(strategy: Any) -> bool:
    return normalize_tool_call_strategy(strategy) in {"native", "hybrid"}


def supports_fallback_tool_calling(strategy: Any) -> bool:
    return normalize_tool_call_strategy(strategy) in {"fallback", "hybrid"}


def _schema_branch_is_null(branch: Any) -> bool:
    if isinstance(branch, str):
        return branch == "null"
    if isinstance(branch, dict):
        branch_type = branch.get("type")
        if branch_type == "null":
            return True
        if isinstance(branch_type, list):
            normalized = [str(item).strip() for item in branch_type]
            return normalized == ["null"]
    return False


def _sanitize_openai_tool_schema(schema: Any) -> Any:
    """Strip nullable/default-null hints from tool input schemas."""

    if isinstance(schema, list):
        return [_sanitize_openai_tool_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    sanitized: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "default" and value is None:
            continue
        sanitized[key] = _sanitize_openai_tool_schema(value)

    any_of = sanitized.get("anyOf")
    if isinstance(any_of, list):
        non_null_branches = [
            branch for branch in any_of if not _schema_branch_is_null(branch)
        ]
        if non_null_branches and len(non_null_branches) != len(any_of):
            if len(non_null_branches) == 1 and isinstance(non_null_branches[0], dict):
                collapsed = dict(non_null_branches[0])
                for key, value in sanitized.items():
                    if key == "anyOf":
                        continue
                    collapsed.setdefault(key, value)
                sanitized = collapsed
            else:
                sanitized["anyOf"] = non_null_branches

    schema_type = sanitized.get("type")
    if isinstance(schema_type, list):
        non_null_types = [item for item in schema_type if str(item).strip() != "null"]
        if non_null_types and len(non_null_types) != len(schema_type):
            sanitized["type"] = (
                non_null_types[0] if len(non_null_types) == 1 else non_null_types
            )

    return sanitized


def build_openai_tools_payload(
    tools: Sequence[ProviderToolSpec],
    *,
    canonical_to_external: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for tool in tools:
        canonical_name = tool.name.strip()
        name = (
            str(canonical_to_external.get(canonical_name, canonical_name)).strip()
            if canonical_to_external
            else canonical_name
        )
        if not name:
            continue
        if hasattr(tool, "parameters") and isinstance(tool.parameters, dict):
            parameters = _sanitize_openai_tool_schema(tool.parameters)
        elif hasattr(tool, "input_schema") and isinstance(tool.input_schema, dict):
            parameters = _sanitize_openai_tool_schema(tool.input_schema)
        else:
            parameters = {}
        if not parameters:
            parameters = {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }
        function_payload: dict[str, Any] = {
            "name": name,
            "description": tool.description.strip() or f"Tool `{name}`",
            "parameters": parameters,
        }
        if tool.strict:
            function_payload["strict"] = True
        payload.append({"type": "function", "function": function_payload})
    return payload


def is_schema_only_submit_output_tools(tools: Sequence[ProviderToolSpec]) -> bool:
    normalized_tools = [tool for tool in tools if tool.name.strip()]
    if not normalized_tools:
        return False
    return all(tool.name.strip() == "submit_output" for tool in normalized_tools)


def build_fallback_tool_call_instruction(
    tools: Sequence[ProviderToolSpec],
    *,
    schema_only: bool | None = None,
    canonical_to_external: Mapping[str, str] | None = None,
) -> str:
    normalized_tools = [tool for tool in tools if tool.name.strip()]
    if not normalized_tools:
        return ""
    if schema_only is None:
        schema_only = is_schema_only_submit_output_tools(normalized_tools)

    tool_lines = []
    for tool in normalized_tools:
        canonical_name = tool.name.strip()
        external_name = (
            str(canonical_to_external.get(canonical_name, canonical_name)).strip()
            if canonical_to_external
            else canonical_name
        )
        tool_lines.append(f"- {external_name}: {tool.description.strip()}")

    clause_three = (
        "3. If no tool is required, return a normal assistant text response.\n"
    )
    if schema_only:
        clause_three = (
            "3. You MUST call `submit_output`. Plain text responses are not valid.\n"
        )

    return (
        "Tool-calling contract:\n"
        "1. If a tool is required, return a valid JSON object in this exact shape:\n"
        '{"tool_calls":[{"name":"<tool_name>","arguments":{}}]}\n'
        "2. Do not invent tool names. Use only the tools listed below.\n"
        + clause_three
        + "Available tools:\n"
        + "\n".join(tool_lines)
    )


def _strip_tool_wrapper_prefix(value: str) -> str:
    return strip_tool_wrapper_prefix(str(value or ""))


def _normalize_envelope_target(target: str) -> str | None:
    if not target:
        return None
    raw_target = str(target or "").strip()
    if raw_target in _ENVELOPE_TARGET_NAMESPACE_MAP:
        return _ENVELOPE_TARGET_NAMESPACE_MAP[raw_target]
    canonical = _canonical_model_tool_id(raw_target)
    if canonical:
        return canonical
    target = _strip_tool_wrapper_prefix(raw_target)
    if target in _ENVELOPE_TARGET_NAMESPACE_MAP:
        return _ENVELOPE_TARGET_NAMESPACE_MAP[target]
    canonical = _canonical_model_tool_id(target)
    if canonical:
        return canonical
    return target


def detect_raw_envelope(text: str) -> bool:
    if not text:
        return False
    return bool(_RAW_ENVELOPE_RE.search(text))


def detect_raw_tool_markup(text: str) -> bool:
    if not text:
        return False
    return bool(_RAW_TOOL_MARKUP_RE.search(str(text)))


def detect_raw_xml_tool_wrapper(text: str) -> bool:
    if not text:
        return False
    return bool(_RAW_XML_TOOL_WRAPPER_RE.search(str(text)))


def detect_raw_tool_payload_json(text: str) -> bool:
    if not text:
        return False
    token = str(text or "").strip()
    lower = token.lower()
    if not (
        token.startswith("{")
        or token.startswith("[")
        or token.startswith("```")
        or "```json" in lower
    ):
        return False
    if not (
        '"tool"' in lower
        or '"tool_calls"' in lower
        or '"name"' in lower
        or '":op"' in lower
    ):
        return False
    return any(
        key in lower
        for key in (
            '"arguments"',
            '"args"',
            '":args"',
            '"command"',
            '"path"',
            '"query"',
        )
    )


def sanitize_envelope_leak(text: str, *, metadata: dict[str, Any] | None = None) -> str:
    if detect_raw_envelope(text):
        if metadata and metadata.get("envelope_target_normalized"):
            return text
        reason = (
            metadata.get("envelope_rejected_reason", "unknown")
            if metadata
            else "unparseable"
        )
        raw_target = (
            metadata.get("envelope_target_raw", "unknown") if metadata else "unknown"
        )
    elif detect_raw_tool_markup(text):
        if metadata and metadata.get("fallback_tool_name_normalized"):
            return text
        reason = (
            metadata.get("fallback_rejected_reason", "unknown")
            if metadata
            else "unparseable"
        )
        raw_target = (
            metadata.get("fallback_tool_name_raw", "unknown") if metadata else "unknown"
        )
    else:
        return text

    return (
        "[system: UNEXECUTABLE_TOOL_ENVELOPE]\n"
        f"The model generated a tool envelope that could not be executed.\n"
        f"Target: {raw_target}\n"
        f"Reason: {reason}\n"
        "This response has been blocked to prevent raw markup leak."
    )


def _normalize_allowed_tool_names(
    allowed_tool_names: Iterable[str] | None,
) -> set[str] | None:
    if allowed_tool_names is None:
        return None
    normalized = {str(name).strip() for name in allowed_tool_names if str(name).strip()}
    return normalized


def _normalize_legacy_operation_token(raw_value: Any) -> str:
    token = _normalize_tool_name(str(raw_value or "").strip())
    return _FAMILY_OPERATION_SUFFIX_ALIASES.get(token, token)


def _resolve_family_wrapper_operation_candidate(
    *,
    family_name: str,
    arguments: Mapping[str, Any] | None,
    allowed_tool_names: set[str] | None,
) -> str | None:
    family = _normalize_tool_name(family_name)
    if not family:
        return None
    op_token = _normalize_legacy_operation_token((arguments or {}).get("operation"))
    if not op_token:
        op_token = _infer_family_wrapper_operation_token(
            family_name=family,
            arguments=arguments,
        )
    if not op_token:
        return None

    allowed_scope = list(allowed_tool_names or ALL_MODEL_TOOL_IDS_SET)
    prefix_matches = []
    dotted_prefix = f"{family}."
    for candidate in allowed_scope:
        token = str(candidate or "").strip()
        if not token:
            continue
        canonical = _canonical_model_tool_id(token) or token
        if canonical.startswith(dotted_prefix):
            prefix_matches.append(canonical)

    normalized_matches = [
        candidate
        for candidate in prefix_matches
        if _normalize_tool_name(candidate[len(dotted_prefix) :]) == op_token
    ]
    if len(normalized_matches) == 1:
        return normalized_matches[0]
    return None


def _infer_family_wrapper_operation_token(
    *,
    family_name: str,
    arguments: Mapping[str, Any] | None,
) -> str:
    """Infer operation for provider wrapper tools from structural arguments.

    Some OpenAI-compatible providers emit a family-level function name such as
    ``file`` while placing the operation in the argument shape. Keep this
    deliberately narrow: only infer operations when the schema shape is
    unambiguous, and leave broader semantic routing to the model/tool contract.
    """

    args = arguments or {}
    if family_name == "file":
        has_path = "path" in args or "file_path" in args
        if has_path and "content" in args:
            return "write"
    return ""


def _coerce_tool_arguments_for_resolved_tool(
    resolved_name: str,
    raw_arguments: Any,
) -> dict[str, Any]:
    arguments = _coerce_tool_arguments(raw_arguments)
    if resolved_name == "file.write":
        _copy_arg_alias(arguments, source="file_path", target="path")
    return arguments


def _copy_arg_alias(
    arguments: dict[str, Any],
    *,
    source: str,
    target: str,
) -> None:
    if target not in arguments and source in arguments:
        arguments[target] = arguments[source]


def _json_payload_candidates(text: str) -> List[str]:
    raw = str(text or "").strip()
    candidates: list[str] = []
    if raw:
        candidates.append(raw)
        embedded = _extract_embedded_json_candidate(raw)
        if embedded and embedded != raw:
            candidates.append(embedded)
    for match in _JSON_BLOCK_RE.finditer(raw):
        candidates.append(match.group(1).strip())
    for match in _XML_BLOCK_RE.finditer(raw):
        candidates.append(match.group(1).strip())
    candidates.extend(_embedded_json_value_candidates(raw))
    return candidates


def _embedded_json_value_candidates(text: str) -> list[str]:
    decoder = json.JSONDecoder()
    raw = str(text or "")
    candidates: list[str] = []
    seen: set[tuple[int, int]] = set()
    occupied_spans: list[tuple[int, int]] = []
    for index, char in enumerate(raw):
        if char not in "{[":
            continue
        if any(start < index < end for start, end in occupied_spans):
            continue
        try:
            _, end = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        span = (index, index + end)
        if span in seen:
            continue
        seen.add(span)
        occupied_spans.append(span)
        candidate = raw[span[0] : span[1]].strip()
        if candidate:
            candidates.append(candidate)
    return candidates


def _extract_embedded_json_candidate(text: str) -> str | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    obj_start = raw.find("{")
    obj_end = raw.rfind("}")
    if obj_start >= 0 and obj_end > obj_start:
        candidate = raw[obj_start : obj_end + 1].strip()
        if candidate:
            return candidate
    list_start = raw.find("[")
    list_end = raw.rfind("]")
    if list_start >= 0 and list_end > list_start:
        candidate = raw[list_start : list_end + 1].strip()
        if candidate:
            return candidate
    return None


def _decode_json(raw_payload: str) -> Any | None:
    candidate = str(raw_payload or "").strip()
    if not candidate:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        repaired = _repair_trailing_unbalanced_json(candidate, exc)
        if not repaired:
            return None
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            return None


def _repair_trailing_unbalanced_json(
    candidate: str,
    error: json.JSONDecodeError,
) -> str:
    """Repair EOF-truncated JSON objects emitted inside bounded tool envelopes."""

    token = str(candidate or "").strip()
    if not token or error.pos < len(token) - 1:
        return ""

    stack: list[str] = []
    in_string = False
    escaped = False
    for char in token:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack:
                return ""
            opener = stack.pop()
            if (opener, char) not in {("{", "}"), ("[", "]")}:
                return ""

    if in_string or not stack or len(stack) > 3:
        return ""
    suffix = "".join("}" if opener == "{" else "]" for opener in reversed(stack))
    return f"{token}{suffix}"


def _tool_calls_from_payload(
    payload: Any,
    *,
    source: str,
    allowed_tool_names: set[str] | None,
) -> list[ProviderToolCall]:
    if isinstance(payload, dict) and isinstance(payload.get("tool_calls"), list):
        return _parse_tool_call_items(
            payload["tool_calls"],
            source=source,
            allowed_tool_names=allowed_tool_names,
        )
    if isinstance(payload, dict) and any(
        isinstance(payload.get(key), str) for key in ("name", "tool_name", "tool")
    ):
        return _parse_tool_call_items(
            [payload],
            source=source,
            allowed_tool_names=allowed_tool_names,
        )
    if isinstance(payload, dict):
        inferred = _infer_single_tool_call_from_schema_payload(
            payload,
            source=source,
            allowed_tool_names=allowed_tool_names,
        )
        if inferred is not None:
            return [inferred]
    if isinstance(payload, dict) and _is_submit_output_only_allowed(allowed_tool_names):
        submit_payload = _coerce_submit_output_payload(payload)
        if submit_payload is not None:
            return [
                ProviderToolCall(
                    id="",
                    name="submit_output",
                    arguments=submit_payload,
                    source=source,
                )
            ]
    if isinstance(payload, list):
        return _parse_tool_call_items(
            payload,
            source=source,
            allowed_tool_names=allowed_tool_names,
        )
    return []


def _infer_single_tool_call_from_schema_payload(
    payload: Mapping[str, Any],
    *,
    source: str,
    allowed_tool_names: set[str] | None,
) -> ProviderToolCall | None:
    resolved_name = ""
    if "path" in payload and "content" in payload:
        resolved_name = (
            _resolve_allowed_tool_name(
                "file.write",
                allowed_tool_names=allowed_tool_names,
                arguments=payload,
            )
            or ""
        )
    elif "command" in payload:
        resolved_name = (
            _resolve_allowed_tool_name(
                "exec.run",
                allowed_tool_names=allowed_tool_names,
                arguments=payload,
            )
            or ""
        )
    if not resolved_name:
        return None
    return ProviderToolCall(
        id=str(payload.get("id", "") or "").strip(),
        name=resolved_name,
        arguments=_coerce_tool_arguments_for_resolved_tool(
            resolved_name,
            dict(payload),
        ),
        source=source,
    )


def _parse_tool_call_items(
    items: Sequence[Any],
    *,
    source: str,
    allowed_tool_names: set[str] | None,
) -> list[ProviderToolCall]:
    parsed: list[ProviderToolCall] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(
            item.get(
                "name",
                item.get("tool_name", item.get("tool", item.get(":op", ""))),
            )
        ).strip()
        raw_arguments: Any = item.get("arguments")
        if raw_arguments is None:
            raw_arguments = item.get(":args")
        if raw_arguments is None:
            raw_arguments = item.get("parameters")
        if raw_arguments is None:
            raw_arguments = item.get("tool_input")
        if raw_arguments is None:
            raw_arguments = _coerce_inline_tool_arguments(item)
        arguments = _coerce_tool_arguments(raw_arguments)
        resolved_name = _resolve_allowed_tool_name(
            name, allowed_tool_names=allowed_tool_names, arguments=arguments
        )
        if not resolved_name:
            continue
        parsed.append(
            ProviderToolCall(
                id=str(item.get("id", item.get("tool_call_id", ""))).strip(),
                name=resolved_name,
                arguments=_coerce_tool_arguments_for_resolved_tool(
                    resolved_name,
                    arguments,
                ),
                source=source,
            )
        )
    return parsed


def _coerce_inline_tool_arguments(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in dict(item).items()
        if key
        not in {
            "id",
            "tool_call_id",
            "name",
            "tool_name",
            "tool",
            ":op",
            "type",
            "arguments",
            ":args",
            "parameters",
            "tool_input",
        }
    }


def _normalize_tool_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def canonicalize_tool_name_for_runtime(raw_name: str) -> str:
    raw = str(raw_name or "").strip()
    if not raw:
        return ""
    canonical = _canonical_model_tool_id(raw)
    if canonical:
        return canonical
    normalized = _normalize_tool_name(raw)
    normalized_matches = [
        candidate
        for candidate in ALL_MODEL_TOOL_IDS_SET
        if _normalize_tool_name(candidate) == normalized
    ]
    if len(normalized_matches) == 1:
        return normalized_matches[0]
    stripped = _strip_tool_wrapper_prefix(raw)
    if stripped and stripped != raw:
        canonical = _canonical_model_tool_id(stripped)
        if canonical:
            return canonical
        normalized = _normalize_tool_name(stripped)
        normalized_matches = [
            candidate
            for candidate in ALL_MODEL_TOOL_IDS_SET
            if _normalize_tool_name(candidate) == normalized
        ]
        if len(normalized_matches) == 1:
            return normalized_matches[0]
        return stripped
    return raw


def _resolve_allowed_tool_name(
    raw_name: str,
    *,
    allowed_tool_names: set[str] | None,
    arguments: Mapping[str, Any] | None = None,
) -> str | None:
    raw = str(raw_name or "").strip()
    if not raw:
        return None

    def _resolve_candidate(name: str) -> str | None:
        canonical_name = _canonical_model_tool_id(name)
        if canonical_name:
            if allowed_tool_names is None:
                return canonical_name
            allowed_canonical = {
                candidate
                for candidate in (
                    _canonical_model_tool_id(item) for item in allowed_tool_names
                )
                if candidate
            }
            if canonical_name in allowed_canonical:
                return canonical_name
            return None

        if allowed_tool_names is not None:
            lowered = name.lower()
            for candidate in allowed_tool_names:
                normalized_candidate = str(candidate or "").strip()
                if normalized_candidate and normalized_candidate.lower() == lowered:
                    return normalized_candidate
            normalized = _normalize_tool_name(name)
            normalized_matches = [
                str(candidate or "").strip()
                for candidate in allowed_tool_names
                if str(candidate or "").strip()
                and _normalize_tool_name(str(candidate or "").strip()) == normalized
            ]
            if len(normalized_matches) == 1:
                only_match = normalized_matches[0]
                return _canonical_model_tool_id(only_match) or only_match
        return None

    resolved = _resolve_candidate(raw)
    if resolved:
        return resolved
    family_resolved = _resolve_family_wrapper_operation_candidate(
        family_name=raw,
        arguments=arguments,
        allowed_tool_names=allowed_tool_names,
    )
    if family_resolved:
        return family_resolved

    name = _strip_tool_wrapper_prefix(raw)
    if not name or name == raw:
        return None
    resolved = _resolve_candidate(name)
    if resolved:
        return resolved
    return _resolve_family_wrapper_operation_candidate(
        family_name=name,
        arguments=arguments,
        allowed_tool_names=allowed_tool_names,
    )


def _canonical_model_tool_id(raw_name: str) -> str | None:
    token = str(raw_name or "").strip()
    if not token:
        return None
    alias = _LEGACY_MODEL_TOOL_ALIASES.get(token.lower())
    if alias:
        return alias
    if token in ALL_MODEL_TOOL_IDS_SET:
        return token
    direct = _CANONICAL_MODEL_TOOL_ID_BY_LOWER.get(token.lower())
    if direct:
        return direct
    normalized = _normalize_tool_name(token)
    normalized_matches = [
        candidate
        for candidate in ALL_MODEL_TOOL_IDS_SET
        if _normalize_tool_name(candidate) == normalized
    ]
    if len(normalized_matches) == 1:
        return normalized_matches[0]
    return None


def _coerce_tool_arguments(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return dict(raw_arguments)
    if isinstance(raw_arguments, str):
        candidate = raw_arguments.strip()
        if not candidate:
            return {}
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return dict(parsed)
    return {}


def _is_submit_output_only_allowed(allowed_tool_names: set[str] | None) -> bool:
    if not allowed_tool_names:
        return False
    normalized = {
        _strip_tool_wrapper_prefix(name)
        for name in allowed_tool_names
        if _strip_tool_wrapper_prefix(name)
    }
    return normalized == {"submit_output"}


def _coerce_submit_output_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    candidate: Any = dict(payload or {})
    for key in ("decision", "Decision", "output", "result", "payload", "inputs"):
        if not isinstance(candidate, dict):
            break
        raw_value = candidate.get(key)
        parsed = _coerce_tool_arguments(raw_value)
        if parsed:
            candidate = parsed
            break
        if isinstance(raw_value, dict):
            candidate = dict(raw_value)
            break
    if isinstance(candidate, dict):
        return dict(candidate)
    return None


def _extract_raw_tool_name_from_payload(payload: Any) -> str | None:
    if isinstance(payload, dict):
        tool_calls = payload.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            first_call = tool_calls[0]
            if isinstance(first_call, dict):
                return str(first_call.get("name", "")).strip() or None
        name = payload.get("name")
        if isinstance(name, str):
            return name.strip() or None
        operation = payload.get(":op")
        if isinstance(operation, str):
            return operation.strip() or None
    elif isinstance(payload, list) and payload:
        first_item = payload[0]
        if isinstance(first_item, dict):
            return (
                str(first_item.get("name") or first_item.get(":op") or "").strip()
                or None
            )
    return None


def _coerce_minimax_parameter_value(raw_value: Any) -> Any:
    if raw_value is None:
        return ""
    token = html.unescape(str(raw_value)).strip()
    if not token:
        return ""
    if token.startswith("{") or token.startswith("["):
        try:
            return json.loads(token)
        except json.JSONDecodeError:
            return token
    lowered = token.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    return token


def _parse_minimax_bracket_args(body: str) -> dict[str, Any]:
    if not body:
        return {}
    args: dict[str, Any] = {}
    for param_match in re.finditer(
        r"--(?P<name>[a-zA-Z0-9_\-]+)\s+(?P<value>\"[\s\S]*?\"|'[\s\S]*?'|[^\s}]+)",
        body,
    ):
        name = param_match.group("name").strip()
        value = param_match.group("value").strip()
        if not name:
            continue
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        args[name] = _coerce_minimax_parameter_value(value)
    return args
