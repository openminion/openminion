import uuid
from collections.abc import Callable, Mapping
from typing import Any, Optional

from ..constants import LLM_CANDIDATE_STATUS_SUCCESS
from ..errors import LLMCtlError
from .coercion import _first_positive_int
from .schemas import (
    AgentLLMBudgets,
    AgentLLMPolicy,
    CandidateResponse,
    EnsembleRoute,
    EnsembleTemplate,
    LLMCatalogDefaults,
    LLMRoute,
    Rubric,
    RuntimeLLMRequest,
    SelectionResult,
    SingleRoute,
)


def select_candidate(
    *,
    request: RuntimeLLMRequest,
    candidates: list[CandidateResponse],
    strategy: EnsembleTemplate,
    judge: Callable[
        [RuntimeLLMRequest, list[CandidateResponse], str, Optional[Rubric]],
        SelectionResult,
    ],
    heuristic_selector: Callable[[list[CandidateResponse]], SelectionResult],
) -> Optional[SelectionResult]:
    successful = [
        item for item in candidates if item.status == LLM_CANDIDATE_STATUS_SUCCESS
    ]
    if not successful:
        return None

    policy = strategy.selection_policy
    if policy == "pick_primary_if_ok":
        primary = next(
            (
                item
                for item in candidates[:1]
                if item.status == LLM_CANDIDATE_STATUS_SUCCESS
            ),
            None,
        )
        if primary is not None:
            return SelectionResult(
                winner_candidate_id=primary.candidate_id,
                winner_profile_id=primary.profile_id,
                scores=None,
                reasons=["Primary candidate succeeded"],
                risk_flags=None,
            )
        first = successful[0]
        return SelectionResult(
            winner_candidate_id=first.candidate_id,
            winner_profile_id=first.profile_id,
            scores=None,
            reasons=["Primary failed; picked first successful candidate"],
            risk_flags=["primary_failed"],
        )

    if policy in {"pick_highest_score", "majority_vote"} and strategy.judge_profile_id:
        return judge(
            request,
            successful,
            strategy.judge_profile_id,
            rubric=strategy.rubric,
        )

    if policy in {"first_success", "ask_user_on_disagreement"}:
        first = successful[0]
        reason = "First successful candidate selected"
        if policy == "ask_user_on_disagreement":
            reason = (
                "Interactive user selection not available; selected first successful "
                "candidate"
            )
        return SelectionResult(
            winner_candidate_id=first.candidate_id,
            winner_profile_id=first.profile_id,
            scores=None,
            reasons=[reason],
            risk_flags=["interactive_selection_skipped"]
            if policy == "ask_user_on_disagreement"
            else None,
        )

    return heuristic_selector(successful)


def heuristic_selection(candidates: list[CandidateResponse]) -> SelectionResult:
    winner = candidates[0]
    return SelectionResult(
        winner_candidate_id=winner.candidate_id,
        winner_profile_id=winner.profile_id,
        scores=None,
        reasons=[
            "No judge result was available; selected first successful candidate by "
            "configured provider order."
        ],
        risk_flags=["judge_unavailable_first_success"],
    )


def resolve_ensemble_strategy(
    *,
    route: EnsembleRoute,
    ensembles: Mapping[str, EnsembleTemplate],
    defaults: LLMCatalogDefaults,
) -> EnsembleTemplate:
    if route.strategy_inline is not None:
        strategy = route.strategy_inline
    elif route.strategy_id is not None:
        if route.strategy_id not in ensembles:
            raise LLMCtlError(
                "INVALID_ARGUMENT",
                f"Unknown strategy_id: {route.strategy_id}",
                {"strategy_id": route.strategy_id},
            )
        strategy = ensembles[route.strategy_id]
    else:
        strategy = EnsembleTemplate(
            id=f"inline:{uuid.uuid4().hex[:8]}",
            mode="second_opinion",
            providers=list(route.providers or []),
            selection_policy=defaults.default_selection_policy,
            timeout_ms=defaults.default_timeout_ms,
            max_parallel=defaults.default_max_parallel,
            rubric=defaults.default_rubric,
        )

    updates: dict[str, Any] = {}
    if route.providers is not None:
        updates["providers"] = list(route.providers)
    if route.judge_profile_id is not None:
        updates["judge_profile_id"] = route.judge_profile_id
    if route.selection_policy is not None:
        updates["selection_policy"] = route.selection_policy
    if route.rubric is not None:
        updates["rubric"] = route.rubric
    if route.timeout_ms is not None:
        updates["timeout_ms"] = route.timeout_ms
    if route.max_parallel is not None:
        updates["max_parallel"] = route.max_parallel
    if route.stop_early is not None:
        updates["stop_early"] = route.stop_early
    if updates:
        strategy = strategy.model_copy(update=updates)
    return strategy


def resolve_ensemble_provider_ids(
    route: EnsembleRoute,
    strategy: EnsembleTemplate,
    *,
    resolve_profile: Callable[[str], object],
) -> list[str]:
    providers = list(route.providers or strategy.providers)
    if not providers:
        raise LLMCtlError("INVALID_ARGUMENT", "Ensemble route has no providers")
    for profile_id in providers:
        resolve_profile(profile_id)
    return providers


def expand_self_consistency(
    providers: list[str], strategy: EnsembleTemplate
) -> list[str]:
    if strategy.mode != "self_consistency":
        return providers
    if len(providers) != 1:
        return providers
    fanout = _first_positive_int(strategy.max_parallel, 1)
    return [providers[0] for _ in range(max(1, fanout))]


def enforce_route_access(
    route: LLMRoute,
    policy: AgentLLMPolicy,
    *,
    route_profile_ids: Callable[[LLMRoute], list[str]],
) -> None:
    profile_ids = route_profile_ids(route)
    if policy.allow_profiles is not None:
        allow = set(policy.allow_profiles)
        denied = sorted([item for item in profile_ids if item not in allow])
        if denied:
            raise LLMCtlError(
                "POLICY_DENIED",
                "LLM profile disallowed by allow_profiles",
                {"denied_profiles": denied, "error_code": "LLM_PROFILE_DISALLOWED"},
            )
    if policy.deny_profiles is not None:
        deny = set(policy.deny_profiles)
        blocked = sorted([item for item in profile_ids if item in deny])
        if blocked:
            raise LLMCtlError(
                "POLICY_DENIED",
                "LLM profile disallowed by deny_profiles",
                {
                    "denied_profiles": blocked,
                    "error_code": "LLM_PROFILE_DISALLOWED",
                },
            )


def route_profile_ids(
    route: LLMRoute,
    *,
    ensembles: Mapping[str, EnsembleTemplate],
    resolve_profile: Callable[[str], object],
) -> list[str]:
    if isinstance(route, SingleRoute):
        resolve_profile(route.profile_id)
        return [route.profile_id]
    ids: list[str] = []
    if route.providers:
        ids.extend(route.providers)
    if route.strategy_id and route.strategy_id in ensembles:
        ids.extend(ensembles[route.strategy_id].providers)
    if route.strategy_inline:
        ids.extend(route.strategy_inline.providers)
    if route.judge_profile_id:
        ids.append(route.judge_profile_id)
    unique = []
    for item in ids:
        if item not in unique:
            unique.append(item)
    for item in unique:
        resolve_profile(item)
    return unique


def apply_budget_clamps(
    request: RuntimeLLMRequest,
    budgets: AgentLLMBudgets,
    *,
    max_tokens_per_call_hard: int,
) -> RuntimeLLMRequest:
    # Keep agent-level orchestration budgets distinct from llmctl BudgetPolicy.
    # These clamps shape request budget for orchestration routing/turn semantics.
    clamped_tokens = min(
        int(request.budget.max_tokens), max(1, int(budgets.max_tokens_per_call))
    )
    clamped_tokens = min(clamped_tokens, max(1, int(max_tokens_per_call_hard)))
    clamped_timeout = min(
        int(request.budget.timeout_ms), max(1, int(budgets.max_time_ms_per_turn))
    )
    budget = request.budget.model_copy(
        update={"max_tokens": clamped_tokens, "timeout_ms": clamped_timeout}
    )
    return request.model_copy(update={"budget": budget})


def apply_ensemble_budget_clamps(
    strategy: EnsembleTemplate,
    route: EnsembleRoute,
    budgets: AgentLLMBudgets,
    *,
    max_parallel_global: int,
) -> EnsembleTemplate:
    max_parallel = min(
        max(1, int(strategy.max_parallel)),
        max(1, int(budgets.max_parallel)),
        max(1, int(max_parallel_global)),
    )
    updates: dict[str, Any] = {"max_parallel": max_parallel}
    if route.fanout is not None and strategy.mode == "self_consistency":
        clamped_fanout = min(
            max(1, int(route.fanout)), max(1, int(budgets.max_ensemble_fanout))
        )
        updates["max_parallel"] = min(max_parallel, clamped_fanout)
    return strategy.model_copy(update=updates)
