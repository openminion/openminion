import re
from typing import TYPE_CHECKING, Any

from ..tools.parser import normalize_tool_name_for_brain
from ..retry import call_structured_with_retry
from ..schemas import (
    FreshnessContract,
    FreshnessDiagnostics,
    FreshnessObligations,
    iso_now,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..diagnostics.events import CanonicalEventLogger
    from ..runner import BrainRunner
    from ..schemas import WorkingState


_FRESHNESS_CLASSIFIER_PROMPT = (
    "You are an internal freshness-routing classifier. Decide whether the user "
    "request depends on current or rapidly changing facts. Return JSON only with "
    "fields: intent, domain, time_sensitive, needs_live_data, needs_sources, "
    "needs_exact_date, answer_mode, reason, confidence. "
    "Use answer_mode='browse_then_answer' when live evidence is required before "
    "answering. Use answer_mode='local_only' when the request can be answered "
    "without live verification. Set needs_exact_date=true whenever stale "
    "calendar assumptions would materially degrade the answer quality, including "
    "requests for latest/current/recent news, market-sensitive research, live "
    "stock baskets, or any answer that must be anchored to today's date rather "
    "than an assumed month or year."
)

_DIRECT_TOOL_VERB_RE = re.compile(
    r"^\s*(?:please\s+)?(?:use|run|call|invoke)\s+([a-z][a-z0-9_.-]*)\b",
    re.IGNORECASE,
)


def _direct_tool_request_name(user_input: str) -> str | None:
    request_text = str(user_input or "").strip()
    if not request_text:
        return None
    parts = request_text.split(maxsplit=2)
    if len(parts) >= 2 and parts[0].lower() == "tool":
        return normalize_tool_name_for_brain(parts[1]) or None
    match = _DIRECT_TOOL_VERB_RE.match(request_text)
    if not match:
        return None
    return normalize_tool_name_for_brain(match.group(1)) or None


def _build_freshness_context(*, user_input: str) -> dict[str, Any]:
    return {
        "messages": [
            {"role": "system", "content": _FRESHNESS_CLASSIFIER_PROMPT},
            {
                "role": "user",
                "content": (
                    f'User message: "{user_input}"\n\n'
                    "Classify freshness sensitivity semantically. Do not rely on "
                    "keyword lists. Return a JSON object only."
                ),
            },
        ],
        "hints": {
            "user_input": user_input,
            "current_datetime": iso_now(),
            "mode_name": "freshness_classify",
        },
    }


def map_freshness_obligations(
    contract: FreshnessContract,
) -> FreshnessObligations:
    return FreshnessObligations(
        require_live_data=bool(contract.needs_live_data),
        require_sources=bool(contract.needs_sources),
        require_exact_date=bool(contract.needs_exact_date),
        require_explicit_failure_wording=bool(contract.needs_live_data),
        answer_mode=contract.answer_mode,
    )


def classify_request_freshness(
    runner: "BrainRunner",
    *,
    state: "WorkingState",
    user_input: str | None,
    logger: "CanonicalEventLogger",
) -> tuple[FreshnessContract, FreshnessObligations, FreshnessDiagnostics]:
    del logger
    request_text = str(user_input or "").strip()
    model = (
        str(getattr(runner.profile.llm_profiles, "decide_model", "") or "").strip()
        or str(getattr(runner.profile.llm_profiles, "act_model", "") or "").strip()
    )
    diagnostics = FreshnessDiagnostics(
        classifier_mode="llm",
        classifier_model=model,
        classified_at=iso_now(),
    )
    if not request_text:
        diagnostics.classifier_mode = "skipped_empty_request"
        diagnostics.notes.append(
            "No user input was available for freshness classification."
        )
        contract = FreshnessContract()
        return contract, map_freshness_obligations(contract), diagnostics
    direct_tool_name = _direct_tool_request_name(request_text)
    if direct_tool_name:
        diagnostics.classifier_mode = "skipped_direct_tool_request"
        diagnostics.notes.append(
            f"Freshness classification skipped for explicit tool request '{direct_tool_name}'."
        )
        contract = FreshnessContract(intent=direct_tool_name)
        return contract, map_freshness_obligations(contract), diagnostics
    if getattr(runner, "llm_api", None) is None or not model:
        diagnostics.classifier_mode = "skipped_missing_llm"
        diagnostics.notes.append(
            "Classifier skipped because no LLM model was available."
        )
        contract = FreshnessContract()
        return contract, map_freshness_obligations(contract), diagnostics

    try:
        raw = call_structured_with_retry(
            runner.llm_api,
            model=model,
            purpose="freshness_classify",
            context=_build_freshness_context(user_input=request_text),
            schema=FreshnessContract,
        )
        contract = FreshnessContract.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        diagnostics.classifier_mode = "classifier_failed"
        diagnostics.notes.append(f"classifier_error={exc}")
        contract = FreshnessContract()
        return contract, map_freshness_obligations(contract), diagnostics
    obligations = map_freshness_obligations(contract)
    if contract.time_sensitive:
        diagnostics.notes.append(
            "LLM classified the request as freshness-sensitive; runtime obligations were enabled."
        )
    else:
        diagnostics.notes.append(
            "LLM classified the request as safe for local-only answering."
        )
    if state.trace_id:
        diagnostics.notes.append(f"trace_id={state.trace_id}")
    return contract, obligations, diagnostics


def build_freshness_hints(
    *,
    contract: FreshnessContract | None,
    obligations: FreshnessObligations | None,
) -> dict[str, Any]:
    if contract is None or obligations is None or not contract.time_sensitive:
        return {}
    hints: dict[str, Any] = {
        "freshness_contract": contract.model_dump(mode="json"),
        "freshness_obligations": obligations.model_dump(mode="json"),
    }
    if obligations.require_exact_date:
        hints["style_overrides"] = {
            "freshness_exact_date_contract": (
                "Anchor any live-search framing and final chronology to the typed "
                "current_datetime and any evidence_date facts already present in "
                "context. Do not assume a month or year that is not supported by "
                "those typed date facts. If exact dated evidence is missing after "
                "lookup, say the timeframe could not be confirmed instead of "
                "inventing one."
            )
        }
    return hints


__all__ = [
    "build_freshness_hints",
    "classify_request_freshness",
    "map_freshness_obligations",
]
