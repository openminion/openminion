from dataclasses import dataclass
from typing import Any, Literal

from openminion.modules.brain.constants import (
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_ACT_PROFILE_GENERAL,
    BRAIN_EXECUTION_TARGET_DELEGATED,
)
from openminion.modules.brain.loop.adaptive import ACT_ADAPTIVE_ALLOWED_TOOLS
from openminion.modules.brain.loop.strategies.coding.contracts import (
    CODING_ALLOWED_TOOLS,
)
from openminion.modules.brain.loop.tools.runtime import build_runtime_tool_specs
from openminion.modules.brain.loop.tools.plan_control import build_plan_tool_spec
from openminion.modules.brain.loop.tools.review_control import build_review_tool_spec
from openminion.modules.llm.schemas import ToolSpec

ENTRY_CLARIFY_TOOL_NAME = "clarify"
ENTRY_CODING_TOOL_NAME = "coding"
ENTRY_DECOMPOSE_TOOL_NAME = "decompose"
ENTRY_RESEARCH_TOOL_NAME = "research"
EntryPath = Literal["act", "respond", "clarify"]


@dataclass(frozen=True, slots=True)
class EntryPathDetection:
    path: EntryPath
    response_text: str
    clarify_question: str
    tool_call_names: tuple[str, ...]


def clarify_tool_spec() -> ToolSpec:
    return ToolSpec(
        name=ENTRY_CLARIFY_TOOL_NAME,
        description=(
            "Ask the user a clarifying question when required information is missing."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The exact clarifying question to ask the user.",
                }
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    )


def decompose_tool_spec() -> ToolSpec:
    return ToolSpec(
        name=ENTRY_DECOMPOSE_TOOL_NAME,
        description=(
            "Break the current task into explicit independent subtasks when "
            "orchestration is the right execution shape. Use this for genuinely "
            "separate deliverables or branches of work, not for a single deep-"
            "research thread or iterative evidence gathering pass."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "subtasks": {
                    "type": "array",
                    "description": (
                        "Model-authored subtasks. Use an empty list only when "
                        "you intentionally decline to decompose after calling "
                        "this control tool."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": (
                                    "Stable model-authored subtask identifier "
                                    "for lineage."
                                ),
                            },
                            "description": {
                                "type": "string",
                                "description": (
                                    "Model-authored description of the subtask."
                                ),
                            },
                            "inputs": {
                                "type": "object",
                                "description": (
                                    "Optional structured inputs needed by this subtask."
                                ),
                                "additionalProperties": True,
                            },
                            "depends_on": {
                                "type": "array",
                                "description": (
                                    "Optional subtask ids that must complete "
                                    "before this subtask."
                                ),
                                "items": {"type": "string"},
                            },
                            "suggested_mode": {
                                "type": "string",
                                "description": (
                                    "Optional model-authored execution mode hint."
                                ),
                            },
                            "priority": {
                                "type": "integer",
                                "description": "Optional priority hint.",
                            },
                        },
                        "required": ["id", "description"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["subtasks"],
            "additionalProperties": False,
        },
    )


def coding_tool_spec() -> ToolSpec:
    return ToolSpec(
        name=ENTRY_CODING_TOOL_NAME,
        description=(
            "Enter the dedicated coding loop when the whole request is a single "
            "software task that needs iterative file edits, project scaffolding, "
            "tests, command execution, and final verification before answering."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )


def research_tool_spec() -> ToolSpec:
    return ToolSpec(
        name=ENTRY_RESEARCH_TOOL_NAME,
        description=(
            "Enter the dedicated iterative research loop when the whole request "
            "is a single deep-research thread that needs multiple searches, "
            "evidence gathering, and synthesis before a final answer."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )


def extract_response_text(response: Any) -> str:
    text = str(getattr(response, "output_text", "") or "").strip()
    if text:
        return text
    assistant_messages = list(getattr(response, "assistant_messages", []) or [])
    for message in reversed(assistant_messages):
        content = str(getattr(message, "content", "") or "").strip()
        if content:
            return content
    return ""


def detect_entry_path(response: Any) -> EntryPathDetection:
    """Detect the typed entry path from provider response structure."""
    tool_calls = list(getattr(response, "tool_calls", []) or [])
    tool_call_names = tuple(
        str(getattr(call, "name", "") or "").strip() for call in tool_calls
    )
    response_text = extract_response_text(response)
    clarify_question = ""
    for call in tool_calls:
        if str(getattr(call, "name", "") or "").strip() != ENTRY_CLARIFY_TOOL_NAME:
            continue
        arguments = getattr(call, "arguments", {}) or {}
        if isinstance(arguments, dict):
            clarify_question = str(arguments.get("question", "") or "").strip()
        if not clarify_question:
            clarify_question = response_text
        if not clarify_question:
            clarify_question = "Please clarify your request."
        return EntryPathDetection(
            path="clarify",
            response_text=response_text,
            clarify_question=clarify_question,
            tool_call_names=tool_call_names,
        )
    if tool_call_names:
        return EntryPathDetection(
            path="act",
            response_text=response_text,
            clarify_question="",
            tool_call_names=tool_call_names,
        )
    return EntryPathDetection(
        path="respond",
        response_text=response_text,
        clarify_question="",
        tool_call_names=tool_call_names,
    )


def entry_supports_seed_response(
    *,
    act_profile: str,
    execution_target_kind: str,
) -> bool:
    normalized_profile = str(act_profile or "").strip().lower()
    normalized_target = str(execution_target_kind or "").strip().lower()
    if normalized_target == BRAIN_EXECUTION_TARGET_DELEGATED:
        return False
    return normalized_profile in {
        BRAIN_ACT_PROFILE_GENERAL,
        BRAIN_ACT_PROFILE_CODING,
    }


def build_entry_tool_specs(
    runner: Any | None,
    *,
    act_profile: str,
    execution_target_kind: str,
    include_control_tools: bool = True,
) -> tuple[list[ToolSpec], bool]:
    normalized_profile = str(act_profile or "").strip().lower()
    normalized_target = str(execution_target_kind or "").strip().lower()
    supports_seed = entry_supports_seed_response(
        act_profile=normalized_profile,
        execution_target_kind=normalized_target,
    )
    allowed_tools: frozenset[str] = frozenset()
    if supports_seed:
        allowed_tools = (
            CODING_ALLOWED_TOOLS
            if normalized_profile == BRAIN_ACT_PROFILE_CODING
            else ACT_ADAPTIVE_ALLOWED_TOOLS
        )
    tool_specs = build_runtime_tool_specs(runner, allowed_tools=allowed_tools)
    if include_control_tools:
        tool_specs.append(coding_tool_spec())
        tool_specs.append(build_plan_tool_spec())
        tool_specs.append(research_tool_spec())
        tool_specs.append(decompose_tool_spec())
        tool_specs.append(clarify_tool_spec())
        tool_specs.append(build_review_tool_spec())
    return tool_specs, supports_seed
