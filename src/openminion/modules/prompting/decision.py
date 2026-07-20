"""Shared brain decision prompt fragments."""

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
        "Use the clarify tool whenever a missing detail blocks meaningful progress, "
        "including filename, path, location, or target details needed to continue. "
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

# Constraint emitted when a time-sensitive tool path fails freshness checks.
BRAIN_FRESHNESS_POLICY_CONSTRAINT = (
    "FRESHNESS_POLICY: Do not fabricate stale real-time data"
)


def fixed_profile_rewrites(default_act_profile: str) -> dict[str, str]:
    """Render decision prompt rewrites for a runtime-fixed act profile."""

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
    "fixed_profile_rewrites",
]
