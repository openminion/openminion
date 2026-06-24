import json
import re
from typing import Any, Iterable

from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.llm.providers.tool_calling.base import ToolCallParseResult
from openminion.modules.llm.providers.tool_calling.contracts import (
    _MINIMAX_FUNCTION_CALL_RE,
    _MINIMAX_INVOKE_RE,
    _MINIMAX_PARAMETER_RE,
    _MINIMAX_TOOL_CALL_RE,
    _MINIMAX_TOOL_NAME_RE,
    _coerce_minimax_parameter_value,
    _normalize_allowed_tool_names,
    _normalize_tool_name,
    _resolve_allowed_tool_name,
)


class MinimaxXmlToolCallParser:
    name = "minimax_xml"

    def parse_text(
        self,
        text: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> ToolCallParseResult:
        raw = str(text or "").strip()
        if not raw:
            return ToolCallParseResult()

        allowed = _normalize_allowed_tool_names(allowed_tool_names)
        blocks = [raw]
        if _MINIMAX_TOOL_CALL_RE.search(raw):
            blocks = [match.group(0) for match in _MINIMAX_TOOL_CALL_RE.finditer(raw)]

        parsed: list[ProviderToolCall] = []
        raw_name: str | None = None
        for block in blocks:
            for invoke_match in _MINIMAX_INVOKE_RE.finditer(block):
                name_raw = invoke_match.group("name").strip()
                if raw_name is None and name_raw:
                    raw_name = name_raw

                args: dict[str, Any] = {}
                body = invoke_match.group("body")
                for param_match in _MINIMAX_PARAMETER_RE.finditer(body):
                    param_name = param_match.group("name").strip()
                    if not param_name:
                        continue
                    raw_value = param_match.group("value")
                    args[param_name] = _coerce_minimax_parameter_value(raw_value)

                resolved_name = _resolve_allowed_tool_name(
                    name_raw,
                    allowed_tool_names=allowed,
                )
                normalized_wrapper = _normalize_tool_name(name_raw)
                if normalized_wrapper in {"tool_use", "tooluse"}:
                    wrapped = self._resolve_tool_use_wrapper(
                        args, allowed_tool_names=allowed
                    )
                    if wrapped is not None:
                        resolved_name, args, wrapped_raw_name = wrapped
                        if raw_name is None and wrapped_raw_name:
                            raw_name = wrapped_raw_name
                if not resolved_name:
                    continue

                parsed.append(
                    ProviderToolCall(
                        id="",
                        name=resolved_name,
                        arguments=args,
                        source="fallback",
                    )
                )

        # Some OpenRouter+Minimax responses emit a <FunctionCall> wrapper with
        # ruby-style key/value pairs and <param ...> arguments.
        if not parsed:
            for function_call_match in _MINIMAX_FUNCTION_CALL_RE.finditer(raw):
                body = function_call_match.group("body")
                name_raw = self._extract_function_call_name(body)
                if raw_name is None and name_raw:
                    raw_name = name_raw

                args: dict[str, Any] = {}
                for param_match in _MINIMAX_PARAMETER_RE.finditer(body):
                    param_name = param_match.group("name").strip()
                    if not param_name:
                        continue
                    raw_value = param_match.group("value")
                    args[param_name] = _coerce_minimax_parameter_value(raw_value)

                resolved_name = _resolve_allowed_tool_name(
                    name_raw,
                    allowed_tool_names=allowed,
                )
                normalized_wrapper = _normalize_tool_name(name_raw)
                if normalized_wrapper in {"tool_use", "tooluse"}:
                    wrapped = self._resolve_tool_use_wrapper(
                        args, allowed_tool_names=allowed
                    )
                    if wrapped is not None:
                        resolved_name, args, wrapped_raw_name = wrapped
                        if raw_name is None and wrapped_raw_name:
                            raw_name = wrapped_raw_name
                if not resolved_name:
                    continue

                parsed.append(
                    ProviderToolCall(
                        id="",
                        name=resolved_name,
                        arguments=args,
                        source="fallback",
                    )
                )

        # Some responses use a lightweight <tool_code> block:
        # <tool_code>tool: browser_navigate args: { url: "https://..." }</tool_code>
        if not parsed:
            for tool_code_match in re.finditer(
                r"<tool_code>\s*(?P<body>[\s\S]*?)</tool_code>",
                raw,
                re.IGNORECASE,
            ):
                body = str(tool_code_match.group("body") or "")
                name_raw = self._extract_tool_code_name(body)
                if raw_name is None and name_raw:
                    raw_name = name_raw
                if not name_raw:
                    continue
                args = self._extract_tool_code_args(body)
                resolved_name = _resolve_allowed_tool_name(
                    name_raw,
                    allowed_tool_names=allowed,
                )
                if not resolved_name:
                    continue
                parsed.append(
                    ProviderToolCall(
                        id="",
                        name=resolved_name,
                        arguments=args,
                        source="fallback",
                    )
                )

        # Support for <tool name="..."> variant observed in MiniMax responses
        if not parsed:
            for tool_match in _MINIMAX_TOOL_NAME_RE.finditer(raw):
                name_raw = tool_match.group("name").strip()
                if raw_name is None and name_raw:
                    raw_name = name_raw
                if not name_raw:
                    continue
                body = tool_match.group("body")
                args: dict[str, Any] = {}
                for param_match in _MINIMAX_PARAMETER_RE.finditer(body):
                    param_name = param_match.group("name").strip()
                    if not param_name:
                        continue
                    raw_value = param_match.group("value")
                    args[param_name] = _coerce_minimax_parameter_value(raw_value)
                resolved_name = _resolve_allowed_tool_name(
                    name_raw,
                    allowed_tool_names=allowed,
                )
                if not resolved_name:
                    continue
                parsed.append(
                    ProviderToolCall(
                        id="",
                        name=resolved_name,
                        arguments=args,
                        source="fallback",
                    )
                )

        if parsed:
            metadata = {"fallback_parse_mode": "minimax_xml"}
            if raw_name:
                metadata["fallback_tool_name_raw"] = raw_name
            first_name = str(parsed[0].name or "").strip()
            if first_name:
                metadata["fallback_tool_name_normalized"] = first_name
            return ToolCallParseResult(calls=parsed, metadata=metadata)
        if raw_name:
            return ToolCallParseResult(
                metadata={
                    "fallback_parse_mode": "minimax_xml",
                    "fallback_tool_name_raw": raw_name,
                    "fallback_rejected_reason": "tool_not_allowed",
                }
            )
        return ToolCallParseResult()

    @staticmethod
    def _extract_function_call_name(block: str) -> str:
        for pattern in (
            r"[\"']tool[\"']\s*=>\s*[\"'](?P<name>[^\"']+)[\"']",
            r"[\"']name[\"']\s*=>\s*[\"'](?P<name>[^\"']+)[\"']",
            r"tool\s*=>\s*[\"'](?P<name>[^\"']+)[\"']",
        ):
            match = re.search(pattern, block, re.IGNORECASE)
            if match:
                return str(match.group("name") or "").strip()
        return ""

    @staticmethod
    def _extract_tool_code_name(block: str) -> str:
        match = re.search(
            r"(?:^|\n)\s*(?:tool|name)\s*:\s*(?P<name>[a-zA-Z0-9._-]+)",
            block,
            re.IGNORECASE,
        )
        if not match:
            return ""
        return str(match.group("name") or "").strip()

    @staticmethod
    def _extract_tool_code_args(block: str) -> dict[str, Any]:
        match = re.search(
            r"(?:^|\n)\s*args\s*:\s*\{(?P<body>[\s\S]*?)\}",
            block,
            re.IGNORECASE,
        )
        if not match:
            return {}
        args_body = str(match.group("body") or "")
        parsed: dict[str, Any] = {}
        for arg_match in re.finditer(
            r"(?P<name>[a-zA-Z0-9_\-]+)\s*:\s*(?P<value>\"[\s\S]*?\"|'[\s\S]*?'|[^,\n]+)",
            args_body,
        ):
            arg_name = str(arg_match.group("name") or "").strip()
            raw_value = str(arg_match.group("value") or "").strip()
            if not arg_name:
                continue
            if (raw_value.startswith('"') and raw_value.endswith('"')) or (
                raw_value.startswith("'") and raw_value.endswith("'")
            ):
                raw_value = raw_value[1:-1]
            parsed[arg_name] = _coerce_minimax_parameter_value(raw_value)
        return parsed

    @staticmethod
    def _resolve_tool_use_wrapper(
        args: dict[str, Any],
        *,
        allowed_tool_names: set[str] | None,
    ) -> tuple[str, dict[str, Any], str] | None:
        tool_name_raw = str(
            args.get("tool_name") or args.get("tool") or args.get("name") or ""
        ).strip()
        if not tool_name_raw:
            return None

        resolved_name = _resolve_allowed_tool_name(
            tool_name_raw,
            allowed_tool_names=allowed_tool_names,
        )
        if not resolved_name:
            return None

        payload = args.get("arguments", args.get("args", {}))
        normalized_args = MinimaxXmlToolCallParser._coerce_arguments(payload)
        if not normalized_args:
            normalized_args = {
                key: value
                for key, value in args.items()
                if key not in {"tool_name", "tool", "name", "arguments", "args"}
            }

        return resolved_name, normalized_args, tool_name_raw

    @staticmethod
    def _coerce_arguments(raw_payload: Any) -> dict[str, Any]:
        if isinstance(raw_payload, dict):
            return dict(raw_payload)
        if isinstance(raw_payload, str):
            candidate = _coerce_minimax_parameter_value(raw_payload)
            if isinstance(candidate, dict):
                return dict(candidate)
            token = str(raw_payload).strip()
            if not token:
                return {}
            try:
                decoded = json.loads(token)
            except json.JSONDecodeError:
                return {}
            if isinstance(decoded, dict):
                return dict(decoded)
        return {}
