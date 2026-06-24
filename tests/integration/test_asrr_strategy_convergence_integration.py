from __future__ import annotations

from openminion.modules.brain.bootstrap.route_catalog import registered_routes
from openminion.modules.brain.schemas.autonomy.progress import (
    NoProgressWatchdogConfig,
    NoProgressWatchdogCounters,
    ProgressSignal,
    compose_progress_signal,
    evaluate_no_progress_watchdog,
)
from openminion.modules.brain.schemas.autonomy.strategy import (
    PivotAuthorization,
    ResearchConvergenceConfig,
    ResearchConvergenceCounters,
    StrategyPivotEvent,
    compose_pivot_authorization,
    compose_research_convergence_signal,
    compose_strategy_pivot_event,
)


def test_asrr_pivot_path_end_to_end_with_apbr_watchdog() -> None:

    # Turn 0: counters fresh, watchdog has not yet fired.
    config = NoProgressWatchdogConfig()  # defaults
    counters_t0 = NoProgressWatchdogCounters()
    trigger_t0 = evaluate_no_progress_watchdog(counters=counters_t0, config=config)
    assert trigger_t0.fired is False
    auth_t0 = compose_pivot_authorization(
        watchdog_trigger=trigger_t0, pivot_policy_allows=True
    )
    assert auth_t0.authorized is False
    assert auth_t0.trigger_kind is None

    # Turn N: circular-repeat counter has accumulated to the threshold.
    counters_tN = NoProgressWatchdogCounters(
        circular_repeat_count=config.circular_repeat_threshold
    )
    trigger_tN = evaluate_no_progress_watchdog(counters=counters_tN, config=config)
    assert trigger_tN.fired is True
    assert "circular_repeat" in trigger_tN.kinds

    # Runtime authorizes the pivot structurally.
    auth_tN = compose_pivot_authorization(
        watchdog_trigger=trigger_tN, pivot_policy_allows=True
    )
    assert auth_tN.authorized is True
    assert auth_tN.trigger_kind == "circular_repeat"

    # Model chooses a new route from the typed catalog. The runtime
    # NEVER does this step; it only validates the model's choice
    # against the catalog at compose-time.
    catalog = registered_routes()
    assert "respond" in catalog and "act" in catalog
    model_chosen_route = "act"
    assert model_chosen_route in catalog

    # Runtime composes the typed event; validation accepts the catalog
    # route and rejects anything outside it (ASRR-Q5: reject).
    event = compose_strategy_pivot_event(
        from_route="respond",
        to_route=model_chosen_route,
        authorization=auth_tN,
        turn_index=8,
    )
    assert isinstance(event, StrategyPivotEvent)
    assert event.from_route == "respond"
    assert event.to_route == "act"
    assert event.authorization.authorized is True
    assert event.authorization.trigger_kind == "circular_repeat"
    assert event.turn_index == 8


def test_asrr_pivot_path_respects_operator_config_gate() -> None:

    config = NoProgressWatchdogConfig()
    counters = NoProgressWatchdogCounters(
        circular_repeat_count=config.circular_repeat_threshold
    )
    trigger = evaluate_no_progress_watchdog(counters=counters, config=config)
    assert trigger.fired is True

    auth = compose_pivot_authorization(
        watchdog_trigger=trigger, pivot_policy_allows=False
    )
    assert auth.authorized is False
    assert auth.trigger_kind is None
    assert auth.pivot_policy_allows is False


def test_asrr_convergence_path_end_to_end_structural_only() -> None:

    cfg = ResearchConvergenceConfig(
        min_typed_finding_count=3,
        min_source_coverage=2,
        require_no_new_evidence=True,
    )

    # Iteration 1: one finding, one source pair, new evidence this
    # turn → not converged.
    counters_i1 = ResearchConvergenceCounters(
        typed_finding_count=1,
        source_coverage=1,
        new_evidence_delta=1,
    )
    progress_i1 = compose_progress_signal(turn_index=0, new_typed_record_delta=1)
    signal_i1 = compose_research_convergence_signal(
        counters=counters_i1, config=cfg, progress=progress_i1
    )
    assert signal_i1.converged is False

    # Iteration 2: two findings, two source pairs, new evidence this
    # turn → still not converged.
    counters_i2 = ResearchConvergenceCounters(
        typed_finding_count=2,
        source_coverage=2,
        new_evidence_delta=1,
    )
    progress_i2 = compose_progress_signal(turn_index=1, new_typed_record_delta=1)
    signal_i2 = compose_research_convergence_signal(
        counters=counters_i2, config=cfg, progress=progress_i2
    )
    assert signal_i2.converged is False

    # Iteration 3: three findings, two pairs, but new evidence STILL
    # arrived this turn → blocked by no_new_evidence axis.
    counters_i3 = ResearchConvergenceCounters(
        typed_finding_count=3,
        source_coverage=2,
        new_evidence_delta=1,
    )
    progress_i3 = compose_progress_signal(turn_index=2, new_typed_record_delta=1)
    signal_i3 = compose_research_convergence_signal(
        counters=counters_i3, config=cfg, progress=progress_i3
    )
    assert signal_i3.converged is False

    # Iteration 4: counters meet thresholds AND no new evidence this
    # turn → converged. reason_axes lists all satisfied required axes.
    counters_i4 = ResearchConvergenceCounters(
        typed_finding_count=3,
        source_coverage=2,
        new_evidence_delta=0,
    )
    progress_i4 = ProgressSignal(turn_index=3, forward_motion=False)
    signal_i4 = compose_research_convergence_signal(
        counters=counters_i4, config=cfg, progress=progress_i4
    )
    assert signal_i4.converged is True
    assert set(signal_i4.reason_axes) >= {
        "typed_finding_count",
        "source_coverage",
        "no_new_evidence",
    }
    # Verifier-family axis NOT consulted (counts empty) → not in reasons.
    assert "verifier_family_consultation" not in signal_i4.reason_axes


def test_asrr_convergence_path_consults_verifier_family_axis_when_supplied() -> None:

    cfg = ResearchConvergenceConfig()
    counters = ResearchConvergenceCounters(
        typed_finding_count=4,
        source_coverage=3,
        new_evidence_delta=0,
        verifier_family_counts={"structural": 1, "artifact_presence": 2},
    )
    progress = ProgressSignal(turn_index=5, forward_motion=False)
    signal = compose_research_convergence_signal(
        counters=counters, config=cfg, progress=progress
    )
    assert signal.converged is True
    assert "verifier_family_consultation" in signal.reason_axes


def test_asrr_runtime_authorizes_model_chooses_split_is_load_bearing() -> None:

    auth_sig = PivotAuthorization.model_fields
    # Authorization carries only structural fields; no prose.
    assert set(auth_sig) == {"authorized", "trigger_kind", "pivot_policy_allows"}

    event_sig = StrategyPivotEvent.model_fields
    # The event carries only typed structural fields; no prose.
    assert set(event_sig) == {
        "from_route",
        "to_route",
        "authorization",
        "turn_index",
    }
