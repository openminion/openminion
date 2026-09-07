"""Shared brain decision prompt fragments."""

from typing import Any

DECIDE_STYLE_OVERRIDES: dict[str, str] = {
    "entry_response_rule": (
        "This is the unified entry call. Start the work directly and return one "
        "visible entry control or execution tool call. Use respond(answer=..., "
        "freshness=...) for a direct answer; include freshness in every entry "
        "control call."
    ),
    "entry_tool_rule": (
        "If the request needs execution and a visible tool can help, call the tool "
        "directly in this response instead of describing the tool you would use."
    ),
    "entry_coding_profile_rule": (
        "For a single software task that needs iterative file edits, project "
        "scaffolding, tests, command execution, and final verification before "
        "answering, call the entry coding control tool instead of doing the whole "
        "workflow as direct one-shot file/tool calls."
    ),
    "entry_clarify_rule": (
        "Use the clarify tool only when a required detail blocks meaningful progress "
        "and no available tool or documented default can resolve it. Do not clarify "
        "for optional tool arguments or information an available tool can discover. "
        "Do not ask blocking clarifying questions in plain text when clarify(question=...) is available."
    ),
    "entry_no_routing_metadata_rule": (
        "Do not emit submit_output, mode labels, act_profile, execution_target, "
        "reason_code, confidence, or other decide metadata. Runtime owns routing "
        "defaults and workflow state."
    ),
    "entry_text_answer_rule": (
        "If no execution tool is needed and no blocking detail is missing, call "
        "respond with the complete answer and typed freshness assessment."
    ),
    "entry_skill_binding_rule": (
        "When multiple active skills are visible and a plan step or command should use one, "
        "set skill_id to an exact active skill id. Do not invent skill ids or encode skill "
        "choice in prose only."
    ),
}

BRAIN_FRESHNESS_POLICY_CONSTRAINT = (
    "FRESHNESS_POLICY: Do not fabricate stale real-time data"
)

ENTRY_CLARIFY_RECONSIDERATION_MESSAGE = (
    "Reconsider the clarification before asking the user. Check the inactive tool "
    "directory and the visible tools. If a tool can investigate the missing "
    "information or make meaningful progress, call the visible tool-request control "
    "(`tool.request` or provider-safe `tool_request`) or that tool now. Keep the "
    "clarify call only when no available tool or documented default can resolve the "
    "required detail."
)


def build_entry_inactive_tool_directory(tool_specs: list[Any]) -> str:
    lines = [
        "Execution tool schemas in this directory are inactive and cannot be called "
        "directly. To use one, call the visible tool-request activation control "
        "(`tool.request` or provider-safe `tool_request`) with its exact name from "
        "this directory."
    ]
    for spec in tool_specs:
        name = str(getattr(spec, "name", "") or "").strip()
        if not name:
            continue
        description = " ".join(str(getattr(spec, "description", "") or "").split())
        if len(description) > 120:
            description = f"{description[:117].rstrip()}..."
        lines.append(f"- {name}: {description or name}")
    return "\n".join(lines)


def fixed_profile_rewrites(default_act_profile: str) -> dict[str, str]:
    return {
        "entry_fixed_profile_rule": (
            "Runtime already resolved the working act profile to "
            f"'{default_act_profile}'. Work within the visible tool and prompt "
            "surface for that profile. Do not restate or emit act_profile yourself."
        )
    }


__all__ = [
    "BRAIN_FRESHNESS_POLICY_CONSTRAINT",
    "DECIDE_STYLE_OVERRIDES",
    "ENTRY_CLARIFY_RECONSIDERATION_MESSAGE",
    "build_entry_inactive_tool_directory",
    "fixed_profile_rewrites",
]
