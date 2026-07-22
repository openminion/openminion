from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any, Literal
from collections.abc import Callable, Sequence

from openminion.modules.memory.diagnostics.operability import parse_iso_utc
from openminion.modules.memory.runtime.consolidation.coordinator import (
    ConsolidationConfig,
    _WORKING_STATE_MODULE_STATE_ATTR,
)
from openminion.modules.memory.storage.base import CandidateListOptions
from openminion.modules.memory.runtime.consolidation.backend_access import (
    memory_backend,
)

ConsolidationEligibilityReason = Literal[
    "OK",
    "NO_RECENT_ROLLOUT",
    "IDLE_GATE_NOT_MET",
    "RATE_LIMIT_INSUFFICIENT",
    "ALREADY_CONSOLIDATED",
]


@dataclass(frozen=True)
class EligibilityResult:
    is_eligible: bool
    reason_code: ConsolidationEligibilityReason
    state_hash: str = ""
    candidate_ids: tuple[str, ...] = field(default_factory=tuple)
    last_activity_at: str | None = None


def _candidate_ids(candidates: Sequence[Any]) -> tuple[str, ...]:
    return tuple(
        str(getattr(item, "candidate_id", "") or "").strip()
        for item in candidates
        if str(getattr(item, "candidate_id", "") or "").strip()
    )


def _state_hash(candidate_ids: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for candidate_id in candidate_ids:
        digest.update(candidate_id.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _latest_activity(candidates: Sequence[Any]) -> str | None:
    timestamps = []
    for item in candidates:
        updated_at = str(getattr(item, "updated_at", "") or "").strip()
        created_at = str(getattr(item, "created_at", "") or "").strip()
        candidate = updated_at or created_at
        parsed = parse_iso_utc(candidate)
        if parsed is not None:
            timestamps.append((parsed, candidate))
    if not timestamps:
        return None
    timestamps.sort(key=lambda item: item[0], reverse=True)
    return timestamps[0][1]


class ConsolidationEligibilityChecker:
    def __init__(
        self,
        memory_api: Any | None = None,
        *,
        recent_rollout_probe: Callable[[str, str, int], Sequence[Any]] | None = None,
        rate_limit_remaining_percent_probe: Callable[[str, str], float | int | None]
        | None = None,
        working_state_probe: Callable[[str, str], Any | None] | None = None,
    ) -> None:
        self._memory_api = memory_api
        self._recent_rollout_probe = recent_rollout_probe
        self._rate_limit_remaining_percent_probe = rate_limit_remaining_percent_probe
        self._working_state_probe = working_state_probe

    def _recent_rollout_candidates(
        self,
        session_id: str,
        agent_id: str,
        recent_rollout_limit: int,
    ) -> Sequence[Any]:
        if self._recent_rollout_probe is not None:
            return self._recent_rollout_probe(
                session_id, agent_id, recent_rollout_limit
            )
        backend = memory_backend(self._memory_api)
        candidate_list = getattr(backend, "candidate_list", None)
        if not callable(candidate_list):
            return []
        proposed_scope = f"agent:{str(agent_id or '').strip()}"
        try:
            return list(
                candidate_list(
                    CandidateListOptions(
                        session_id=str(session_id or "").strip() or None,
                        proposed_scope=proposed_scope,
                        status="proposed",
                        limit=max(1, int(recent_rollout_limit)),
                    )
                )
            )
        except Exception:
            return []

    def _remaining_rate_limit_percent(self, session_id: str, agent_id: str) -> float:
        if self._rate_limit_remaining_percent_probe is None:
            return 100.0
        raw = self._rate_limit_remaining_percent_probe(session_id, agent_id)
        if raw is None:
            return 100.0
        return float(raw)

    def _maintenance_state(self, session_id: str, agent_id: str) -> dict[str, Any]:
        if self._working_state_probe is None:
            return {}
        state = self._working_state_probe(session_id, agent_id)
        module_state = getattr(state, _WORKING_STATE_MODULE_STATE_ATTR, None)
        if module_state is None and isinstance(state, dict):
            module_state = state.get(_WORKING_STATE_MODULE_STATE_ATTR)
        if not isinstance(module_state, dict):
            return {}
        maintenance = module_state.get("memory_context_maintenance", {})
        return dict(maintenance) if isinstance(maintenance, dict) else {}

    def is_eligible(
        self,
        session_id: str,
        agent_id: str,
        config: ConsolidationConfig,
        *,
        now: datetime | None = None,
    ) -> EligibilityResult:
        target_now = now or datetime.now(timezone.utc)
        candidates = list(
            self._recent_rollout_candidates(
                str(session_id or "").strip(),
                str(agent_id or "").strip(),
                int(config.recent_rollout_limit),
            )
        )
        candidate_ids = _candidate_ids(candidates)
        if not candidate_ids:
            return EligibilityResult(
                is_eligible=False,
                reason_code="NO_RECENT_ROLLOUT",
            )

        state_hash = _state_hash(candidate_ids)
        maintenance = self._maintenance_state(session_id, agent_id)
        if (
            str(maintenance.get("last_consolidation_state_hash", "") or "").strip()
            == state_hash
        ):
            return EligibilityResult(
                is_eligible=False,
                reason_code="ALREADY_CONSOLIDATED",
                state_hash=state_hash,
                candidate_ids=candidate_ids,
            )

        last_activity_at = _latest_activity(candidates)
        last_activity_dt = parse_iso_utc(last_activity_at)
        if last_activity_dt is None:
            return EligibilityResult(
                is_eligible=False,
                reason_code="NO_RECENT_ROLLOUT",
                state_hash=state_hash,
                candidate_ids=candidate_ids,
            )
        idle_seconds = (target_now - last_activity_dt).total_seconds()
        if idle_seconds < int(config.idle_seconds_before_eligible):
            return EligibilityResult(
                is_eligible=False,
                reason_code="IDLE_GATE_NOT_MET",
                state_hash=state_hash,
                candidate_ids=candidate_ids,
                last_activity_at=last_activity_at,
            )

        remaining_percent = self._remaining_rate_limit_percent(session_id, agent_id)
        if remaining_percent < float(config.min_rate_limit_remaining_percent):
            return EligibilityResult(
                is_eligible=False,
                reason_code="RATE_LIMIT_INSUFFICIENT",
                state_hash=state_hash,
                candidate_ids=candidate_ids,
                last_activity_at=last_activity_at,
            )

        return EligibilityResult(
            is_eligible=True,
            reason_code="OK",
            state_hash=state_hash,
            candidate_ids=candidate_ids,
            last_activity_at=last_activity_at,
        )


__all__ = [
    "ConsolidationEligibilityChecker",
    "ConsolidationEligibilityReason",
    "EligibilityResult",
]
