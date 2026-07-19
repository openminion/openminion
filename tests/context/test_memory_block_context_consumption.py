from __future__ import annotations

from datetime import datetime, timezone

from openminion.modules.context.schemas import (
    BuildPackRequest,
    ContextBudgets,
    IdentitySnippet,
    MemoryBlockSegmentRef,
    SessionSlice,
    bucket_caps_for,
)
from openminion.modules.context.service import ContextCtlService

from sophiagraph import SophiaGraphMemoryStore
from sophiagraph.models import MemoryBlock, MemoryNamespace


class _IdentityClient:
    contract_version = "v1"

    def render(
        self,
        *,
        agent_id: str,
        purpose: str,
        max_tokens: int,
        provider_pref=None,
    ) -> IdentitySnippet:
        del max_tokens, provider_pref
        return IdentitySnippet(
            agent_id=agent_id,
            profile_version="prof-v1",
            render_version="rend-v1",
            text=f"Agent: {agent_id}\nPurpose: {purpose}",
        )


class _SessionClient:
    contract_version = "v1"

    def __init__(self) -> None:
        self.events: list[dict] = []

    def get_slice(
        self,
        *,
        session_id: str,
        purpose: str,
        limits: dict[str, int],
    ) -> SessionSlice:
        del purpose, limits
        return SessionSlice(
            session_id=session_id,
            slice_version="slice-v1",
            last_event_id="evt-last",
            summary_short="summary short",
        )

    def append_event(self, session_id: str, event_type: str, payload: dict, **kwargs):
        self.events.append(
            {
                "session_id": session_id,
                "event_type": event_type,
                "payload": payload,
                "kwargs": kwargs,
            }
        )
        return "evt-manifest"

    def emit_canonical_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict,
        **kwargs,
    ):
        self.events.append(
            {
                "session_id": session_id,
                "event_type": event_type,
                "payload": payload,
                "kwargs": kwargs,
            }
        )
        return "evt-canonical"


class _MemoryClient:
    contract_version = "v1"

    def query_facts(self, **kwargs):
        del kwargs
        return []

    def query_memory_cards(self, **kwargs):
        del kwargs
        return []

    def recall_session_start_memory(self, **kwargs):
        del kwargs
        return []

    def recall_mid_session_memory(self, **kwargs):
        del kwargs
        return []

    def recall_recent_session_artifacts(self, **kwargs):
        del kwargs
        return []

    def get_procedure(self, *, procedure_id: str):
        del procedure_id
        return None


class _ArtifactClient:
    contract_version = "v1"

    def query_digests(self, **kwargs):
        del kwargs
        return []


def _namespace() -> MemoryNamespace:
    return MemoryNamespace(tenant_id="acme", agent_id="agent-1")


def _block(
    block_id: str,
    *,
    class_name: str = "agent_identity",
    mode: str = "read_only",
    content: str = "Always prefer durable evidence before changing code.",
    token_estimate: int = 18,
    stale_after: str | None = None,
) -> MemoryBlock:
    now = datetime(2026, 7, 18, tzinfo=timezone.utc).isoformat()
    return MemoryBlock(
        block_id=block_id,
        class_name=class_name,
        mode=mode,
        content=content,
        token_estimate=token_estimate,
        owner_namespace=_namespace(),
        source="operator_pin",
        created_at=now,
        last_updated_at=now,
        last_updated_by="operator",
        stale_after=stale_after,
    )


def _budgets(memory_tokens: int = 256) -> ContextBudgets:
    return ContextBudgets(
        total_max_tokens=2048,
        identity_tokens=120,
        summary_tokens=120,
        conversation_summary_tokens=80,
        active_plan_tokens=80,
        task_digest_tokens=60,
        recent_turn_tokens=220,
        facts_tokens=120,
        memory_tokens=memory_tokens,
        skills_tokens=80,
        artifact_tokens=120,
        instructions_tokens=120,
    )


def _service(
    store: SophiaGraphMemoryStore | None,
    *,
    enabled: bool = True,
) -> ContextCtlService:
    return ContextCtlService(
        identityctl=_IdentityClient(),
        sessctl=_SessionClient(),
        memctl=_MemoryClient(),
        artifactctl=_ArtifactClient(),
        memory_block_store=store,
        memory_blocks_enabled=enabled,
    )


def _request(memory_tokens: int = 256) -> BuildPackRequest:
    return BuildPackRequest(
        session_id="session-1",
        agent_id="agent-1",
        purpose="chat",
        query="What should you remember about this repo?",
        budgets_override=_budgets(memory_tokens=memory_tokens),
    )


def test_memory_block_ref_schema_serializes_without_openminion_block_model() -> None:
    ref = MemoryBlockSegmentRef(
        block_id="blk-1",
        class_name="agent_identity",
        mode="read_only",
        namespace_id="agent:agent-1",
        provenance_ref="memory-block:blk-1",
    )

    assert ref.model_dump()["block_id"] == "blk-1"
    import openminion.modules.context.schemas as context_schemas

    assert not hasattr(context_schemas, "MemoryBlock")


def test_bucket_caps_include_memory_bucket() -> None:
    caps = bucket_caps_for(_budgets(memory_tokens=333))

    assert caps["memory"] == 333


def test_eligible_blocks_enter_context_pack_and_trace() -> None:
    store = SophiaGraphMemoryStore()
    store.put_memory_block(
        _block(
            "blk-identity",
            content="This agent should cite exact validation evidence.",
        )
    )
    service = _service(store)

    pack = service.build_pack(_request())

    block_segment = next(
        segment
        for segment in pack.segments
        if segment.id == "memory-block:blk-identity"
    )
    assert block_segment.bucket == "memory"
    assert "cite exact validation evidence" in block_segment.content
    assert block_segment.metadata["memory_block"]["block_id"] == "blk-identity"
    assert pack.token_budget_report.buckets["memory"].selected_count == 1
    assert pack.context_manifest.decision_trace is not None
    assert "memory-block:blk-identity" in (
        pack.context_manifest.decision_trace.memory_block_refs
    )


def test_deferred_modes_stay_inactive() -> None:
    store = SophiaGraphMemoryStore()
    store.put_memory_block(_block("blk-shared", mode="shared"))
    store.put_memory_block(_block("blk-writable", mode="writable"))
    service = _service(store)

    pack = service.build_pack(_request())

    assert not any(segment.id.startswith("memory-block:") for segment in pack.segments)
    assert pack.token_budget_report.buckets["memory"].selected_count == 0
    assert pack.token_budget_report.buckets["memory"].dropped_count == 2


def test_stale_marker_is_preserved_from_sophiagraph_renderer() -> None:
    store = SophiaGraphMemoryStore()
    store.put_memory_block(
        _block(
            "blk-stale",
            content="Use the current CLI recovery command.",
            stale_after="2026-01-01T00:00:00+00:00",
        )
    )
    service = _service(store)

    pack = service.build_pack(_request())

    block_segment = next(
        segment for segment in pack.segments if segment.id == "memory-block:blk-stale"
    )
    assert block_segment.content.startswith("[stale]")
    assert block_segment.metadata["memory_block"]["stale"] is True


def test_budget_truncation_does_not_rewrite_runtime_prose() -> None:
    store = SophiaGraphMemoryStore()
    content = "alpha beta gamma delta epsilon zeta eta theta"
    store.put_memory_block(
        _block(
            "blk-large",
            class_name="active_mission",
            mode="pinned",
            content=content,
            token_estimate=400,
        )
    )
    service = _service(store)

    pack = service.build_pack(_request(memory_tokens=150))

    block_segment = next(
        segment for segment in pack.segments if segment.id == "memory-block:blk-large"
    )
    assert "alpha beta" in block_segment.content
    assert "memory block" not in block_segment.content.lower()
    assert pack.token_budget_report.buckets["memory"].trim_applied is True


def test_hard_floor_failure_degrades_without_crashing_turn() -> None:
    store = SophiaGraphMemoryStore()
    store.put_memory_block(
        _block(
            "blk-impossible-budget",
            class_name="active_mission",
            mode="pinned",
            content="alpha beta gamma delta epsilon zeta eta theta",
            token_estimate=400,
        )
    )
    service = _service(store)

    pack = service.build_pack(_request(memory_tokens=8))

    assert not any(
        segment.id == "memory-block:blk-impossible-budget" for segment in pack.segments
    )
    memory_bucket = pack.token_budget_report.buckets["memory"]
    assert memory_bucket.total_available == 1
    assert memory_bucket.dropped_count == 1
    assert memory_bucket.trim_applied is True


def test_disabled_feature_falls_back_to_retrieval_only() -> None:
    store = SophiaGraphMemoryStore()
    store.put_memory_block(_block("blk-disabled"))
    service = _service(store, enabled=False)

    pack = service.build_pack(_request())

    assert not any(segment.id.startswith("memory-block:") for segment in pack.segments)
    assert pack.token_budget_report.buckets["memory"].selected_count == 0
