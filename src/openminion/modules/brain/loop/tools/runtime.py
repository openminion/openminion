from __future__ import annotations

import json
import platform
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from openminion.modules.brain.schemas.decisions import (
    ConfidentComplete,
    DelegationContext,
    DelegationResultSummary,
    FinalizationStatus,
    MetaRulePreference,
    MemoryConsolidationResult,
    PendingTurnContext,
    SessionWorkSummary,
    WatchOutcome,
    GoalDeclaration,
    GoalRevision,
)
from openminion.modules.llm.schemas import LLMRequest, LLMResponse, Message, ToolSpec
from openminion.modules.brain.runtime.reasoning import (
    ThinkingCtl,
    ThinkingRequest,
    ThinkingResolutionInput,
)

from openminion.modules.brain.bootstrap.route_catalog import get_route_descriptor
from openminion.modules.brain.tools.schema import collect_runtime_tool_schemas
from openminion.tools.exec.process import resolve_shell_family
from .contracts import AdaptiveToolLoopRuntimeUnavailableError
from .response_trailers import (
    TYPED_SIGNAL_SOURCE_STRUCTURED_FIELD,
    TYPED_SIGNAL_SOURCE_TRAILER,
    normalize_task_plan_trailer_response,
    with_typed_signal_source as _with_typed_signal_source,
)
from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS

ModelT = TypeVar("ModelT", bound=BaseModel)

_THINKING_CTL = ThinkingCtl()

_SIGNAL_PAYLOAD_ALIASES: dict[str, dict[str, str]] = {
    "confident_complete": {"confident_complete": "complete"},
}

_CONFIDENT_COMPLETE_RE = re.compile(
    r"(?s)(?P<body>.*?)(?:\n\s*)?<confident_complete>\s*(?P<payload>\{.*\})\s*</confident_complete>\s*$"
)
_FINALIZATION_STATUS_RE = re.compile(
    r"(?s)(?P<body>.*?)(?:\n\s*)?<finalization_status>\s*(?P<payload>\{.*\})\s*</finalization_status>\s*$"
)
_PENDING_TURN_CONTEXT_RE = re.compile(
    r"(?s)(?P<body>.*?)(?:\n\s*)?<pending_turn_context>\s*(?P<payload>\{.*\})\s*</pending_turn_context>\s*$"
)
_META_RULE_PREFERENCE_RE = re.compile(
    r"(?s)(?P<body>.*?)(?:\n\s*)?<meta_rule_preference>\s*(?P<payload>\{.*\})\s*</meta_rule_preference>\s*$"
)
_WATCH_OUTCOME_RE = re.compile(
    r"(?s)(?P<body>.*?)(?:\n\s*)?<watch_outcome>\s*(?P<payload>\{.*\})\s*</watch_outcome>\s*$"
)
_MEMORY_CONSOLIDATION_RE = re.compile(
    r"(?s)(?P<body>.*?)(?:\n\s*)?<memory_consolidation>\s*(?P<payload>\{.*\})\s*</memory_consolidation>\s*$"
)
_SESSION_WORK_SUMMARY_RE = re.compile(
    r"(?s)(?P<body>.*?)(?:\n\s*)?<session_work_summary>\s*(?P<payload>\{.*\})\s*</session_work_summary>\s*$"
)
_DELEGATION_CONTEXT_RE = re.compile(
    r"(?s)(?P<body>.*?)(?:\n\s*)?<delegation_context>\s*(?P<payload>\{.*\})\s*</delegation_context>\s*$"
)
_DELEGATION_RESULT_SUMMARY_RE = re.compile(
    r"(?s)(?P<body>.*?)(?:\n\s*)?<delegation_result_summary>\s*(?P<payload>\{.*\})\s*</delegation_result_summary>\s*$"
)


def _exec_run_description() -> str:
    system = platform.system() or "unknown"
    try:
        shell_family = resolve_shell_family().value
    except Exception:
        shell_family = "unknown"
    return (
        "Run one allowlisted direct command for verification or existing-file "
        f"workflows on platform={system}, shell_family={shell_family}. Do not use "
        "pipes, redirections, shell chaining, fallback operators, or multi-command "
        "snippets. Prefer host.metrics for disk, memory, and OS status; prefer "
        "structured file/web tools for discovery, reads, scaffolding, or web fetches."
    )


def _tool_spec_description(
    tool_name: str,
    raw: dict[str, Any],
    descriptions: dict[str, str],
) -> str:
    if tool_name == "exec.run":
        return _exec_run_description()
    return str(raw.get("description", "") or "").strip() or descriptions.get(
        tool_name, tool_name
    )


def _validated_model(
    payload: Any,
    *,
    field_name: str,
    model: type[ModelT],
) -> ModelT | None:
    if not isinstance(payload, dict):
        return None
    aliases = _SIGNAL_PAYLOAD_ALIASES.get(field_name, {})
    if aliases:
        normalized_payload = dict(payload)
        for source_key, target_key in aliases.items():
            if source_key in normalized_payload and target_key not in normalized_payload:
                normalized_payload[target_key] = normalized_payload.pop(source_key)
        payload = normalized_payload
    try:
        return model.model_validate(payload)
    except ValidationError:
        return None


def _trailing_json_object(raw_text: str) -> tuple[str, dict[str, Any]] | None:
    stripped = raw_text.rstrip()
    if not stripped.endswith("}"):
        return None
    decoder = json.JSONDecoder()
    for start, character in enumerate(stripped):
        if character != "{":
            continue
        body = stripped[:start].rstrip()
        if not body:
            continue
        try:
            payload, end = decoder.raw_decode(stripped[start:])
        except json.JSONDecodeError:
            continue
        if end == len(stripped) - start and isinstance(payload, dict):
            return body, payload
    return None


def _updated_assistant_messages(
    response: LLMResponse,
    *,
    content: str,
) -> list[Message]:
    assistant_messages = list(getattr(response, "assistant_messages", []) or [])
    if not assistant_messages:
        return assistant_messages
    updated_messages = list(assistant_messages)
    last = updated_messages[-1]
    if getattr(last, "role", "") == "assistant":
        updated_messages[-1] = last.model_copy(update={"content": content})
    return updated_messages


def _normalize_structured_signal_response(
    response: LLMResponse,
    *,
    field_name: str,
    model: type[ModelT],
) -> LLMResponse | None:
    structured = _validated_model(
        getattr(response, field_name, None),
        field_name=field_name,
        model=model,
    )
    if structured is None:
        return None
    return _with_typed_signal_source(
        response,
        field_name=field_name,
        source=TYPED_SIGNAL_SOURCE_STRUCTURED_FIELD,
        update={field_name: structured.model_dump(mode="json")},
    )


def _normalize_signal_response(
    response: LLMResponse,
    *,
    field_name: str,
    model: type[ModelT],
    trailer_pattern: re.Pattern[str],
) -> LLMResponse:
    normalized = _normalize_structured_signal_response(
        response,
        field_name=field_name,
        model=model,
    )
    if normalized is not None:
        return normalized

    raw_text = str(getattr(response, "output_text", "") or "")
    if not raw_text:
        return response
    match = trailer_pattern.match(raw_text)
    payload: dict[str, Any]
    if match is not None:
        try:
            loaded = json.loads(match.group("payload"))
        except json.JSONDecodeError:
            return response
        if not isinstance(loaded, dict):
            return response
        stripped_text = str(match.group("body") or "").rstrip()
        payload = loaded
    else:
        trailing = _trailing_json_object(raw_text)
        if trailing is None:
            return response
        stripped_text, payload = trailing
    structured = _validated_model(payload, field_name=field_name, model=model)
    if structured is None:
        return response
    return _with_typed_signal_source(
        response,
        field_name=field_name,
        source=TYPED_SIGNAL_SOURCE_TRAILER,
        update={
            "output_text": stripped_text,
            "assistant_messages": _updated_assistant_messages(
                response,
                content=stripped_text,
            ),
            field_name: structured.model_dump(mode="json"),
        },
    )


def _unwrap_llm_client(llm_adapter: Any) -> Any:
    client = getattr(llm_adapter, "client", None)
    if client is not None:
        if callable(getattr(client, "complete", None)):
            return client
        if callable(getattr(client, "call", None)):
            return client
    llm = getattr(llm_adapter, "llm", None)
    if llm is not None:
        if callable(getattr(llm, "complete", None)):
            return llm
        if callable(getattr(llm, "call", None)):
            return llm
    if callable(getattr(llm_adapter, "complete", None)):
        return llm_adapter
    if callable(getattr(llm_adapter, "call", None)):
        return llm_adapter
    return None


def _build_llm_request(
    *,
    messages: list[Message],
    tools: list[ToolSpec],
    model: str,
    tool_choice: str | dict[str, Any] = "auto",
    max_output_tokens: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> LLMRequest:
    return LLMRequest(
        model=model or None,
        messages=messages,
        tools=tools if tools else None,
        tool_choice=tool_choice,  # type: ignore[arg-type]
        max_output_tokens=max_output_tokens,
        metadata=metadata or {},
    )


def _normalize_confident_complete_response(response: LLMResponse) -> LLMResponse:
    return _normalize_signal_response(
        response,
        field_name="confident_complete",
        model=ConfidentComplete,
        trailer_pattern=_CONFIDENT_COMPLETE_RE,
    )


def _normalize_pending_turn_context_response(response: LLMResponse) -> LLMResponse:
    return _normalize_signal_response(
        response,
        field_name="pending_turn_context",
        model=PendingTurnContext,
        trailer_pattern=_PENDING_TURN_CONTEXT_RE,
    )


def _normalize_finalization_status_response(response: LLMResponse) -> LLMResponse:
    return _normalize_signal_response(
        response,
        field_name=STATE_KEY_FINALIZATION_STATUS,
        model=FinalizationStatus,
        trailer_pattern=_FINALIZATION_STATUS_RE,
    )


def _submit_output_close_requested(arguments: dict[str, Any]) -> bool:
    if arguments.get("satisfied") is True:
        return True
    next_action = str(arguments.get("next_action", "") or "").strip().lower()
    return next_action in {"close", "complete", "done", "final", "final_answer"}


def _normalize_submit_output_final_answer_response(
    response: LLMResponse,
) -> LLMResponse:
    if str(getattr(response, "output_text", "") or "").strip():
        return response
    if isinstance(getattr(response, STATE_KEY_FINALIZATION_STATUS, None), dict):
        return response
    tool_calls = list(getattr(response, "tool_calls", []) or [])
    if len(tool_calls) != 1:
        return response
    tool_call = tool_calls[0]
    if str(getattr(tool_call, "name", "") or "").strip() != "submit_output":
        return response
    arguments = getattr(tool_call, "arguments", {}) or {}
    if not isinstance(arguments, dict):
        return response
    final_answer = str(arguments.get("final_answer", "") or "").strip()
    if not final_answer or not _submit_output_close_requested(arguments):
        return response
    finalization_status = FinalizationStatus(
        status="final_answer",
        reasoning=str(
            arguments.get("reasoning", arguments.get("reason", ""))
            or "submit_output final_answer"
        ),
    ).model_dump(mode="json")
    return _with_typed_signal_source(
        response,
        field_name=STATE_KEY_FINALIZATION_STATUS,
        source=TYPED_SIGNAL_SOURCE_STRUCTURED_FIELD,
        update={
            "output_text": final_answer,
            "assistant_messages": [Message(role="assistant", content=final_answer)],
            "tool_calls": [],
            STATE_KEY_FINALIZATION_STATUS: finalization_status,
        },
    )


def _normalize_watch_outcome_response(response: LLMResponse) -> LLMResponse:
    return _normalize_signal_response(
        response,
        field_name="watch_outcome",
        model=WatchOutcome,
        trailer_pattern=_WATCH_OUTCOME_RE,
    )


def _normalize_meta_rule_preference_response(response: LLMResponse) -> LLMResponse:
    return _normalize_signal_response(
        response,
        field_name="meta_rule_preference",
        model=MetaRulePreference,
        trailer_pattern=_META_RULE_PREFERENCE_RE,
    )


def _normalize_memory_consolidation_response(response: LLMResponse) -> LLMResponse:
    return _normalize_signal_response(
        response,
        field_name="memory_consolidation",
        model=MemoryConsolidationResult,
        trailer_pattern=_MEMORY_CONSOLIDATION_RE,
    )


def _normalize_goal_declaration_response(response: LLMResponse) -> LLMResponse:
    normalized = _normalize_structured_signal_response(
        response,
        field_name="goal_declaration",
        model=GoalDeclaration,
    )
    return normalized or response


def _normalize_goal_revision_response(response: LLMResponse) -> LLMResponse:
    normalized = _normalize_structured_signal_response(
        response,
        field_name="goal_revision",
        model=GoalRevision,
    )
    return normalized or response


def _normalize_session_work_summary_response(response: LLMResponse) -> LLMResponse:
    return _normalize_signal_response(
        response,
        field_name="session_work_summary",
        model=SessionWorkSummary,
        trailer_pattern=_SESSION_WORK_SUMMARY_RE,
    )


def _normalize_delegation_context_response(response: LLMResponse) -> LLMResponse:
    return _normalize_signal_response(
        response,
        field_name="delegation_context",
        model=DelegationContext,
        trailer_pattern=_DELEGATION_CONTEXT_RE,
    )


def _normalize_delegation_result_summary_response(response: LLMResponse) -> LLMResponse:
    return _normalize_signal_response(
        response,
        field_name="delegation_result_summary",
        model=DelegationResultSummary,
        trailer_pattern=_DELEGATION_RESULT_SUMMARY_RE,
    )


class DefaultAdaptiveToolLoopLLMRuntime:
    def __init__(self, llm_client: Any) -> None:
        self._client = llm_client
        self._use_call_path = not callable(
            getattr(llm_client, "complete", None)
        ) and callable(getattr(llm_client, "call", None))

    @classmethod
    def from_adapter(cls, llm_adapter: Any) -> "DefaultAdaptiveToolLoopLLMRuntime":
        client = _unwrap_llm_client(llm_adapter)
        if client is None:
            raise AdaptiveToolLoopRuntimeUnavailableError(
                "Adaptive tool loop requires a raw LLMClient.complete(...) or "
                "OpenMinionLLMClient.call(...) path but the current llm_adapter "
                f"({type(llm_adapter).__name__!r}) does not expose one."
            )
        return cls(client)

    def complete(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSpec],
        model: str,
        tool_choice: str | dict[str, Any] = "auto",
        max_output_tokens: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LLMResponse:
        if self._use_call_path:
            request = _build_llm_request(
                messages=messages,
                tools=tools,
                model=model,
                tool_choice=tool_choice,
                max_output_tokens=max_output_tokens,
                metadata=metadata,
            )
            response = self._client.call(request)
            response = _normalize_confident_complete_response(response)
            response = _normalize_finalization_status_response(response)
            response = _normalize_pending_turn_context_response(response)
            response = _normalize_memory_consolidation_response(response)
            response = _normalize_watch_outcome_response(response)
            response = _normalize_session_work_summary_response(response)
            response = _normalize_goal_declaration_response(response)
            response = _normalize_goal_revision_response(response)
            response = _normalize_delegation_context_response(response)
            response = _normalize_delegation_result_summary_response(response)
            response = _normalize_meta_rule_preference_response(response)
            response = normalize_task_plan_trailer_response(response)
            response = _normalize_submit_output_final_answer_response(response)
            return response

        overrides: dict[str, Any] = {
            "model": model,
            "tool_choice": tool_choice,
        }
        if max_output_tokens is not None:
            overrides["max_output_tokens"] = max_output_tokens
        if metadata is not None:
            overrides["metadata"] = metadata
        response = self._client.complete(
            messages, tools if tools else None, **overrides
        )
        response = _normalize_confident_complete_response(response)
        response = _normalize_finalization_status_response(response)
        response = _normalize_pending_turn_context_response(response)
        response = _normalize_memory_consolidation_response(response)
        response = _normalize_watch_outcome_response(response)
        response = _normalize_session_work_summary_response(response)
        response = _normalize_goal_declaration_response(response)
        response = _normalize_goal_revision_response(response)
        response = _normalize_delegation_context_response(response)
        response = _normalize_delegation_result_summary_response(response)
        response = _normalize_meta_rule_preference_response(response)
        response = normalize_task_plan_trailer_response(response)
        response = _normalize_submit_output_final_answer_response(response)
        return response


def resolve_loop_model(ctx: Any) -> str:
    profile = getattr(getattr(ctx, "options", None), "profile", None)
    if profile is None:
        profile = getattr(getattr(ctx, "options", None), "agent_profile", None)
    if profile is not None:
        llm_profiles = getattr(profile, "llm_profiles", None)
        if llm_profiles is not None:
            act_model = getattr(llm_profiles, "act_model", None)
            if act_model:
                return str(act_model)
            decide_model = getattr(llm_profiles, "decide_model", None)
            if decide_model:
                return str(decide_model)
    return ""


def _resolve_loop_provider_name(ctx: Any) -> str:
    llm_adapter = getattr(ctx, "llm_adapter", None)
    for candidate in (
        llm_adapter,
        getattr(llm_adapter, "client", None),
        getattr(llm_adapter, "llm", None),
    ):
        value = str(getattr(candidate, "name", "") or "").strip().lower()
        if value:
            return value
    return ""


def build_loop_thinking_metadata(
    ctx: Any,
    *,
    purpose: str = "act",
) -> dict[str, Any]:
    profile = getattr(getattr(ctx, "options", None), "profile", None)
    if profile is None:
        profile = getattr(getattr(ctx, "options", None), "agent_profile", None)
    mode_name = str(
        getattr(getattr(ctx, "decision", None), "route", "")
        or getattr(getattr(ctx, "state", None), "active_mode_name", "")
        or ""
    ).strip()
    mode_spec = get_route_descriptor(mode_name) if mode_name else None
    resolved = _THINKING_CTL.resolve_mode_aware(
        request=ThinkingRequest(
            purpose=purpose,
            requested_profile=None,
            provider=_resolve_loop_provider_name(ctx) or None,
            model=resolve_loop_model(ctx) or None,
            metadata={"context_owner": "brain.loop.tools.runtime"},
        ),
        layers=ThinkingResolutionInput(
            code_default_profile="minimal",
            agent_profile=str(getattr(profile, "thinking", "") or "").strip() or None,
        ),
        mode_policy=getattr(mode_spec, "thinking_policy", None),
        mode_name=mode_name or None,
    )
    metadata = {
        "purpose": purpose,
        **_THINKING_CTL.build_provider_metadata(resolved=resolved),
    }
    user_input = str(getattr(ctx, "user_input", "") or "").strip()
    if not user_input:
        state = getattr(ctx, "state", None)
        if state is not None:
            user_input = str(getattr(state, "last_user_input", "") or "").strip()
    if user_input:
        metadata.setdefault("user_input", user_input)
        metadata.setdefault("original_user_input", user_input)
    if mode_name:
        metadata.setdefault("mode_name", mode_name)
    return metadata


def build_runtime_tool_specs(
    runner: Any | None,
    *,
    allowed_tools: frozenset[str],
) -> list[ToolSpec]:
    descriptions: dict[str, str] = {
        "file.list_dir": "List files and directories at a path.",
        "file.read": "Read file contents.",
        "file.find": "Search for files matching a pattern.",
        "file.write": (
            "Write or overwrite a file and create parent directories "
            "automatically; use this to scaffold new project files and folders."
        ),
        "file.search": "Search file contents and matches in a workspace.",
        "file.edit": "Apply a targeted file edit or patch.",
        "exec.run": _exec_run_description(),
        "exec.poll": "Poll the status or output of a running process.",
        "exec.list": "List currently running processes.",
        "exec.kill": "Kill a running process by ID.",
        "web.search": "Search the web for current information.",
        "web.fetch": "Fetch and summarize web content from a URL.",
        "weather": "Get current weather for a location.",
        "time": "Get the current time for a timezone or locale.",
        "location": "Resolve or infer a geographic location.",
        "host.metrics": "Get local host platform, disk usage, and memory metrics.",
        "ip.public": "Get the public IP details.",
        "ip.local": "Get the local IP details.",
        "browser": "Browse or inspect a web page interactively.",
        "tool.list": "List the available runtime tools.",
        "task.schedule": "Schedule a task for interval or cron-backed execution.",
        "task.list": "List scheduled tasks and their exact task identifiers.",
        "task.cancel": "Cancel a scheduled task by exact task identifier.",
    }
    schema_by_name: dict[str, dict[str, Any]] = {}
    if runner is not None:
        try:
            schema_by_name = {
                str(item.get("name", "")).strip(): dict(item)
                for item in collect_runtime_tool_schemas(runner)
                if isinstance(item, dict) and str(item.get("name", "")).strip()
            }
        except (ValidationError, json.JSONDecodeError):
            schema_by_name = {}
    specs: list[ToolSpec] = []
    for tool_name in sorted(allowed_tools):
        raw = schema_by_name.get(tool_name, {})
        if not raw and tool_name not in descriptions:
            continue
        specs.append(
            ToolSpec(
                name=tool_name,
                description=_tool_spec_description(tool_name, raw, descriptions),
                input_schema=dict(raw.get("parameters", {}) or {})
                or {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                },
            )
        )
    return specs
