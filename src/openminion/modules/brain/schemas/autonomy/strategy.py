from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field

from .progress import (
    NoProgressWatchdogKind,
    NoProgressWatchdogTrigger,
    ProgressSignal,
)
from ..goals import VerifierFamily


class PivotAuthorization(BaseModel):
    """Typed runtime authorization for a strategy pivot."""

    model_config = ConfigDict(extra="forbid")

    authorized: bool
    trigger_kind: NoProgressWatchdogKind | None = None
    pivot_policy_allows: bool = True


def compose_pivot_authorization(
    *,
    watchdog_trigger: NoProgressWatchdogTrigger,
    pivot_policy_allows: bool = True,
) -> PivotAuthorization:
    """Pure structural composer for ``PivotAuthorization``."""

    if not watchdog_trigger.fired or not pivot_policy_allows:
        return PivotAuthorization(
            authorized=False,
            trigger_kind=None,
            pivot_policy_allows=pivot_policy_allows,
        )
    first_kind = watchdog_trigger.kinds[0] if watchdog_trigger.kinds else None
    return PivotAuthorization(
        authorized=first_kind is not None,
        trigger_kind=first_kind,
        pivot_policy_allows=pivot_policy_allows,
    )


class StrategyPivotEvent(BaseModel):
    """Typed runtime-emitted record of a mid-loop strategy transition."""

    model_config = ConfigDict(extra="forbid")

    from_route: str = Field(min_length=1)
    to_route: str = Field(min_length=1)
    authorization: PivotAuthorization
    turn_index: int = Field(ge=0)


def compose_strategy_pivot_event(
    *,
    from_route: str,
    to_route: str,
    authorization: PivotAuthorization,
    turn_index: int,
) -> StrategyPivotEvent:
    """Pure structural composer for ``StrategyPivotEvent``."""

    if not authorization.authorized:
        raise ValueError("StrategyPivotEvent requires authorized PivotAuthorization")

    from openminion.modules.brain.bootstrap.route_catalog import (
        registered_routes,
    )

    catalog = set(registered_routes())
    if from_route not in catalog:
        raise ValueError(
            f"from_route {from_route!r} is not in the registered route catalog"
        )
    if to_route not in catalog:
        raise ValueError(
            f"to_route {to_route!r} is not in the registered route catalog"
        )

    return StrategyPivotEvent(
        from_route=from_route,
        to_route=to_route,
        authorization=authorization,
        turn_index=int(turn_index),
    )


class ResearchConvergenceConfig(BaseModel):
    """Operator-tunable per-axis thresholds for structural convergence."""

    model_config = ConfigDict(extra="forbid")

    min_typed_finding_count: int = Field(default=3, ge=0)
    min_source_coverage: int = Field(default=2, ge=0)
    require_no_new_evidence: bool = True


class ResearchConvergenceCounters(BaseModel):
    """Typed structural counter vector consumed by convergence."""

    model_config = ConfigDict(extra="forbid")

    typed_finding_count: int = Field(default=0, ge=0)
    source_coverage: int = Field(default=0, ge=0)
    new_evidence_delta: int = Field(default=0, ge=0)
    verifier_family_counts: Mapping[VerifierFamily, int] = Field(default_factory=dict)


class ResearchConvergenceSignal(BaseModel):
    """Typed structural convergence signal. ASRR-02 owner surface."""

    model_config = ConfigDict(extra="forbid")

    converged: bool
    reason_axes: tuple[str, ...] = Field(default_factory=tuple)
    counters: ResearchConvergenceCounters
    config: ResearchConvergenceConfig
    progress: ProgressSignal


RESEARCH_CONVERGENCE_AXES: tuple[str, ...] = (
    "typed_finding_count",
    "source_coverage",
    "no_new_evidence",
    "verifier_family_consultation",
)


def compose_research_convergence_signal(
    *,
    counters: ResearchConvergenceCounters,
    config: ResearchConvergenceConfig,
    progress: ProgressSignal,
) -> ResearchConvergenceSignal:
    """Pure structural composer for ``ResearchConvergenceSignal``."""

    threshold_finding = config.min_typed_finding_count
    threshold_coverage = config.min_source_coverage

    axis_finding = threshold_finding == 0 or (
        counters.typed_finding_count >= threshold_finding
    )
    axis_coverage = threshold_coverage == 0 or (
        counters.source_coverage >= threshold_coverage
    )
    if config.require_no_new_evidence:
        axis_no_new_evidence = (
            counters.new_evidence_delta == 0 and not progress.forward_motion
        )
    else:
        axis_no_new_evidence = True

    verifier_family_consulted = bool(counters.verifier_family_counts)
    axis_verifier_family = False
    if verifier_family_consulted:
        axis_verifier_family = any(
            int(count) > 0 for count in counters.verifier_family_counts.values()
        )

    required_satisfied = axis_finding and axis_coverage and axis_no_new_evidence
    converged = bool(required_satisfied)

    reasons: list[str] = []
    if axis_finding and threshold_finding > 0:
        reasons.append("typed_finding_count")
    if axis_coverage and threshold_coverage > 0:
        reasons.append("source_coverage")
    if axis_no_new_evidence and config.require_no_new_evidence:
        reasons.append("no_new_evidence")
    if verifier_family_consulted and axis_verifier_family:
        reasons.append("verifier_family_consultation")

    return ResearchConvergenceSignal(
        converged=converged,
        reason_axes=tuple(reasons),
        counters=counters,
        config=config,
        progress=progress,
    )


__all__ = [
    "PivotAuthorization",
    "RESEARCH_CONVERGENCE_AXES",
    "ResearchConvergenceConfig",
    "ResearchConvergenceCounters",
    "ResearchConvergenceSignal",
    "StrategyPivotEvent",
    "compose_pivot_authorization",
    "compose_research_convergence_signal",
    "compose_strategy_pivot_event",
]
