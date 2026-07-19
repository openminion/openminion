from __future__ import annotations

from openminion.modules.context.pack.finalize import build_context_decision_trace
from openminion.modules.context.schemas import (
    BucketAllocation,
    BuildPackRequest,
    ContextDecisionRef,
    ContextDecisionTraceV1,
    ContextManifest,
    ContextPack,
    ContextSegment,
    IdentityManifest,
    PackingDecisionLog,
    SessionManifest,
    TokenBudgetReport,
    TrimAction,
)
from openminion.modules.context.telemetry import emit_pack_manifest_event
from openminion.modules.context.telemetry import ContextTelemetryBridge
from openminion.modules.context.service import ContextCtlService
from openminion.modules.session.runtime.session_client import SessctlSessionClient
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.telemetry.events.catalog import (
    CONTEXT_MANIFEST_CREATED,
    CONTEXT_MANIFEST_PERSISTENCE_FAILED,
    EVENT_TYPES,
)


def _budget_report(decision_log: PackingDecisionLog | None = None) -> TokenBudgetReport:
    return TokenBudgetReport(
        total_cap_tokens=100,
        total_used_tokens=40,
        buckets={
            "retrieval": BucketAllocation(
                bucket="retrieval",
                cap_tokens=80,
                used_tokens=30,
                selected_count=1,
                total_available=2,
                dropped_count=1,
                trim_applied=True,
            )
        },
        decision_log=decision_log,
    )


def _make_context_pack() -> ContextPack:
    decision_log = PackingDecisionLog()
    trace = build_context_decision_trace(
        session_id="sess-1",
        turn_id="turn-1",
        llm_call_id="call-1",
        prompt_context_id="prompt-1",
        pack_version="pack-1",
        segments=[
            ContextSegment(
                id="retrieval:kept",
                bucket="retrieval",
                content="retrieved text",
                token_estimate=9,
                refs=["mem-1"],
            )
        ],
        decision_log=decision_log,
        token_budget_report=_budget_report(decision_log),
    )
    return ContextPack(
        session_id="sess-1",
        agent_id="agent-1",
        purpose="act",
        profile_version="profile-1",
        render_version="render-1",
        slice_version="slice-1",
        pack_version="pack-1",
        pack_hash="pack-1",
        context_manifest=ContextManifest(
            identity=IdentityManifest(
                agent_id="agent-1",
                profile_version="profile-1",
                render_version="render-1",
            ),
            session=SessionManifest(slice_version="slice-1"),
            included_segment_ids=["retrieval:kept"],
            llm_call_id="call-1",
            prompt_context_id="prompt-1",
            decision_trace=trace,
        ),
        token_budget_report=_budget_report(decision_log),
        prompt_context_id="prompt-1",
    )


def test_context_decision_trace_represents_structural_decisions_without_content() -> (
    None
):
    decision_log = PackingDecisionLog(
        actions=[
            TrimAction(
                action="drop_segment",
                reason_code="over_budget",
                segment_ids=["retrieval:dropped"],
                bucket="retrieval",
                tokens_saved=12,
            )
        ],
        invariants_preserved=["identity"],
    )
    trace = build_context_decision_trace(
        session_id="sess-1",
        turn_id="turn-1",
        llm_call_id="call-1",
        prompt_context_id="prompt-1",
        pack_version="pack-1",
        segments=[
            ContextSegment(
                id="identity",
                bucket="static_prefix",
                content="DO NOT PERSIST RAW CONTENT",
                token_estimate=7,
                pinned=True,
            ),
            ContextSegment(
                id="retrieval:kept",
                bucket="retrieval",
                content="retrieved text",
                token_estimate=9,
                refs=["mem-1"],
            ),
        ],
        decision_log=decision_log,
        token_budget_report=_budget_report(decision_log),
    )

    payload = trace.model_dump(mode="json")
    assert trace.trace_version == "context-decision.v1"
    assert {decision.action for decision in trace.decisions} == {
        "pinned",
        "included",
        "drop_segment",
    }
    assert "mem-1" in trace.retrieval_score_refs
    assert "DO NOT PERSIST RAW CONTENT" not in str(payload)
    assert "retrieved text" not in str(payload)


def test_context_decision_trace_truncates_with_ordered_digest() -> None:
    trace = ContextDecisionTraceV1(
        session_id="sess-1",
        pack_version="pack-1",
        decisions=[
            ContextDecisionRef(
                segment_id=f"seg-{index}",
                bucket="retrieval",
                action="included",
                reason_code="selected",
                token_estimate=1,
            )
            for index in range(520)
        ],
    ).bounded()

    assert len(trace.decisions) <= 512
    assert trace.truncated is True
    assert trace.omitted_decision_count >= 8
    assert trace.omitted_decision_digest


class _CanonicalSession:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit_canonical_event(self, **kwargs):
        self.events.append(dict(kwargs))
        return "evt-canonical"


class _FallbackSession:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit_canonical_event(self, **kwargs):
        raise RuntimeError("canonical unavailable")

    def append_event(self, **kwargs):
        self.events.append(dict(kwargs))
        return "evt-fallback"


class _NoSinkSession:
    pass


class _TelemetryFailureCollector:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit_canonical_event(
        self,
        session_id,
        turn_id,
        event_type,
        payload,
        **kwargs,
    ) -> None:
        self.events.append(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "event_type": event_type,
                "payload": payload,
                "kwargs": kwargs,
            }
        )


class _Logger:
    def debug(self, *args, **kwargs) -> None:
        del args, kwargs

    def warning(self, *args, **kwargs) -> None:
        del args, kwargs


class _SessionAdapter:
    contract_version = "v1"

    def __init__(self, store: SQLiteSessionStore) -> None:
        self._store = store
        self._client = SessctlSessionClient(store)

    def get_slice(self, **kwargs):
        return self._client.get_slice(**kwargs)

    def append_event(self, *args, **kwargs):
        return self._store.append_event(*args, **kwargs)

    def emit_canonical_event(self, **kwargs):
        return self._store.emit_canonical_event(**kwargs)

    def bind_agent(self, *args, **kwargs):
        return self._store.bind_agent(*args, **kwargs)

    def append_llm_request_started(self, *args, **kwargs):
        return self._store.append_llm_request_started(*args, **kwargs)

    def list_events(self, *args, **kwargs):
        return self._store.list_events(*args, **kwargs)

    def get_session(self, *args, **kwargs):
        return self._store.get_session(*args, **kwargs)


class _IdentityClient:
    contract_version = "v1"

    def render(
        self,
        *,
        agent_id,
        purpose,
        max_tokens,
        provider_pref=None,
        query_text=None,
    ):
        from openminion.modules.context.schemas import IdentitySnippet

        del max_tokens, provider_pref, query_text
        return IdentitySnippet(
            agent_id=agent_id,
            purpose=purpose,
            profile_version="profile-1",
            render_version="render-1",
            text=f"Agent {agent_id} for {purpose}",
        )


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

    def get_procedure(self, **kwargs):
        del kwargs
        return None


class _ArtifactClient:
    contract_version = "v1"

    def query_digests(self, **kwargs):
        del kwargs
        return []


class _TelemetryClient:
    contract_version = "v1"

    async def emit_module_operation(self, *args, **kwargs):
        del args, kwargs

    async def emit_module_counter(self, *args, **kwargs):
        del args, kwargs

    async def emit_canonical_event(self, *args, **kwargs):
        del args, kwargs


def test_manifest_event_returns_canonical_persistence_result() -> None:
    pack = _make_context_pack()
    sessctl = _CanonicalSession()

    result = emit_pack_manifest_event(
        sessctl=sessctl,
        session_id="sess-1",
        agent_id="agent-1",
        pack=pack,
        cache_hit=False,
    )

    assert result.persisted is True
    assert result.event_id == "evt-canonical"
    assert result.reason_code == "persisted_canonical"
    event = sessctl.events[0]
    assert event["event_type"] == CONTEXT_MANIFEST_CREATED
    assert event["payload"]["decision_trace"]["persistence_status"] == "persisted"


def test_manifest_event_falls_back_and_returns_event_id() -> None:
    pack = _make_context_pack()
    sessctl = _FallbackSession()

    result = emit_pack_manifest_event(
        sessctl=sessctl,
        session_id="sess-1",
        agent_id="agent-1",
        pack=pack,
        cache_hit=False,
    )

    assert result.persisted is True
    assert result.event_id == "evt-fallback"
    assert result.reason_code == "persisted_fallback"
    assert sessctl.events[0]["type"] == CONTEXT_MANIFEST_CREATED


def test_manifest_event_reports_no_sink_without_fabricating_event() -> None:
    result = emit_pack_manifest_event(
        sessctl=_NoSinkSession(),
        session_id="sess-1",
        agent_id="agent-1",
        pack=_make_context_pack(),
        cache_hit=False,
    )

    assert result.persisted is False
    assert result.reason_code == "no_persistence_sink"


def test_failure_event_type_is_catalog_registered() -> None:
    assert CONTEXT_MANIFEST_PERSISTENCE_FAILED in EVENT_TYPES


def test_bridge_marks_trace_degraded_and_emits_failure_telemetry() -> None:
    telemetry = _TelemetryFailureCollector()
    pack = _make_context_pack()
    bridge = ContextTelemetryBridge(
        sessctl=_NoSinkSession(),
        telemetryctl=telemetry,
        logger=_Logger(),
        module_id="openminion-context",
    )

    result = bridge.emit_pack_manifest_event(
        session_id="sess-1",
        agent_id="agent-1",
        pack=pack,
        cache_hit=False,
        llm_call_id="turn-1",
    )

    assert result.persisted is False
    assert pack.context_manifest is not None
    assert pack.context_manifest.decision_trace is not None
    assert pack.context_manifest.decision_trace.persistence_status == "degraded"
    assert telemetry.events[0]["event_type"] == CONTEXT_MANIFEST_PERSISTENCE_FAILED
    assert telemetry.events[0]["payload"]["reason_code"] == "no_persistence_sink"


def test_sqlite_backed_context_pack_persists_decision_trace(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "context-trace.db")
    session_id = store.create_session(
        initial_agent_id="agent-1",
        profile_version="profile-1",
    )
    store.append_turn(session_id, "user", "hello")
    service = ContextCtlService(
        identityctl=_IdentityClient(),
        sessctl=_SessionAdapter(store),
        memctl=_MemoryClient(),
        artifactctl=_ArtifactClient(),
        telemetryctl=_TelemetryClient(),
        rolling_enabled=True,
        compaction_enabled=False,
        compression_enabled=False,
    )

    pack = service.build_pack(
        BuildPackRequest(
            session_id=session_id,
            agent_id="agent-1",
            purpose="act",
            query="hello",
            llm_call_id="turn-1",
        )
    )

    assert pack.context_manifest is not None
    assert pack.context_manifest.decision_trace is not None
    assert pack.context_manifest.decision_trace.persistence_status == "persisted"
    events = store.list_events(session_id, event_type=CONTEXT_MANIFEST_CREATED)
    assert len(events) == 1
    persisted_trace = events[0]["payload"]["decision_trace"]
    assert persisted_trace["trace_version"] == "context-decision.v1"
    assert persisted_trace["persistence_status"] == "persisted"
    assert persisted_trace["decisions"]
    store.close()
