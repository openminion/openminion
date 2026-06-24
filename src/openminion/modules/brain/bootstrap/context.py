from typing import TYPE_CHECKING, Any

from openminion.modules.brain.config import fixed_act_profile_from_profile
from openminion.modules.brain.execution.delegation import _runner_delegate
from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
from openminion.modules.brain.schemas import WorkingState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from openminion.modules.brain.runner import BrainRunner


_DECIDE_STYLE_OVERRIDES: dict[str, str] = {
    "entry_response_rule": (
        "This is the unified entry call. Start the work directly. You may answer "
        "with plain text, call a real tool, or call clarify(question=...) when "
        "required information is missing."
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
        "If no tool is needed and no blocking detail is missing, answer directly in normal assistant text."
    ),
    "entry_skill_binding_rule": (
        "When multiple active skills are visible and a plan step or command should use one, "
        "set skill_id to an exact active skill id. Do not invent skill ids or encode skill "
        "choice in prose only."
    ),
}


def _fixed_profile_rewrites(default_act_profile: str) -> dict[str, str]:
    return {
        "entry_fixed_profile_rule": (
            "Runtime already resolved the working act profile to "
            f"'{default_act_profile}'. Work within the visible tool and prompt "
            "surface for that profile. Do not restate or emit act_profile yourself."
        )
    }


def _inject_decide_prompt_contract(
    hints: dict[str, Any],
    *,
    runner: Any | None = None,
) -> None:
    existing_overrides = hints.get("style_overrides")
    style_overrides: dict[str, str] = {}
    if isinstance(existing_overrides, dict):
        for key, value in existing_overrides.items():
            key_text = str(key or "").strip()
            if not key_text:
                continue
            style_overrides[key_text] = str(value or "").strip()
    style_overrides.update(_DECIDE_STYLE_OVERRIDES)
    fixed_act_profile = fixed_act_profile_from_profile(getattr(runner, "profile", None))
    if fixed_act_profile is not None:
        style_overrides.update(_fixed_profile_rewrites(fixed_act_profile))
        hints["default_act_profile"] = fixed_act_profile
    hints["style_overrides"] = style_overrides


def _rebuild_decide_context_with_hints(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    logger: CanonicalEventLogger,
    budget_max_tokens: int,
    hints: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    model = (
        str(getattr(runner.profile.llm_profiles, "act_model", "") or "").strip()
        or str(getattr(runner.profile.llm_profiles, "decide_model", "") or "").strip()
    )
    context = _runner_delegate(
        "_build_context",
        runner,
        state=state,
        purpose="decide",
        budget={"max_tokens": budget_max_tokens},
        hints=hints,
        logger=logger,
    )
    estimate = _runner_delegate(
        "_estimate_tokens", runner, model=model, context=context
    )
    return context, estimate


def _compact_decide_mode_descriptions(value: Any) -> list[str] | None:
    if isinstance(value, dict):
        names = [str(key or "").strip() for key in value if str(key or "").strip()]
        return names or None
    if isinstance(value, list):
        names = [str(item or "").strip() for item in value if str(item or "").strip()]
        return names or None
    return None


def _drop_example_style_overrides(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    kept = {
        str(key): str(item) for key, item in value.items() if str(key or "").strip()
    }
    return kept or None
