from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from openminion.modules.brain.schemas.state import BudgetCounters, WorkingState
from openminion.modules.llm.providers.factory import RuntimeLLMHandle
from openminion.modules.llm.schemas import LLMResponse
from openminion.modules.memory.config import ConsolidationConfig
from openminion.modules.memory.errors import PromotionDeniedError
from openminion.modules.memory.models import MemoryCandidate, MemoryRecord
from openminion.modules.memory.runtime.consolidation import run_consolidation_cycle
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore


@dataclass(frozen=True)
class TmcCloseoutArtifact:
    artifact_root: str
    checks: dict[str, bool]
    merge_model_name: str
    merge_model_override_used: bool
    promoted_record_ids: list[str]
    superseded_record_id: str
    superseded_valid_to: str | None
    blocked_errors: list[str]
    maintenance_marker: str
    maintenance_state_hash: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class _TrackingMergeClient:
    def __init__(self) -> None:
        self.models: list[str] = []

    def complete(self, messages, tools=None, **overrides):  # noqa: ANN001
        del messages, tools
        self.models.append(str(overrides.get("model") or ""))
        return LLMResponse(
            ok=True,
            provider="echo",
            model=str(overrides.get("model") or "echo"),
            output_text="consolidation review complete",
            memory_consolidation={
                "decisions": [
                    {
                        "candidate_id": "cand-promote",
                        "action": "promote",
                        "reasoning": "durable lesson",
                    },
                    {
                        "candidate_id": "cand-blocked",
                        "action": "promote",
                        "reasoning": "would promote but gate should block",
                    },
                ]
            },
            assistant_messages=[],
            tool_calls=[],
        )


class _TrackingMemoryService:
    def __init__(self, store: InMemoryMemoryStore) -> None:
        self._service = MemoryService(store=store)
        self._store = store
        self.promote_calls: list[tuple[str, str]] = []
        self.update_calls: list[str] = []
        self.supersede_calls: list[tuple[str, str, str]] = []

    def candidate_put(self, candidate: MemoryCandidate) -> str:
        return self._service.candidate_put(candidate)

    def candidate_get(self, candidate_id: str) -> MemoryCandidate:
        return self._service.candidate_get(candidate_id)

    def candidate_update(
        self, candidate_id: str, patch: dict[str, Any]
    ) -> MemoryCandidate:
        self.update_calls.append(candidate_id)
        return self._service.candidate_update(candidate_id, patch)

    def promote_candidate(self, candidate_id: str, target_scope: str) -> MemoryRecord:
        self.promote_calls.append((candidate_id, target_scope))
        if candidate_id == "cand-blocked":
            raise PromotionDeniedError("blocked by trust gate")
        return self._service.promote_candidate(candidate_id, target_scope)

    def supersede_by_contradiction(
        self, old_record_id: str, new_record_id: str, reason: str = ""
    ) -> MemoryRecord:
        self.supersede_calls.append((old_record_id, new_record_id, reason))
        return self._service.supersede_by_contradiction(
            old_record_id,
            new_record_id,
            reason=reason,
        )

    def find_record_by_normalized_key(
        self,
        *,
        scope: str,
        record_type: str,
        normalized_key: str,
    ) -> MemoryRecord | None:
        return self._service.find_record_by_normalized_key(
            scope=scope,
            record_type=record_type,
            normalized_key=normalized_key,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._service, name)


def _artifact_root(date_tag: str | None = None) -> Path:
    here = Path(__file__).resolve()
    repo_root = here.parents[4]
    tag = date_tag or datetime.now(timezone.utc).strftime("%Y%m%d")
    return repo_root / ".openminion" / "runtime" / f"tmc-{tag}-closeout"


def _state() -> WorkingState:
    return WorkingState(
        session_id="session-1",
        agent_id="agent-1",
        budgets_remaining=BudgetCounters(
            ticks=1,
            tool_calls=1,
            a2a_calls=0,
            tokens=0,
            time_ms=0,
        ),
        session_work_summary="preserve me",
    )


def _candidate(
    *,
    candidate_id: str,
    created_at: str,
    text: str,
    polarity: str,
) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=candidate_id,
        session_id="session-1",
        proposed_scope="agent:agent-1",
        type="fact",
        title=f"title-{candidate_id}",
        content={"text": text},
        source="validated",
        confidence=0.8,
        claim_key="fact:deploy-region",
        polarity=polarity,  # type: ignore[arg-type]
        meta={"claim_key": "fact:deploy-region", "polarity": polarity},
        created_at=created_at,
        updated_at=created_at,
    )


def run_closeout_smoke(
    *,
    artifact_root: Path | None = None,
) -> TmcCloseoutArtifact:
    now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(hours=7)).isoformat()
    root = artifact_root or _artifact_root()
    store = InMemoryMemoryStore()
    service = _TrackingMemoryService(store)
    state = _state()

    store.put(
        MemoryRecord(
            id="old-1",
            scope="agent:agent-1",
            type="fact",
            title="old-region",
            content={"text": "deploy in us-east-1"},
            source="validated",
            confidence=0.9,
            meta={"claim_key": "fact:deploy-region", "polarity": "asserts"},
            created_at="2026-05-01T00:00:00+00:00",
            updated_at="2026-05-01T00:00:00+00:00",
        )
    )
    service.candidate_put(
        _candidate(
            candidate_id="cand-promote",
            created_at=old,
            text="new deploy region sk-SECRETSECRETSECRET1234 should redact",
            polarity="negates",
        )
    )
    service.candidate_put(
        _candidate(
            candidate_id="cand-blocked",
            created_at=old,
            text="secondary candidate for blocked promotion",
            polarity="negates",
        )
    )

    merge_client = _TrackingMergeClient()
    handle = RuntimeLLMHandle(name="openai", model="gpt-4.2", client=merge_client)
    config = ConsolidationConfig(consolidation_model="gpt-4.2-mini")

    result = run_consolidation_cycle(
        service,
        working_state=state,
        primary_model_handle=handle,
        config=config,
        target_scope="agent:agent-1",
        turn_id="turn-1",
        now=now,
        rate_limit_remaining_percent_probe=lambda _session_id, _agent_id: 80,
    )

    maintenance = state.module_state["memory_context_maintenance"]
    old_after = store.get("old-1")
    redacted = any(
        "[REDACTED_SECRET]" in str(item.get("content_preview", "") or "")
        for item in (result.payload.candidate_refs if result.payload else [])
    )
    promoted_ids = list(result.write_result.get("promoted_record_ids", []))
    checks = {
        "extraction_redacted_payload": redacted,
        "merge_selected_consolidation_model": (
            bool(result.merge_decisions)
            and result.merge_decisions.model_name == "gpt-4.2-mini"
            and merge_client.models == ["gpt-4.2-mini"]
        ),
        "durable_writes_via_memory_service": (
            bool(service.update_calls)
            and bool(service.promote_calls)
            and bool(promoted_ids)
        ),
        "blocked_promotion_observed": bool(result.write_result.get("errors", [])),
        "supersession_set_valid_to": bool(
            old_after is not None and old_after.valid_to and old_after.superseded_by_id
        ),
        "maintenance_marker_updated": bool(
            maintenance.get("last_consolidation_marker")
            and maintenance.get("last_consolidation_state_hash")
        ),
    }
    return TmcCloseoutArtifact(
        artifact_root=str(root),
        checks=checks,
        merge_model_name=str(
            result.merge_decisions.model_name if result.merge_decisions else ""
        ),
        merge_model_override_used=merge_client.models == ["gpt-4.2-mini"],
        promoted_record_ids=promoted_ids,
        superseded_record_id=str(getattr(old_after, "superseded_by_id", "") or ""),
        superseded_valid_to=getattr(old_after, "valid_to", None),
        blocked_errors=list(result.write_result.get("errors", [])),
        maintenance_marker=str(maintenance.get("last_consolidation_marker", "") or ""),
        maintenance_state_hash=str(
            maintenance.get("last_consolidation_state_hash", "") or ""
        ),
    )


def write_closeout_artifact(
    artifact: TmcCloseoutArtifact,
    artifact_root: Path,
) -> Path:
    artifact_root.mkdir(parents=True, exist_ok=True)
    out_path = artifact_root / "summary.json"
    out_path.write_text(
        json.dumps(artifact.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return out_path
