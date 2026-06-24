from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.loop.tools.contracts import (
    ADAPTIVE_TERM_ITERATION_CAP,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
)
from openminion.modules.brain.runtime.failures import (
    FailurePatternBucket,
    FailurePatternReadout,
)
from openminion.modules.brain.runtime.attribution import (
    AttributionAggregateRow,
    AttributionReadout,
)
from openminion.modules.brain.runtime.performance import (
    PerformanceRegistry,
    PerformanceRegistryEntry,
)
from openminion.modules.brain.runtime.improvement.bridge import (
    stage_improvement_decision,
)
from openminion.modules.brain.runtime.improvement.contracts import (
    ImprovementDecision,
    OnlineImprovementEval,
    SelfImprovementPolicy,
    SelfImprovementReplayBundle,
)
from openminion.modules.brain.runtime.improvement.judgment import (
    attach_decision_to_adaptive_outcome,
    decide_for_adaptive_outcome,
    decide_online_improvement,
    evaluation_from_adaptive_outcome,
)
from openminion.modules.brain.runtime.improvement.readout import (
    compose_self_improvement_readout,
)
from openminion.modules.brain.runtime.improvement.replay import (
    evaluate_replay_bundle,
    suppress_loop_policy_candidate,
)


class _MemoryApi:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def stage_candidate(self, **kwargs):
        self.calls.append(dict(kwargs))
        return f"cand-{len(self.calls)}"


def _runner(memory_api: _MemoryApi | None = None):
    return SimpleNamespace(
        profile=SimpleNamespace(agent_id="agent-1"),
        memory_api=memory_api,
    )


def test_disabled_policy_ignores_online_evaluation() -> None:
    decision = decide_online_improvement(
        OnlineImprovementEval(
            attempt_id="attempt-1",
            trace_id="trace-1",
            mode_name="act",
            outcome_status="failure",
            anomaly_score=1.0,
            evidence_refs=["trace:trace-1"],
        )
    )
    assert decision.action == "ignore"
    assert decision.rationale_code == "policy_disabled"


def test_online_judgment_stages_candidate_when_external_evidence_exists() -> None:
    decision = decide_online_improvement(
        OnlineImprovementEval(
            attempt_id="attempt-1",
            trace_id="trace-1",
            mode_name="act",
            outcome_status="failure",
            anomaly_score=0.72,
            evidence_refs=["trace:trace-1"],
        ),
        policy=SelfImprovementPolicy(
            policy="post_run",
            max_staged_items_per_run=1,
            min_external_signal_count=1,
        ),
    )
    assert decision.action == "stage_candidate"
    assert decision.memory_kind == "failure_pattern"
    assert decision.rationale_code == "failure_with_external_evidence"


def test_online_judgment_retries_high_anomaly_when_budget_reserved() -> None:
    decision = decide_online_improvement(
        OnlineImprovementEval(
            attempt_id="attempt-1",
            trace_id="trace-1",
            mode_name="act",
            outcome_status="blocked",
            anomaly_score=0.95,
        ),
        policy=SelfImprovementPolicy(policy="anomaly", reserved_llm_calls=1),
    )
    assert decision.action == "retry_now"
    assert decision.memory_kind == "none"


def test_adaptive_outcome_projection_and_trigger_surface() -> None:
    state = AdaptiveToolLoopState(iteration=3, total_tool_calls=2)
    outcome = AdaptiveToolLoopOutcome(
        profile_name="coding",
        mode_name="act",
        termination_reason=ADAPTIVE_TERM_ITERATION_CAP,
        state=state,
        allowed_tools=frozenset({"file.read"}),
        tool_name="file.read",
    )
    evaluation = evaluation_from_adaptive_outcome(
        outcome,
        attempt_id="attempt-1",
        trace_id="trace-1",
        evidence_refs=["trace:trace-1"],
    )
    assert evaluation.outcome_status == "partial"
    assert evaluation.progress_delta == "positive"
    decision = decide_for_adaptive_outcome(
        outcome,
        policy=SelfImprovementPolicy(
            policy="checkpoint",
            max_staged_items_per_run=1,
        ),
        attempt_id="attempt-1",
        trace_id="trace-1",
        evidence_refs=["trace:trace-1"],
    )
    assert decision.action == "stage_candidate"


def test_adaptive_profile_carries_dormant_self_improvement_policy() -> None:
    policy = SelfImprovementPolicy(policy="checkpoint")
    profile = AdaptiveToolLoopProfile(
        profile_name="p",
        mode_name="act",
        allowed_tools=frozenset({"file.read"}),
        self_improvement_policy=policy,
    )
    assert profile.self_improvement_policy is policy


def test_adaptive_outcome_can_emit_typed_self_improvement_telemetry() -> None:
    outcome = AdaptiveToolLoopOutcome(
        profile_name="coding",
        mode_name="act",
        termination_reason=ADAPTIVE_TERM_ITERATION_CAP,
        state=AdaptiveToolLoopState(iteration=2, total_tool_calls=1),
        allowed_tools=frozenset({"file.read"}),
    )
    decision = attach_decision_to_adaptive_outcome(
        outcome,
        policy=SelfImprovementPolicy(
            policy="post_run",
            max_staged_items_per_run=1,
        ),
        attempt_id="attempt-1",
        trace_id="trace-1",
        evidence_refs=["trace:trace-1"],
    )
    payload = outcome.telemetry_payload()
    assert decision.action == "stage_candidate"
    assert payload["self_improvement.evaluation"]["trace_id"] == "trace-1"
    assert payload["self_improvement.decision"]["action"] == "stage_candidate"


def test_bridge_stages_only_stage_actions_through_memory_api() -> None:
    memory_api = _MemoryApi()
    state = SimpleNamespace(memory_candidates=[])
    result = stage_improvement_decision(
        _runner(memory_api),
        state=state,
        decision=ImprovementDecision(
            action="stage_candidate",
            rationale_code="failure_with_external_evidence",
            confidence=0.8,
            memory_kind="failure_pattern",
            tags=["recurring"],
        ),
        evaluation=OnlineImprovementEval(
            attempt_id="attempt-1",
            trace_id="trace-1",
            mode_name="act",
            outcome_status="failure",
            failure_reason_code="iteration_cap",
            evidence_refs=["trace:trace-1"],
        ),
    )
    assert result.candidate_id == "cand-1"
    assert result.skipped_reason is None
    assert state.memory_candidates == ["cand-1"]
    call = memory_api.calls[0]
    assert call["scope"] == "agent:agent-1"
    assert call["record_type"] == "failure_pattern"
    assert call["evidence_refs"] == ["trace:trace-1"]
    assert call["meta"]["source_self_improvement"] is True


def test_bridge_skips_non_stage_actions_and_missing_memory_api() -> None:
    state = SimpleNamespace(memory_candidates=[])
    skipped_action = stage_improvement_decision(
        _runner(_MemoryApi()),
        state=state,
        decision=ImprovementDecision(action="retry_now"),
        evaluation=OnlineImprovementEval(
            attempt_id="a",
            trace_id="t",
            mode_name="act",
            outcome_status="failure",
        ),
    )
    assert skipped_action.skipped_reason == "action_not_stageable"
    skipped_memory = stage_improvement_decision(
        _runner(None),
        state=state,
        decision=ImprovementDecision(
            action="stage_lesson",
            memory_kind="lesson",
        ),
        evaluation=OnlineImprovementEval(
            attempt_id="a",
            trace_id="t",
            mode_name="act",
            outcome_status="failure",
        ),
    )
    assert skipped_memory.skipped_reason == "memory_api_unavailable"


def test_readout_composes_attribution_failure_and_performance() -> None:
    readout = compose_self_improvement_readout(
        attribution=AttributionReadout(
            rows=[
                AttributionAggregateRow(
                    retrieved_record_id="cand-b",
                    total_events=2,
                    by_outcome_status={"success": 0, "failure": 2, "other": 0},
                ),
                AttributionAggregateRow(
                    retrieved_record_id="cand-a",
                    total_events=3,
                    by_outcome_status={"success": 3, "failure": 0, "other": 0},
                    distinct_traces=2,
                ),
            ]
        ),
        failure_patterns=FailurePatternReadout(
            rows=[
                FailurePatternBucket(
                    seam_id="adaptive_termination",
                    reason_code="iteration_cap",
                    recurrence_count=2,
                )
            ]
        ),
        performance=PerformanceRegistry(
            entries=[
                PerformanceRegistryEntry(
                    subject_kind="strategy",
                    subject_id="research",
                    success_count=1,
                )
            ]
        ),
        evidence_window={"traces": 3},
    )
    assert readout.failure_patterns.rows[0].reason_code == "iteration_cap"
    assert readout.performance.entries[0].subject_id == "research"
    assert [row["candidate_ref"] for row in readout.candidate_usefulness] == [
        "cand-a",
        "cand-b",
    ]
    assert readout.evidence_window == {"traces": 3}


def test_replay_bundle_promotes_only_with_evidence_and_metric_improvement() -> None:
    bundle = SelfImprovementReplayBundle(
        bundle_id="bundle-1",
        trace_ids=["trace-1"],
        candidate_ids=["candidate-1"],
        baseline_metrics={"success_rate": 0.4},
        challenger_metrics={"success_rate": 0.7},
    )
    verdict = evaluate_replay_bundle(bundle, candidate_id="candidate-1")
    assert verdict.verdict == "promote"
    assert verdict.reason_code == "challenger_metric_improved"
    assert verdict.supporting_metrics == {
        "baseline.success_rate": 0.4,
        "challenger.success_rate": 0.7,
    }
    assert verdict.evidence_refs == ["trace:trace-1"]


def test_replay_bundle_holds_without_external_evidence() -> None:
    bundle = SelfImprovementReplayBundle(
        bundle_id="bundle-1",
        candidate_ids=["candidate-1"],
        baseline_metrics={"success_rate": 0.4},
        challenger_metrics={"success_rate": 0.7},
    )
    verdict = evaluate_replay_bundle(bundle, candidate_id="candidate-1")
    assert verdict.verdict == "hold"
    assert verdict.reason_code == "insufficient_external_evidence"


def test_replay_bundle_rolls_back_regressions_and_suppresses_explicitly() -> None:
    bundle = SelfImprovementReplayBundle(
        bundle_id="bundle-1",
        trace_ids=["trace-1"],
        candidate_ids=["candidate-1"],
        baseline_metrics={"success_rate": 0.8},
        challenger_metrics={"success_rate": 0.5},
    )
    verdict = evaluate_replay_bundle(bundle, candidate_id="candidate-1")
    assert verdict.verdict == "rollback"
    assert verdict.reason_code == "challenger_metric_regressed"
    suppressed = suppress_loop_policy_candidate(
        candidate_id="candidate-1",
        reason_code="operator_rejected",
        evidence_refs=["review:1"],
    )
    assert suppressed.verdict == "suppress"
    assert suppressed.reason_code == "operator_rejected"
