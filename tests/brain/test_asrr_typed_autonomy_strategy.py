from __future__ import annotations

import pytest

from openminion.modules.brain.bootstrap.route_catalog import registered_routes
from openminion.modules.brain.schemas.autonomy.progress import (
    NoProgressWatchdogConfig,
    NoProgressWatchdogCounters,
    NoProgressWatchdogTrigger,
    ProgressSignal,
    compose_progress_signal,
    evaluate_no_progress_watchdog,
)
from openminion.modules.brain.schemas.autonomy.strategy import (
    RESEARCH_CONVERGENCE_AXES,
    ResearchConvergenceConfig,
    ResearchConvergenceCounters,
    ResearchConvergenceSignal,
    StrategyPivotEvent,
    compose_pivot_authorization,
    compose_research_convergence_signal,
    compose_strategy_pivot_event,
)


# PivotAuthorization + StrategyPivotEvent


def _watchdog_unfired() -> NoProgressWatchdogTrigger:
    return evaluate_no_progress_watchdog(
        counters=NoProgressWatchdogCounters(),
        config=NoProgressWatchdogConfig(),
    )


def _watchdog_fired_circular() -> NoProgressWatchdogTrigger:
    return evaluate_no_progress_watchdog(
        counters=NoProgressWatchdogCounters(circular_repeat_count=3),
        config=NoProgressWatchdogConfig(circular_repeat_threshold=3),
    )


def test_pivot_authorization_denied_when_watchdog_unfired() -> None:
    auth = compose_pivot_authorization(
        watchdog_trigger=_watchdog_unfired(),
        pivot_policy_allows=True,
    )
    assert auth.authorized is False
    assert auth.trigger_kind is None
    assert auth.pivot_policy_allows is True


def test_pivot_authorization_denied_when_policy_disallows() -> None:
    auth = compose_pivot_authorization(
        watchdog_trigger=_watchdog_fired_circular(),
        pivot_policy_allows=False,
    )
    assert auth.authorized is False
    assert auth.trigger_kind is None
    assert auth.pivot_policy_allows is False


def test_pivot_authorization_granted_when_watchdog_fires_and_policy_allows() -> None:
    auth = compose_pivot_authorization(
        watchdog_trigger=_watchdog_fired_circular(),
        pivot_policy_allows=True,
    )
    assert auth.authorized is True
    assert auth.trigger_kind == "circular_repeat"
    assert auth.pivot_policy_allows is True


def test_compose_strategy_pivot_event_requires_authorized_authorization() -> None:
    catalog = registered_routes()
    assert "respond" in catalog and "act" in catalog
    auth = compose_pivot_authorization(
        watchdog_trigger=_watchdog_unfired(),
        pivot_policy_allows=True,
    )
    assert auth.authorized is False
    with pytest.raises(ValueError, match="authorized"):
        compose_strategy_pivot_event(
            from_route="respond",
            to_route="act",
            authorization=auth,
            turn_index=4,
        )


def test_compose_strategy_pivot_event_rejects_unknown_to_route() -> None:
    auth = compose_pivot_authorization(
        watchdog_trigger=_watchdog_fired_circular(),
        pivot_policy_allows=True,
    )
    assert auth.authorized is True
    with pytest.raises(ValueError, match="to_route"):
        compose_strategy_pivot_event(
            from_route="respond",
            to_route="not_in_catalog",
            authorization=auth,
            turn_index=4,
        )


def test_compose_strategy_pivot_event_rejects_unknown_from_route() -> None:
    auth = compose_pivot_authorization(
        watchdog_trigger=_watchdog_fired_circular(),
        pivot_policy_allows=True,
    )
    with pytest.raises(ValueError, match="from_route"):
        compose_strategy_pivot_event(
            from_route="ghost_route",
            to_route="act",
            authorization=auth,
            turn_index=4,
        )


def test_compose_strategy_pivot_event_happy_path() -> None:
    auth = compose_pivot_authorization(
        watchdog_trigger=_watchdog_fired_circular(),
        pivot_policy_allows=True,
    )
    event = compose_strategy_pivot_event(
        from_route="respond",
        to_route="act",
        authorization=auth,
        turn_index=7,
    )
    assert isinstance(event, StrategyPivotEvent)
    assert event.from_route == "respond"
    assert event.to_route == "act"
    assert event.authorization.authorized is True
    assert event.authorization.trigger_kind == "circular_repeat"
    assert event.turn_index == 7


# ResearchConvergenceSignal


def _progress(forward_motion: bool, delta: int) -> ProgressSignal:
    return (
        compose_progress_signal(
            turn_index=0,
            new_typed_record_delta=delta,
        )
        if forward_motion
        else ProgressSignal(turn_index=0, forward_motion=False)
    )


def test_convergence_signal_blocked_by_insufficient_findings() -> None:
    counters = ResearchConvergenceCounters(
        typed_finding_count=1,
        source_coverage=2,
        new_evidence_delta=0,
    )
    config = ResearchConvergenceConfig(
        min_typed_finding_count=3,
        min_source_coverage=2,
        require_no_new_evidence=True,
    )
    progress = ProgressSignal(turn_index=0, forward_motion=False)
    signal = compose_research_convergence_signal(
        counters=counters, config=config, progress=progress
    )
    assert signal.converged is False
    assert "typed_finding_count" not in signal.reason_axes


def test_convergence_signal_blocked_by_insufficient_source_coverage() -> None:
    counters = ResearchConvergenceCounters(
        typed_finding_count=5,
        source_coverage=1,
        new_evidence_delta=0,
    )
    config = ResearchConvergenceConfig(
        min_typed_finding_count=3,
        min_source_coverage=2,
        require_no_new_evidence=True,
    )
    progress = ProgressSignal(turn_index=0, forward_motion=False)
    signal = compose_research_convergence_signal(
        counters=counters, config=config, progress=progress
    )
    assert signal.converged is False
    assert "source_coverage" not in signal.reason_axes


def test_convergence_signal_blocked_by_new_evidence_this_turn() -> None:
    counters = ResearchConvergenceCounters(
        typed_finding_count=5,
        source_coverage=3,
        new_evidence_delta=1,
    )
    config = ResearchConvergenceConfig(
        min_typed_finding_count=3,
        min_source_coverage=2,
        require_no_new_evidence=True,
    )
    # forward_motion True => still progressing
    progress = compose_progress_signal(turn_index=0, new_typed_record_delta=1)
    signal = compose_research_convergence_signal(
        counters=counters, config=config, progress=progress
    )
    assert signal.converged is False
    assert "no_new_evidence" not in signal.reason_axes


def test_convergence_signal_fires_when_all_required_axes_satisfied() -> None:
    counters = ResearchConvergenceCounters(
        typed_finding_count=5,
        source_coverage=3,
        new_evidence_delta=0,
    )
    config = ResearchConvergenceConfig(
        min_typed_finding_count=3,
        min_source_coverage=2,
        require_no_new_evidence=True,
    )
    progress = ProgressSignal(turn_index=0, forward_motion=False)
    signal = compose_research_convergence_signal(
        counters=counters, config=config, progress=progress
    )
    assert signal.converged is True
    assert set(signal.reason_axes) >= {
        "typed_finding_count",
        "source_coverage",
        "no_new_evidence",
    }


def test_convergence_signal_skips_verifier_family_axis_when_unsupplied() -> None:
    counters = ResearchConvergenceCounters(
        typed_finding_count=5,
        source_coverage=3,
        new_evidence_delta=0,
        verifier_family_counts={},  # ASRR-Q4 conditional: empty => skip
    )
    config = ResearchConvergenceConfig()
    progress = ProgressSignal(turn_index=0, forward_motion=False)
    signal = compose_research_convergence_signal(
        counters=counters, config=config, progress=progress
    )
    assert signal.converged is True
    assert "verifier_family_consultation" not in signal.reason_axes


def test_convergence_signal_consults_verifier_family_axis_when_supplied() -> None:
    counters = ResearchConvergenceCounters(
        typed_finding_count=5,
        source_coverage=3,
        new_evidence_delta=0,
        verifier_family_counts={"structural": 2, "freshness": 1},
    )
    config = ResearchConvergenceConfig()
    progress = ProgressSignal(turn_index=0, forward_motion=False)
    signal = compose_research_convergence_signal(
        counters=counters, config=config, progress=progress
    )
    assert signal.converged is True
    assert "verifier_family_consultation" in signal.reason_axes


def test_convergence_signal_axis_identifiers_are_a_closed_set() -> None:

    assert RESEARCH_CONVERGENCE_AXES == (
        "typed_finding_count",
        "source_coverage",
        "no_new_evidence",
        "verifier_family_consultation",
    )


def test_convergence_signal_has_no_prose_fields() -> None:

    fields = ResearchConvergenceSignal.model_fields
    assert "reasoning" not in fields
    assert "suggested_next_query" not in fields
    assert "notes" not in fields
    assert "narrative" not in fields
