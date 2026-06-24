from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from openminion.modules.brain.config import (
    TOOL_SCHEMA_SHORTLIST_MAX_ACTIVE,
    TOOL_SCHEMA_SHORTLIST_THRESHOLD,
)
from openminion.modules.llm.schemas import LLMResponse, Message, ToolSpec

TOOL_REQUEST_TOOL_NAME = "tool.request"
_JSON_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*(?P<body>.*?)\s*```\s*$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class ToolSchemaShortlistResult:
    active_tool_specs: tuple[ToolSpec, ...]
    requestable_tool_specs: tuple[ToolSpec, ...]
    selected_tool_names: tuple[str, ...]
    inactive_tool_names: tuple[str, ...]
    enabled: bool
    reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    llm_call_made: bool = False

    @property
    def total_tokens(self) -> int:
        return int(self.input_tokens or 0) + int(self.output_tokens or 0)

    def scratchpad_payload(self) -> dict[str, Any]:
        return {
            "tool_schema_shortlisting.enabled": self.enabled,
            "tool_schema_shortlisting.reason": self.reason,
            "tool_schema_shortlisting.candidate_count": len(
                self.requestable_tool_specs
            ),
            "tool_schema_shortlisting.active_count": len(self.active_tool_specs),
            "tool_schema_shortlisting.selected_tools": list(self.selected_tool_names),
            "tool_schema_shortlisting.inactive_tools": list(self.inactive_tool_names),
            "tool_schema_shortlisting.input_tokens": int(self.input_tokens or 0),
            "tool_schema_shortlisting.output_tokens": int(self.output_tokens or 0),
            "tool_schema_shortlisting.total_tokens": self.total_tokens,
            "tool_schema_shortlisting.llm_call_made": self.llm_call_made,
        }


def build_tool_request_spec() -> ToolSpec:
    return ToolSpec(
        name=TOOL_REQUEST_TOOL_NAME,
        description=(
            "Activate one inactive tool schema for this adaptive loop. Use an exact "
            "tool name from the inactive tool directory."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Exact inactive tool name to activate.",
                }
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    )


def with_tool_request_spec(tool_specs: Sequence[ToolSpec]) -> list[ToolSpec]:
    specs = [
        spec
        for spec in tool_specs
        if str(getattr(spec, "name", "") or "").strip() != TOOL_REQUEST_TOOL_NAME
    ]
    specs.append(build_tool_request_spec())
    return specs


def build_inactive_tool_directory_message(
    *,
    requestable_tool_specs: Sequence[ToolSpec],
    active_tool_names: set[str] | frozenset[str],
) -> Message | None:
    active_names = {
        str(name).strip() for name in active_tool_names if str(name).strip()
    }
    lines = [
        "[INACTIVE TOOL DIRECTORY]",
        (
            "These tool schemas are inactive to reduce token use. If you need one, "
            "call tool.request with the exact name first, wait for the activation "
            "result, then use the activated tool."
        ),
    ]
    inactive_count = 0
    for spec in requestable_tool_specs:
        name = str(getattr(spec, "name", "") or "").strip()
        if not name or name in active_names:
            continue
        inactive_count += 1
        description = _compact_description(str(getattr(spec, "description", "") or ""))
        lines.append(f"- {name}: {description or name}")
    if inactive_count == 0:
        return None
    return Message(
        role="system",
        content="\n".join(lines),
        meta={"tool_schema_shortlisting": "inactive_directory"},
    )


def should_shortlist_tool_schemas(
    *,
    profile_name: str,
    tool_specs: Sequence[ToolSpec],
) -> bool:
    return (
        str(profile_name or "").strip() == "general_adaptive_v1"
        and len(tool_specs) > TOOL_SCHEMA_SHORTLIST_THRESHOLD
    )


def shortlist_tool_schemas(
    *,
    runtime: Any,
    model: str,
    user_messages: Sequence[Message],
    tool_specs: Sequence[ToolSpec],
    metadata: dict[str, Any] | None = None,
) -> ToolSchemaShortlistResult:
    all_specs = tuple(tool_specs or ())
    if len(all_specs) <= TOOL_SCHEMA_SHORTLIST_THRESHOLD:
        return _disabled_result(all_specs, reason="below_threshold")

    try:
        response = runtime.complete(
            messages=_shortlist_messages(
                user_messages=user_messages, tool_specs=all_specs
            ),
            tools=[],
            model=model,
            tool_choice="none",
            max_output_tokens=400,
            metadata=metadata,
        )
    except Exception:
        return _disabled_result(all_specs, reason="shortlist_call_failed")

    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    if not bool(getattr(response, "ok", False)):
        return _disabled_result(
            all_specs,
            reason="shortlist_not_ok",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            llm_call_made=True,
        )

    requested_names = _parse_selected_tool_names(response)
    if not requested_names:
        return _disabled_result(
            all_specs,
            reason="empty_or_invalid_selection",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            llm_call_made=True,
        )

    spec_by_name = {str(spec.name).strip(): spec for spec in all_specs}
    selected_names: list[str] = []
    seen: set[str] = set()
    for name in requested_names:
        if name in spec_by_name and name not in seen:
            selected_names.append(name)
            seen.add(name)
        if len(selected_names) >= TOOL_SCHEMA_SHORTLIST_MAX_ACTIVE:
            break
    if not selected_names:
        return _disabled_result(
            all_specs,
            reason="selection_had_no_visible_tools",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            llm_call_made=True,
        )
    if len(selected_names) >= len(all_specs):
        return _disabled_result(
            all_specs,
            reason="selected_all_tools",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            llm_call_made=True,
        )

    active_specs = tuple(spec_by_name[name] for name in selected_names)
    inactive_names = tuple(
        str(spec.name).strip()
        for spec in all_specs
        if str(spec.name).strip() not in set(selected_names)
    )
    return ToolSchemaShortlistResult(
        active_tool_specs=active_specs,
        requestable_tool_specs=all_specs,
        selected_tool_names=tuple(selected_names),
        inactive_tool_names=inactive_names,
        enabled=True,
        reason="model_shortlist",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        llm_call_made=True,
    )


def _disabled_result(
    tool_specs: Sequence[ToolSpec],
    *,
    reason: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    llm_call_made: bool = False,
) -> ToolSchemaShortlistResult:
    specs = tuple(tool_specs or ())
    return ToolSchemaShortlistResult(
        active_tool_specs=specs,
        requestable_tool_specs=specs,
        selected_tool_names=tuple(str(spec.name).strip() for spec in specs),
        inactive_tool_names=(),
        enabled=False,
        reason=reason,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        llm_call_made=llm_call_made,
    )


def _shortlist_messages(
    *,
    user_messages: Sequence[Message],
    tool_specs: Sequence[ToolSpec],
) -> list[Message]:
    user_context = "\n".join(
        str(getattr(message, "content", "") or "").strip()
        for message in user_messages
        if str(getattr(message, "role", "") or "") == "user"
        and str(getattr(message, "content", "") or "").strip()
    ).strip()
    if not user_context:
        user_context = "(no user text available)"
    lines = [
        "Select the minimal tool schemas needed for the next adaptive tool loop.",
        'Return strict JSON only: {"tool_ids": ["tool.name", "..."]}.',
        (
            "Choose the smallest useful subset of exact tool names from "
            "CANDIDATE_TOOLS, up to 8 tools. Do not include local file, shell, "
            "or write tools unless the user request actually needs them. Do not "
            "invent tool names. Inactive tools can be requested later with "
            "tool.request."
        ),
        "",
        "USER_REQUEST:",
        user_context,
        "",
        "CANDIDATE_TOOLS:",
    ]
    for spec in tool_specs:
        name = str(getattr(spec, "name", "") or "").strip()
        if not name:
            continue
        description = _compact_description(str(getattr(spec, "description", "") or ""))
        lines.append(f"- {name}: {description or name}")
    return [
        Message(
            role="system",
            content=(
                "You are choosing a compact tool schema shortlist. You decide the "
                "tool names; the runtime only validates exact names."
            ),
        ),
        Message(role="user", content="\n".join(lines)),
    ]


def _parse_selected_tool_names(response: LLMResponse) -> tuple[str, ...]:
    text = str(getattr(response, "output_text", "") or "").strip()
    if not text:
        return ()
    match = _JSON_FENCE_RE.match(text)
    if match is not None:
        text = str(match.group("body") or "").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return ()
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return ()
    if not isinstance(payload, dict):
        return ()
    raw_names = payload.get("tool_ids")
    if not isinstance(raw_names, list):
        return ()
    return tuple(name for name in (str(item).strip() for item in raw_names) if name)


def _compact_description(description: str, *, limit: int = 160) -> str:
    value = " ".join(str(description or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."
