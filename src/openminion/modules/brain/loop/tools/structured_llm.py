from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from openminion.modules.brain.loop.services import runner_from_context
from openminion.modules.brain.retry import call_structured_with_retry


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


def _strip_code_fences(text: str) -> str:
    cleaned = _normalized_text(text)
    if not cleaned.startswith("```"):
        return cleaned
    parts = cleaned.split("```")
    if len(parts) < 2:
        return cleaned
    fenced = parts[1]
    if fenced.startswith("json"):
        fenced = fenced[4:]
    return fenced.strip()


def _model_for_purpose(*, runner: Any, purpose: str) -> str | None:
    profile = getattr(runner, "profile", None)
    llm_profiles = getattr(profile, "llm_profiles", None)
    if llm_profiles is None:
        return None
    if purpose == "summarize":
        return _normalized_text(getattr(llm_profiles, "summarize_model", ""))
    return _normalized_text(getattr(llm_profiles, "reflect_model", ""))


def structured_mode_response(
    ctx: Any,
    *,
    prompt: str,
    schema: type[BaseModel],
    purpose: str = "reflect",
    max_tokens: int = 1200,
) -> BaseModel | None:
    runner = runner_from_context(ctx)
    state = getattr(ctx, "state", None)
    logger = getattr(ctx, "logger", None)
    if runner is not None and getattr(runner, "llm_api", None) is not None:
        model = _model_for_purpose(runner=runner, purpose=purpose)
        if model and state is not None and state.llm_calls_used < state.llm_calls_max:
            try:
                budget_tokens = int(
                    min(max_tokens, max(1, int(state.budgets_remaining.tokens)))
                )
                context = runner._build_context(
                    state=state,
                    purpose=purpose,
                    budget={"max_tokens": budget_tokens},
                    hints={"user_input": prompt},
                    logger=logger,
                    mode_name=str(getattr(state, "active_mode_name", "") or "").strip()
                    or None,
                )
                raw = call_structured_with_retry(
                    runner.llm_api,
                    model=model,
                    purpose=purpose,
                    context=context,
                    schema=schema,
                )
                state.llm_calls_used += 1
                if isinstance(raw, dict):
                    runner._debit_tokens(state, raw, logger)
                return schema.model_validate(raw)
            except Exception:
                pass

    try:
        raw = _strip_code_fences(ctx.direct_response(user_input=prompt))
        if not raw:
            return None
        return schema.model_validate(json.loads(raw))
    except Exception:
        return None
