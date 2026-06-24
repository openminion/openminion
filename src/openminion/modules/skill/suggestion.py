from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from openminion.base.time import utc_now_iso
from openminion.modules.skill.config import SkillConfig
from openminion.modules.skill.proposal import (
    SkillProposal,
    SkillProposalDraft,
    _catalog_duplicate_signatures,
)
from openminion.modules.skill.proposal.queue import (
    PROPOSAL_QUEUE_STATE_PENDING,
)
from openminion.modules.skill.proposal.review import SkillProposalReview
from openminion.modules.skill.storage.base import SkillStore


SUGGESTION_EVENT_SURFACED = "surfaced"
SUGGESTION_EVENT_AUTO_DISMISSED = "auto_dismissed"
SUGGESTION_EVENT_OUTCOME_RECORDED = "outcome_recorded"

DISMISS_REASON_STRUCTURAL_DUPLICATE = "structural_duplicate"
DISMISS_REASON_COOLDOWN_ACTIVE = "cooldown_active"

_DEFAULT_SKILL_CONFIG = SkillConfig()
DEFAULT_SUGGESTION_BATCH_CAP = _DEFAULT_SKILL_CONFIG.suggestion_batch_cap
DEFAULT_SUGGESTION_COOLDOWN_SECONDS = _DEFAULT_SKILL_CONFIG.suggestion_cooldown_seconds
DEFAULT_SUGGESTION_MIN_AGE_SECONDS = _DEFAULT_SKILL_CONFIG.suggestion_min_age_seconds

_CLI_INSPECT_FMT = "openminion skill proposal-inspect {proposal_id}"


@dataclass(frozen=True)
class SkillProposalSuggestion:
    """Operator-facing projection of a persisted SPRQ proposal."""

    proposal_id: str
    source_task_shape_ref: str
    proposer_policy_id: str
    proposed_at: str
    queue_state: str
    display_name: str
    short_description: str
    tags: tuple[str, ...]
    risk_class: str
    tools: tuple[str, ...]
    signature: tuple[str, str, str]
    first_seen_at: str
    cli_inspect_command: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "source_task_shape_ref": self.source_task_shape_ref,
            "proposer_policy_id": self.proposer_policy_id,
            "proposed_at": self.proposed_at,
            "queue_state": self.queue_state,
            "display_name": self.display_name,
            "short_description": self.short_description,
            "tags": list(self.tags),
            "risk_class": self.risk_class,
            "tools": list(self.tools),
            "signature": list(self.signature),
            "first_seen_at": self.first_seen_at,
            "cli_inspect_command": self.cli_inspect_command,
        }


@dataclass(frozen=True)
class SuggestionSurfacePass:
    """Report from one ``run_suggestion_surface_pass`` invocation."""

    surfaced: list[SkillProposalSuggestion] = field(default_factory=list)
    auto_dismissed: list[dict[str, Any]] = field(default_factory=list)
    pending_remaining: int = 0


@dataclass(frozen=True)
class SkillSuggestionStatus:
    """Typed suggestion status payload."""

    surfaced_count: int
    accepted_count: int
    rejected_count: int
    deferred_count: int
    auto_dismissed_count: int
    pending_count: int
    last_surfaced_at: str
    last_outcome_at: str
    auto_dismiss_reasons: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "surfaced_count": int(self.surfaced_count),
            "accepted_count": int(self.accepted_count),
            "rejected_count": int(self.rejected_count),
            "deferred_count": int(self.deferred_count),
            "auto_dismissed_count": int(self.auto_dismissed_count),
            "pending_count": int(self.pending_count),
            "last_surfaced_at": str(self.last_surfaced_at),
            "last_outcome_at": str(self.last_outcome_at),
            "auto_dismiss_reasons": dict(self.auto_dismiss_reasons),
        }


def proposal_signature(proposal: SkillProposal) -> tuple[str, str, str]:
    """Return the canonical structural signature for one proposal."""

    draft = proposal.proposed_skill_definition
    intents: list[Any] = []
    if isinstance(draft.applies_to, Mapping):
        intents = list(draft.applies_to.get("intents") or [])
    synthetic_row = {
        "skill_id": "",
        "name": str(draft.name or ""),
        "tags": list(draft.tags or []),
        "applies_to": {"intents": intents, "steps": []},
    }
    signatures = _catalog_duplicate_signatures([synthetic_row])
    if not signatures:
        return ("", "", "")
    return sorted(signatures)[0]


def _signature_key(signature: tuple[str, str, str]) -> str:
    return "\x1f".join(signature)


def _projected_suggestion(
    record: Mapping[str, Any],
    *,
    signature: tuple[str, str, str],
    first_seen_at: str,
) -> SkillProposalSuggestion:
    proposal_payload = record.get("proposal") or {}
    proposal = SkillProposal.model_validate(proposal_payload)
    draft: SkillProposalDraft = proposal.proposed_skill_definition
    proposal_id = str(record.get("proposal_id") or "")
    return SkillProposalSuggestion(
        proposal_id=proposal_id,
        source_task_shape_ref=str(record.get("source_task_shape_ref") or ""),
        proposer_policy_id=str(record.get("proposer_policy_id") or ""),
        proposed_at=str(record.get("proposed_at") or ""),
        queue_state=str(record.get("queue_state") or ""),
        display_name=str(draft.display_name or "").strip(),
        short_description=str(draft.short_description or "").strip(),
        tags=tuple(str(item) for item in (draft.tags or [])),
        risk_class=str(draft.risk_class or ""),
        tools=tuple(str(item) for item in (draft.tools or [])),
        signature=signature,
        first_seen_at=first_seen_at,
        cli_inspect_command=_CLI_INSPECT_FMT.format(proposal_id=proposal_id),
    )


def _parse_iso_to_epoch(value: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    cleaned = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned).timestamp()
    except (ValueError, TypeError):
        return None


def run_suggestion_surface_pass(
    store: SkillStore,
    *,
    now: str | None = None,
    batch_cap: int = DEFAULT_SUGGESTION_BATCH_CAP,
    cooldown_seconds: int = DEFAULT_SUGGESTION_COOLDOWN_SECONDS,
    min_age_seconds: int = DEFAULT_SUGGESTION_MIN_AGE_SECONDS,
) -> SuggestionSurfacePass:
    """Run one suggestion surface pass."""

    now_iso = str(now or utc_now_iso())
    now_epoch = _parse_iso_to_epoch(now_iso) or 0.0
    safe_batch_cap = max(1, int(batch_cap))
    safe_cooldown = max(0, int(cooldown_seconds))
    safe_min_age = max(0, int(min_age_seconds))

    pending = list(
        store.list_proposals(
            queue_state=PROPOSAL_QUEUE_STATE_PENDING,
            limit=max(safe_batch_cap * 4, 50),
        )
    )

    surfaced: list[SkillProposalSuggestion] = []
    dismissed: list[dict[str, Any]] = []
    pass_signatures_used: set[tuple[str, str, str]] = set()

    for record in pending:
        proposal = SkillProposal.model_validate(record["proposal"])
        proposal_id = str(record.get("proposal_id") or "")
        signature = proposal_signature(proposal)
        signature_key = _signature_key(signature)

        created_epoch = _parse_iso_to_epoch(record.get("created_at") or "")
        if (
            safe_min_age > 0
            and created_epoch is not None
            and now_epoch > 0
            and (now_epoch - created_epoch) < safe_min_age
        ):
            continue

        if signature in pass_signatures_used:
            _emit_dismiss(
                store,
                proposal_id=proposal_id,
                signature=signature_key,
                reason=DISMISS_REASON_STRUCTURAL_DUPLICATE,
                surfaced_at=now_iso,
            )
            dismissed.append(
                {
                    "proposal_id": proposal_id,
                    "signature": list(signature),
                    "reason": DISMISS_REASON_STRUCTURAL_DUPLICATE,
                }
            )
            continue

        last_surfaced = store.latest_surfaced_at_for_signature(signature=signature_key)
        last_surfaced_epoch = _parse_iso_to_epoch(last_surfaced or "")
        if (
            safe_cooldown > 0
            and last_surfaced_epoch is not None
            and now_epoch > 0
            and (now_epoch - last_surfaced_epoch) < safe_cooldown
        ):
            _emit_dismiss(
                store,
                proposal_id=proposal_id,
                signature=signature_key,
                reason=DISMISS_REASON_COOLDOWN_ACTIVE,
                surfaced_at=now_iso,
            )
            dismissed.append(
                {
                    "proposal_id": proposal_id,
                    "signature": list(signature),
                    "reason": DISMISS_REASON_COOLDOWN_ACTIVE,
                }
            )
            continue

        if len(surfaced) >= safe_batch_cap:
            break

        _emit_surfaced(
            store,
            proposal_id=proposal_id,
            signature=signature_key,
            surfaced_at=now_iso,
        )
        pass_signatures_used.add(signature)
        surfaced.append(
            _projected_suggestion(record, signature=signature, first_seen_at=now_iso)
        )

    pending_remaining = max(0, len(pending) - len(surfaced) - len(dismissed))
    return SuggestionSurfacePass(
        surfaced=surfaced,
        auto_dismissed=dismissed,
        pending_remaining=pending_remaining,
    )


def list_active_suggestions(
    store: SkillStore,
    *,
    limit: int = 50,
) -> list[SkillProposalSuggestion]:
    """Return current pending proposals as ``SkillProposalSuggestion`` rows."""

    rows = store.list_proposals(
        queue_state=PROPOSAL_QUEUE_STATE_PENDING,
        limit=max(1, int(limit)),
    )
    out: list[SkillProposalSuggestion] = []
    for record in rows:
        proposal = SkillProposal.model_validate(record["proposal"])
        signature = proposal_signature(proposal)
        signature_key = _signature_key(signature)
        first_seen_at = (
            store.latest_surfaced_at_for_signature(signature=signature_key) or ""
        )
        out.append(
            _projected_suggestion(
                record, signature=signature, first_seen_at=str(first_seen_at)
            )
        )
    return out


def record_outcome(
    store: SkillStore,
    *,
    proposal_id: str,
    review: SkillProposalReview,
    surfaced_at: str | None = None,
) -> None:
    """Persist one outcome audit row when an SPRQ review lands."""

    record = store.get_proposal(proposal_id=proposal_id)
    if record is None:
        return
    proposal = SkillProposal.model_validate(record["proposal"])
    signature = proposal_signature(proposal)
    store.record_suggestion_event(
        event_id=str(uuid.uuid4()),
        proposal_id=str(proposal_id),
        signature=_signature_key(signature),
        event_type=SUGGESTION_EVENT_OUTCOME_RECORDED,
        reason=None,
        outcome=str(review.status),
        surfaced_at=str(surfaced_at or utc_now_iso()),
    )


def suggestion_status(store: SkillStore) -> SkillSuggestionStatus:
    counts = store.count_suggestion_events()
    return SkillSuggestionStatus(
        surfaced_count=int(counts.get("surfaced_count", 0)),
        accepted_count=int(counts.get("accepted_count", 0)),
        rejected_count=int(counts.get("rejected_count", 0)),
        deferred_count=int(counts.get("deferred_count", 0)),
        auto_dismissed_count=int(counts.get("auto_dismissed_count", 0)),
        pending_count=int(counts.get("pending_count", 0)),
        last_surfaced_at=str(counts.get("last_surfaced_at", "")),
        last_outcome_at=str(counts.get("last_outcome_at", "")),
        auto_dismiss_reasons=dict(counts.get("auto_dismiss_reasons", {})),
    )


def _emit_surfaced(
    store: SkillStore,
    *,
    proposal_id: str,
    signature: str,
    surfaced_at: str,
) -> None:
    store.record_suggestion_event(
        event_id=str(uuid.uuid4()),
        proposal_id=proposal_id,
        signature=signature,
        event_type=SUGGESTION_EVENT_SURFACED,
        reason=None,
        outcome=None,
        surfaced_at=surfaced_at,
    )


def _emit_dismiss(
    store: SkillStore,
    *,
    proposal_id: str,
    signature: str,
    reason: str,
    surfaced_at: str,
) -> None:
    store.record_suggestion_event(
        event_id=str(uuid.uuid4()),
        proposal_id=proposal_id,
        signature=signature,
        event_type=SUGGESTION_EVENT_AUTO_DISMISSED,
        reason=reason,
        outcome=None,
        surfaced_at=surfaced_at,
    )


__all__ = (
    "DEFAULT_SUGGESTION_BATCH_CAP",
    "DEFAULT_SUGGESTION_COOLDOWN_SECONDS",
    "DEFAULT_SUGGESTION_MIN_AGE_SECONDS",
    "DISMISS_REASON_COOLDOWN_ACTIVE",
    "DISMISS_REASON_STRUCTURAL_DUPLICATE",
    "SUGGESTION_EVENT_AUTO_DISMISSED",
    "SUGGESTION_EVENT_OUTCOME_RECORDED",
    "SUGGESTION_EVENT_SURFACED",
    "SkillProposalSuggestion",
    "SkillSuggestionStatus",
    "SuggestionSurfacePass",
    "list_active_suggestions",
    "proposal_signature",
    "record_outcome",
    "run_suggestion_surface_pass",
    "suggestion_status",
)
