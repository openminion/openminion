import json
import re
from typing import Any

from pydantic import BaseModel

from openminion.modules.llm.schemas import (
    LLMRequest,
    LLMResponse,
    Message,
    ToolCall,
    UsageInfo,
)

from ...interfaces import BRAIN_ADAPTER_INTERFACE_VERSION


class LocalLLMAdapter:
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def estimate_tokens(self, *, model: str, context: dict[str, Any]) -> int:
        del model, context
        return 50

    def call_structured(
        self,
        *,
        model: str,
        purpose: str,
        context: dict[str, Any],
        schema: type[BaseModel],
    ) -> dict[str, Any]:
        del model, purpose
        user_input = _extract_mock_user_input(context)
        if schema.__name__ == "SkillSubsetSelection":
            user_message = _extract_skill_selection_user_message(context)
            skill_catalog = _extract_skill_selection_catalog(context)
            skill_id = _select_mock_skill_id(user_message, skill_catalog)
            return {
                "skill_ids": [skill_id] if skill_id else [],
                "intent": _summarize_mock_intent(user_message),
            }

        if schema.__name__ == "Decision":
            mock_decision = _mock_decision_from_context(user_input, context)
            if mock_decision is not None:
                return mock_decision
            return {
                "route": "respond",
                "confidence": 0.9,
                "reason_code": "mock_decide",
                "respond_kind": "answer",
                "answer": "I'm here. What can I help you with?",
            }

        if schema.__name__ == "Plan":
            mock_plan = _mock_plan_from_user_input(user_input)
            if mock_plan is not None:
                return mock_plan
            return {
                "objective": "mock_plan_objective",
                "steps": [
                    {
                        "kind": "tool",
                        "title": "step1",
                        "tool_name": "echo",
                        "args": {},
                        "success_criteria": {},
                        "idempotency_key": "step-1",
                    }
                ],
                "stop_conditions": ["local fixture complete"],
                "assumptions": [],
                "risk_summary": "low",
                "success_criteria": {"fixture": "local_plan_mock"},
            }

        if schema.__name__ == "ReflectReport":
            return {
                "outcome": "improved",
                "fixes": [],
                "new_procedure": None,
                "new_policy": None,
                "new_preference": None,
            }

        if schema.__name__ == "PostActionJudgment":
            return _mock_post_action_judgment(context)

        if schema.__name__ == "ClosureJudgment":
            return _mock_closure_judgment(context)

        if schema.__name__ == "CorrectionPlan":
            raise ValueError(
                "LocalLLMAdapter does not synthesize CorrectionPlan defaults; "
                "provide an explicit fixture or a real LLM runtime."
            )

        if schema.__name__ == "SuccessMemoryReport":
            hints = _context_hints(context)
            session_id = _context_id(context, "session_id", default="local-session")
            agent_id = _context_id(context, "agent_id", default="local-agent")
            command_ids = _string_list(hints.get("success_memory_command_ids"))
            tool_names = _string_list(hints.get("success_memory_tool_names"))
            goal = _hint_string(
                hints,
                "success_memory_goal",
                default="completed task",
            )
            items: list[dict[str, Any]] = [
                {
                    "kind": "procedure",
                    "title": f"Procedure for {goal}",
                    "content": {
                        "goal": goal,
                        "command_ids": command_ids,
                        "artifact_refs": list(
                            hints.get("success_memory_artifact_refs") or []
                        ),
                    },
                    "confidence": 0.9,
                    "tags": ["success_path", "procedure"],
                    "scope_suggestion": "agent",
                }
            ]
            if tool_names:
                items.append(
                    {
                        "kind": "tool_habit",
                        "title": f"Tool habit: {tool_names[0]}",
                        "content": {
                            "tool_name": tool_names[0],
                            "when": goal,
                        },
                        "confidence": 0.85,
                        "tags": ["success_path", "tool_habit"],
                        "scope_suggestion": "agent",
                    }
                )
            return {
                "session_id": session_id,
                "agent_id": agent_id,
                "outcome": "success",
                "command_ids": command_ids,
                "items": items,
            }

        if schema.__name__ == "FailureMemoryReport":
            hints = _context_hints(context)
            session_id = _context_id(context, "session_id", default="local-session")
            agent_id = _context_id(context, "agent_id", default="local-agent")
            command_ids = _string_list(hints.get("failure_memory_command_ids"))
            tool_names = _string_list(hints.get("failure_memory_tool_names"))
            args_signatures = _string_list(hints.get("failure_memory_args_signatures"))
            termination_reason = _hint_string(
                hints,
                "failure_memory_termination_reason",
                default="tool_failure_no_recovery",
            )
            error_code = _hint_string(hints, "failure_memory_error_code")
            goal = _hint_string(
                hints,
                "failure_memory_goal",
                default="the current task",
            )
            tool_name = tool_names[0] if tool_names else "the failing tool"
            title = (
                f"Correction for {tool_name}"
                if tool_names
                else f"Correction for {termination_reason}"
            )
            correction_text = (
                f"Before retrying {tool_name}, verify the precondition that failed during {goal}."
                if tool_names
                else f"Before retrying, verify the precondition that failed during {goal}."
            )
            content: dict[str, Any] = {
                "text": correction_text,
                "tool_name": tool_names[0] if tool_names else None,
                "args_signature": args_signatures[0] if args_signatures else None,
                "termination_reason": termination_reason,
                "error_code": error_code or None,
            }
            return {
                "session_id": session_id,
                "agent_id": agent_id,
                "outcome": "failure",
                "termination_reason": termination_reason,
                "command_ids": command_ids,
                "items": [
                    {
                        "kind": "correction",
                        "title": title,
                        "content": content,
                        "confidence": 0.84,
                        "tags": ["failure_path", "correction"],
                        "scope_suggestion": "agent",
                    }
                ],
                "meta_rule_preference": (
                    {
                        "rule": f"{tool_name}.retry_strategy",
                        "preferred_value": "verify_precondition_first",
                        "reasoning": (
                            f"{tool_name} failed during {goal}; check the prerequisite before retrying."
                        ),
                    }
                    if tool_names
                    else None
                ),
            }

        if schema.__name__ == "FeasibilityReport":
            hints = _context_hints(context)
            sub_intents = [
                item
                for item in (hints.get("feasibility_sub_intents") or [])
                if isinstance(item, dict) and str(item.get("id", "")).strip()
            ]
            return {
                "plan_viable": True,
                "recommendation": "proceed_full",
                "user_message": "",
                "requires_user_choice": False,
                "viable_intent_ids": [
                    str(item.get("id")).strip() for item in sub_intents
                ],
                "blocked_intent_ids": [],
                "assessments": [
                    {
                        "intent_id": str(item.get("id")).strip(),
                        "status": "covered",
                        "reason": "",
                        "covering_tools": [],
                        "blocked_by": [],
                        "alternatives": [],
                    }
                    for item in sub_intents
                ],
            }

        try:
            return schema().model_dump(mode="json")
        except Exception:
            return {}

    def call(self, request: LLMRequest) -> LLMResponse:
        model_name = str(request.model or "local")
        user_input = _extract_user_input_from_messages(request.messages)
        metadata = dict(getattr(request, "metadata", {}) or {})
        capability_category = str(metadata.get("capability_category", "") or "").strip()
        forced_tools = metadata.get("forced_tools")
        tool_specs = list(request.tools or [])
        available_tool_names = {
            str(spec.name or "").strip()
            for spec in tool_specs
            if str(spec.name or "").strip()
        }
        real_tool_names = {name for name in available_tool_names if name != "clarify"}
        tool_message = _last_tool_message(request.messages)
        if tool_message is not None:
            return _llm_response(
                model=model_name,
                output_text=_mock_followup_from_tool_message(tool_message),
            )

        tool_command = _mock_tool_command_from_user_input(user_input)
        if tool_command is not None:
            tool_name = str(tool_command.get("tool_name", "") or "").strip()
            if tool_name in available_tool_names:
                return _tool_call_response(
                    model=model_name,
                    name=tool_name,
                    arguments=dict(tool_command.get("args", {}) or {}),
                )

        if len(real_tool_names) == 1:
            only_tool = next(iter(real_tool_names))
            lower = str(user_input or "").strip().lower()
            clarify_question = _clarify_question_for_tool(only_tool)
            if (
                clarify_question
                and not lower.startswith("tool ")
                and "clarify" in available_tool_names
            ):
                return _clarify_tool_response(
                    model=model_name,
                    question=clarify_question,
                )
            return _tool_call_response(model=model_name, name=only_tool)

        decision = _mock_decision_from_context(
            user_input,
            {
                "messages": [
                    {"role": message.role, "content": message.content}
                    for message in request.messages
                ],
                "hints": {
                    "user_input": user_input,
                    "forced_tools": forced_tools,
                    "capability_category": capability_category,
                },
            },
        )
        if isinstance(decision, dict):
            if (
                str(decision.get("mode", "") or "").strip() == "respond"
                and str(decision.get("respond_kind", "") or "").strip() == "clarify"
                and "clarify" in available_tool_names
            ):
                return _clarify_tool_response(
                    model=model_name,
                    question=str(decision.get("question", "") or "").strip(),
                )
            if (
                str(decision.get("mode", "") or "").strip() == "respond"
                and str(decision.get("respond_kind", "") or "").strip() == "answer"
            ):
                return _llm_response(
                    model=model_name,
                    output_text=str(decision.get("answer", "") or "").strip(),
                )

        return _llm_response(
            model=model_name,
            output_text="I'm here. What can I help you with?",
        )


_PREROUTING_USER_MESSAGE_RE = re.compile(r'User message:\s*"(?P<message>[^"]*)"', re.I)
_PREROUTING_SKILL_LINE_RE = re.compile(
    r"^- (?P<skill_id>[a-zA-Z0-9_.:-]+)(?: \([^)]+\))?:", re.M
)


def _extract_skill_selection_user_message(context: dict[str, Any]) -> str:
    messages = context.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            content = str(message.get("content", "") or "")
            match = _PREROUTING_USER_MESSAGE_RE.search(content)
            if match:
                return str(match.group("message") or "").strip()
    hints = context.get("hints")
    if isinstance(hints, dict):
        return str(hints.get("user_input", "") or "").strip()
    return ""


def _extract_mock_user_input(context: dict[str, Any]) -> str:
    value = _hint_string(_context_hints(context), "user_input")
    if value:
        return value
    messages = context.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            if str(message.get("role", "")).strip().lower() == "user":
                value = str(message.get("content", "") or "").strip()
                if value:
                    return value
    return ""


def _extract_user_input_from_messages(messages: list[Message]) -> str:
    for message in reversed(list(messages or [])):
        if str(getattr(message, "role", "") or "").strip().lower() != "user":
            continue
        content = str(getattr(message, "content", "") or "").strip()
        if content:
            return content
    return ""


def _last_tool_message(messages: list[Message]) -> Message | None:
    for message in reversed(list(messages or [])):
        if str(getattr(message, "role", "") or "").strip().lower() == "tool":
            return message
    return None


def _mock_followup_from_tool_message(message: Message) -> str:
    payload_text = str(getattr(message, "content", "") or "").strip()
    if not payload_text:
        return "Done."
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return payload_text
    if not isinstance(payload, dict):
        return payload_text
    summary = str(payload.get("summary", "") or "").strip()
    if summary:
        return summary
    outputs = payload.get("outputs")
    if isinstance(outputs, dict) and outputs:
        return json.dumps(outputs, ensure_ascii=False, sort_keys=True)
    error = payload.get("error")
    if isinstance(error, dict):
        message_text = str(error.get("message", "") or "").strip()
        if message_text:
            return message_text
    return str(payload.get("status", "") or "").strip() or "Done."


def _llm_response(
    *,
    model: str,
    output_text: str = "",
    tool_calls: list[ToolCall] | None = None,
) -> LLMResponse:
    return LLMResponse(
        ok=True,
        provider="local",
        model=model,
        output_text=output_text,
        assistant_messages=(
            [Message(role="assistant", content=output_text)] if output_text else []
        ),
        tool_calls=list(tool_calls or []),
        usage=UsageInfo(input_tokens=1, output_tokens=1, total_tokens=2),
        latency_ms=0,
        finish_reason="tool_calls" if tool_calls else "stop",
        provider_raw={},
        telemetry={},
    )


def _tool_call_response(
    *,
    model: str,
    name: str,
    arguments: dict[str, Any] | None = None,
) -> LLMResponse:
    return _llm_response(
        model=model,
        tool_calls=[
            ToolCall(
                id="local-tool-1" if name != "clarify" else "local-clarify-1",
                name=name,
                arguments=dict(arguments or {}),
                status="requested",
            )
        ],
    )


def _clarify_tool_response(*, model: str, question: str) -> LLMResponse:
    return _tool_call_response(
        model=model,
        name="clarify",
        arguments={"question": question},
    )


def _clarify_question_for_tool(tool_name: str) -> str:
    return {
        "file.read": "What path should I read?",
        "weather": "Which location should I check the weather for?",
    }.get(str(tool_name or "").strip().lower(), "")


def _context_hints(context: dict[str, Any]) -> dict[str, Any]:
    hints = context.get("hints")
    return dict(hints) if isinstance(hints, dict) else {}


def _context_id(context: dict[str, Any], key: str, *, default: str) -> str:
    return str(context.get(key, "") or default)


def _string_list(value: Any) -> list[str]:
    return [
        item for item in (str(entry).strip() for entry in list(value or [])) if item
    ]


def _hint_string(hints: dict[str, Any], key: str, *, default: str = "") -> str:
    return str(hints.get(key, "") or "").strip() or default


def _mock_tool_command_from_user_input(user_input: str) -> dict[str, Any] | None:
    parts = user_input.strip().split(maxsplit=2)
    if len(parts) < 2 or parts[0].lower() != "tool":
        return None
    tool_name = str(parts[1] or "").strip()
    if not tool_name:
        return None
    args: dict[str, Any] = {}
    if len(parts) == 3 and parts[2].strip():
        payload = parts[2].strip()
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            decoded = payload
        if isinstance(decoded, dict):
            args = decoded
        else:
            args = {"value": decoded}
    return {
        "kind": "tool",
        "title": f"Tool call: {tool_name}",
        "tool_name": tool_name,
        "args": args,
        "success_criteria": {"status": "success"},
        "risk_level": "low",
    }


def _mock_decision_from_context(
    user_input: str, context: dict[str, Any]
) -> dict[str, Any] | None:
    normalized = str(user_input or "").strip()
    lower = normalized.lower()
    hints = context.get("hints", {}) if isinstance(context.get("hints"), dict) else {}
    if not lower:
        return _mock_targeted_tool_decision_from_hints(
            hints=hints,
            user_input=normalized,
        )
    if lower.startswith("plan tool "):
        return {
            "route": "act",
            "confidence": 0.9,
            "reason_code": "mock_plan",
            "rationale": "Execute the plan tool request through the act loop.",
        }
    tool_command = _mock_tool_command_from_user_input(normalized)
    if tool_command is not None:
        return {
            "route": "act",
            "confidence": 0.95,
            "reason_code": "mock_tool_prompt",
            "rationale": "Execute the request through the shared act loop.",
        }
    if lower in {"hi", "hello", "hey", "hello!", "hi!"}:
        return {
            "route": "respond",
            "confidence": 0.95,
            "reason_code": "mock_greeting",
            "respond_kind": "answer",
            "answer": "I'm here. What can I help you with?",
        }
    return _mock_targeted_tool_decision_from_hints(
        hints=hints,
        user_input=normalized,
    )


def _mock_targeted_tool_decision_from_hints(
    *,
    hints: dict[str, Any],
    user_input: str,
) -> dict[str, Any] | None:
    forced_tools = hints.get("forced_tools")
    if isinstance(forced_tools, list) and forced_tools:
        forced_name = str(forced_tools[0] or "").strip()
        if forced_name:
            return _mock_targeted_tool_decision(
                tool_name=forced_name,
                user_input=user_input,
                reason_code="mock_forced_tool",
            )
    capability_category = str(hints.get("capability_category", "") or "").strip()
    if capability_category:
        return _mock_targeted_tool_decision(
            tool_name=capability_category,
            user_input=user_input,
            reason_code="mock_capability_tool",
        )
    return None


def _mock_targeted_tool_decision(
    *, tool_name: str, user_input: str, reason_code: str
) -> dict[str, Any]:
    normalized_tool = str(tool_name or "").strip().lower()
    normalized_input = str(user_input or "").strip().lower()
    clarify_question = _clarify_question_for_tool(normalized_tool)
    if clarify_question and not normalized_input.startswith("tool "):
        return {
            "route": "respond",
            "confidence": 0.8,
            "reason_code": (
                "mock_file_read_needs_path"
                if normalized_tool == "file.read"
                else "mock_weather_needs_location"
            ),
            "respond_kind": "clarify",
            "question": clarify_question,
        }
    return {
        "route": "act",
        "confidence": 0.95,
        "reason_code": reason_code,
        "act_profile": "general",
        "execution_target": {"kind": "local"},
        "rationale": "Execute the request through the shared act loop.",
    }


def _mock_plan_from_user_input(user_input: str) -> dict[str, Any] | None:
    normalized = str(user_input or "").strip()
    lower = normalized.lower()
    if not lower.startswith("plan tool "):
        return None
    tool_command = _mock_tool_command_from_user_input(normalized[5:].strip())
    if tool_command is None:
        return None
    return {
        "objective": normalized,
        "steps": [tool_command],
        "stop_conditions": ["done"],
        "assumptions": [],
        "risk_summary": "low",
        "success_criteria": {"status": "success"},
    }


def _mock_post_action_judgment(context: dict[str, Any]) -> dict[str, Any]:
    hints = context.get("hints", {}) if isinstance(context.get("hints"), dict) else {}
    runtime_facts = (
        hints.get("post_action_runtime_facts", {})
        if isinstance(hints.get("post_action_runtime_facts"), dict)
        else {}
    )
    action_result = (
        runtime_facts.get("action_result", {})
        if isinstance(runtime_facts.get("action_result"), dict)
        else {}
    )
    status = str(action_result.get("status", "") or "").strip().lower()
    summary = str(action_result.get("summary", "") or "").strip()
    if status == "success":
        return {
            "outcome": "advance",
            "reason": summary or "mock_action_succeeded",
            "user_message": None,
            "confidence": 0.9,
        }
    if status == "retry":
        return {
            "outcome": "retry",
            "reason": summary or "mock_retry_requested",
            "user_message": None,
            "confidence": 0.8,
        }
    if status == "needs_user":
        return {
            "outcome": "ask_user",
            "reason": summary or "mock_needs_user",
            "user_message": summary or "I need your input before I continue.",
            "confidence": 0.8,
        }
    return {
        "outcome": "ask_user",
        "reason": summary or "mock_action_failed",
        "user_message": summary or "I hit a problem and need your guidance.",
        "confidence": 0.75,
    }


def _mock_closure_judgment(context: dict[str, Any]) -> dict[str, Any]:
    hints = context.get("hints", {}) if isinstance(context.get("hints"), dict) else {}
    summary = str(hints.get("closure_action_summary", "") or "").strip()
    reason = str(hints.get("closure_candidate_reason", "") or "").strip() or summary
    final_answer = summary or "Done."
    return {
        "satisfied": True,
        "reason": reason or "mock_closure_satisfied",
        "next_action": "close",
        "final_answer": final_answer,
    }


def _extract_skill_selection_catalog(context: dict[str, Any]) -> list[str]:
    messages = context.get("messages")
    if not isinstance(messages, list):
        return []
    catalog: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = str(message.get("content", "") or "")
        for match in _PREROUTING_SKILL_LINE_RE.finditer(content):
            skill_id = str(match.group("skill_id") or "").strip()
            if skill_id and skill_id != "(none":
                catalog.append(skill_id)
    return catalog


def _select_mock_skill_id(user_message: str, skill_catalog: list[str]) -> str | None:
    normalized = user_message.strip().lower()
    if not normalized or not skill_catalog:
        return None

    for skill_id in skill_catalog:
        if skill_id.lower() in normalized:
            return skill_id
    return None


def _summarize_mock_intent(user_message: str) -> str:
    compact = " ".join(str(user_message or "").strip().split())
    if len(compact) <= 80:
        return compact
    return compact[:77].rstrip() + "..."
