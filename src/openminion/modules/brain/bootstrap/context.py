from typing import TYPE_CHECKING, Any

from openminion.modules.brain.config import fixed_act_profile_from_profile
from openminion.modules.brain.execution.delegation import _runner_delegate
from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
from openminion.modules.brain.schemas import WorkingState
from openminion.modules.prompting.decision import (
    DECIDE_STYLE_OVERRIDES,
    fixed_profile_rewrites,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from openminion.modules.brain.runner import BrainRunner


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
    style_overrides.update(DECIDE_STYLE_OVERRIDES)
    fixed_act_profile = fixed_act_profile_from_profile(getattr(runner, "profile", None))
    if fixed_act_profile is not None:
        style_overrides.update(fixed_profile_rewrites(fixed_act_profile))
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
