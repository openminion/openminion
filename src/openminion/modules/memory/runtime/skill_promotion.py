from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sophiagraph.audit.events import MemoryAuditEvent

from openminion.modules.memory.storage.audit import MemoryAuditSink

if TYPE_CHECKING:
    from openminion.modules.skill.config import SkillConfig
    from openminion.modules.skill.proposal.promotion import PromotionPassReport


SKILL_PROMOTION_PASS_EVENT_TYPE = "skill_promotion_pass"


@dataclass(frozen=True)
class SkillPromotionRunResult:
    enabled: bool
    dry_run: bool
    report: "PromotionPassReport | None"


def _emit_audit_event(
    audit_sink: MemoryAuditSink | None, report: "PromotionPassReport"
) -> None:
    if audit_sink is None:
        return
    try:
        audit_sink.append_event(
            MemoryAuditEvent(
                event_type=SKILL_PROMOTION_PASS_EVENT_TYPE,
                target_kind="batch",
                details={
                    "candidates_considered": int(report.candidates_considered),
                    "proposals_drafted": int(report.proposals_drafted),
                    "auto_approved_structural_duplicates": int(
                        report.auto_approved_structural_duplicates
                    ),
                    "pending_operator_review": int(report.pending_operator_review),
                    "apply_emergent_results_count": len(report.apply_emergent_results),
                    "skipped_reasons": dict(report.skipped_reasons),
                    "dry_run": bool(report.dry_run),
                },
            )
        )
    except Exception:
        # Best-effort audit — never block the cadence on audit-sink failure.
        return


def run_skill_promotion_cadence_once(
    *,
    config: "SkillConfig",
    memory_api: Any,
    audit_sink: MemoryAuditSink | None = None,
    force_enabled: bool = False,
) -> SkillPromotionRunResult:
    enabled = bool(config.promotion_cadence_enabled) or bool(force_enabled)
    if not enabled:
        return SkillPromotionRunResult(enabled=False, dry_run=True, report=None)

    from openminion.modules.skill.proposal.promotion import run_promotion_pass

    report = run_promotion_pass(
        memory_api,
        success_threshold=int(config.promotion_cadence_success_threshold),
        utility_threshold=float(config.promotion_cadence_utility_threshold),
        dry_run=False,
    )
    _emit_audit_event(audit_sink, report)
    return SkillPromotionRunResult(
        enabled=True,
        dry_run=False,
        report=report,
    )


__all__ = [
    "SKILL_PROMOTION_PASS_EVENT_TYPE",
    "SkillPromotionRunResult",
    "run_skill_promotion_cadence_once",
]
