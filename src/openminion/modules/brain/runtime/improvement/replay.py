"""Replay/eval promotion gates for self-improvement candidates."""

from typing import Mapping

from .contracts import LoopPolicyPromotionVerdict, SelfImprovementReplayBundle


def evaluate_replay_bundle(
    bundle: SelfImprovementReplayBundle | Mapping[str, object],
    *,
    candidate_id: str,
    metric_name: str = "success_rate",
    min_external_signal_count: int = 1,
) -> LoopPolicyPromotionVerdict:
    """Compare baseline/challenger metrics and emit a typed verdict."""

    bundle_obj = SelfImprovementReplayBundle.model_validate(bundle)
    candidate = str(candidate_id or "").strip()
    evidence_refs = _supporting_evidence_refs(bundle_obj)
    supporting_metrics = _supporting_metrics(bundle_obj, metric_name)

    def hold(reason_code: str) -> LoopPolicyPromotionVerdict:
        return LoopPolicyPromotionVerdict(
            candidate_id=candidate,
            verdict="hold",
            reason_code=reason_code,
            supporting_metrics=supporting_metrics,
            evidence_refs=evidence_refs,
        )

    if len(evidence_refs) < int(min_external_signal_count or 0):
        return hold("insufficient_external_evidence")
    if candidate not in bundle_obj.candidate_ids:
        return hold("candidate_not_in_bundle")
    if metric_name not in bundle_obj.baseline_metrics:
        return hold("missing_baseline_metric")
    if metric_name not in bundle_obj.challenger_metrics:
        return hold("missing_challenger_metric")

    baseline = float(bundle_obj.baseline_metrics[metric_name])
    challenger = float(bundle_obj.challenger_metrics[metric_name])
    if challenger > baseline:
        verdict = "promote"
        reason = "challenger_metric_improved"
    elif challenger < baseline:
        verdict = "rollback"
        reason = "challenger_metric_regressed"
    else:
        verdict = "hold"
        reason = "challenger_metric_unchanged"
    return LoopPolicyPromotionVerdict(
        candidate_id=candidate,
        verdict=verdict,
        reason_code=reason,
        supporting_metrics=supporting_metrics,
        evidence_refs=evidence_refs,
    )


def suppress_loop_policy_candidate(
    *,
    candidate_id: str,
    reason_code: str,
    evidence_refs: list[str] | None = None,
) -> LoopPolicyPromotionVerdict:
    """Emit a typed suppression verdict without applying durable lifecycle state."""

    return LoopPolicyPromotionVerdict(
        candidate_id=str(candidate_id or "").strip(),
        verdict="suppress",
        reason_code=str(reason_code or "").strip() or "operator_suppressed",
        supporting_metrics={},
        evidence_refs=[ref for ref in (evidence_refs or []) if str(ref).strip()],
    )


def _supporting_evidence_refs(bundle: SelfImprovementReplayBundle) -> list[str]:
    refs = [ref for ref in bundle.evidence_refs if str(ref).strip()]
    refs.extend(
        f"trace:{trace_id}" for trace_id in bundle.trace_ids if str(trace_id).strip()
    )
    return refs


def _supporting_metrics(
    bundle: SelfImprovementReplayBundle,
    metric_name: str,
) -> dict[str, float]:
    out: dict[str, float] = {}
    if metric_name in bundle.baseline_metrics:
        out[f"baseline.{metric_name}"] = float(bundle.baseline_metrics[metric_name])
    if metric_name in bundle.challenger_metrics:
        out[f"challenger.{metric_name}"] = float(bundle.challenger_metrics[metric_name])
    return out


__all__ = [
    "evaluate_replay_bundle",
    "suppress_loop_policy_candidate",
]
