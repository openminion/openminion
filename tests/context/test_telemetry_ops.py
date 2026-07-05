import unittest

from openminion.modules.context.schemas import (
    ArtifactDigest,
    BucketAllocation,
    BuildPackRequest,
    ContextManifest,
    ContextPack,
    ContextBudgets,
    IdentitySnippet,
    IdentityManifest,
    SessionManifest,
    SessionSlice,
    SessionTurn,
    TokenBudgetReport,
)
from openminion.modules.context.service import ContextCtlService
from openminion.modules.context.telemetry import emit_pack_manifest_event


class _TelemetryStub:
    def __init__(self) -> None:
        self.operations: list[dict] = []
        self.counters: list[dict] = []

    async def emit_module_operation(
        self,
        session_id: str,
        turn_id: str,
        module_id: str,
        operation: str,
        *,
        count: int = 1,
        status: str = "ok",
        latency_ms: float = 0.0,
        extra: dict | None = None,
    ) -> None:
        self.operations.append(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "module_id": module_id,
                "operation": operation,
                "count": count,
                "status": status,
                "latency_ms": latency_ms,
                "extra": extra or {},
            }
        )

    async def emit_module_counter(
        self,
        session_id: str,
        turn_id: str,
        module_id: str,
        counter_name: str,
        value: float,
        *,
        status: str = "ok",
        extra: dict | None = None,
    ) -> None:
        self.counters.append(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "module_id": module_id,
                "counter_name": counter_name,
                "value": value,
                "status": status,
                "extra": extra or {},
            }
        )


class _ExplodingTelemetry:
    async def emit_module_operation(self, *args, **kwargs) -> None:
        raise ValueError("invalid operation payload")

    async def emit_module_counter(self, *args, **kwargs) -> None:
        raise ValueError("invalid counter payload")


class _CanonicalEventSessionStub:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit_canonical_event(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: dict,
        actor_type: str,
        actor_id: str,
    ) -> None:
        self.events.append(
            {
                "session_id": session_id,
                "event_type": event_type,
                "payload": payload,
                "actor_type": actor_type,
                "actor_id": actor_id,
            }
        )


class _IdentityClient:
    contract_version = "v1"

    def __init__(self, text: str) -> None:
        self._text = text

    def render(
        self, *, agent_id: str, purpose: str, max_tokens: int, provider_pref=None
    ) -> IdentitySnippet:
        return IdentitySnippet(
            agent_id=agent_id,
            profile_version="prof:v1",
            render_version="rend:v1",
            text=self._text,
        )


class _SessionClient:
    contract_version = "v1"

    def get_slice(self, *, session_id: str, purpose: str, limits: dict) -> SessionSlice:
        return SessionSlice(
            session_id=session_id,
            slice_version="slice:v1",
            last_event_id="evt-1",
            summary_short="summary",
            recent_turns=[
                SessionTurn(turn_id="turn-1", role="user", content="hello"),
            ],
        )


class _MemoryClient:
    contract_version = "v1"

    def query_facts(self, *, session_id, agent_id, query, limit, mode_name=None):
        del session_id, agent_id, query, limit, mode_name
        return []

    def query_memory_cards(self, *, session_id, agent_id, query, limit, mode_name=None):
        del session_id, agent_id, query, limit, mode_name
        return []

    def recall_session_start_memory(
        self, *, session_id, agent_id, query, turn_index, limit, mode_name=None
    ):
        del session_id, agent_id, query, turn_index, limit, mode_name
        return []

    def recall_mid_session_memory(self, **kwargs):
        del kwargs
        return []

    def recall_recent_session_artifacts(self, **kwargs):
        del kwargs
        return []

    def get_procedure(self, *, procedure_id):
        return None


class _ArtifactClient:
    contract_version = "v1"

    def __init__(self, digests: list[ArtifactDigest]) -> None:
        self._digests = digests

    def query_digests(self, *, session_id, agent_id, query, limit):
        return self._digests[:limit]


def _make_service(*, telemetry) -> ContextCtlService:
    long_identity = "I" * 400
    artifacts = [ArtifactDigest(ref=f"art-{i}", bullets=["x" * 200]) for i in range(12)]
    return ContextCtlService(
        identityctl=_IdentityClient(long_identity),
        sessctl=_SessionClient(),
        memctl=_MemoryClient(),
        artifactctl=_ArtifactClient(artifacts),
        telemetryctl=telemetry,
        rolling_enabled=True,
        compaction_enabled=False,
        compression_enabled=False,
    )


def _tight_budget() -> ContextBudgets:
    return ContextBudgets(
        total_max_tokens=120,
        identity_tokens=5,
        summary_tokens=20,
        recent_turn_tokens=20,
        facts_tokens=20,
        memory_tokens=20,
        skills_tokens=10,
        artifact_tokens=20,
        instructions_tokens=10,
    )


class ContextTelemetryOpsTests(unittest.TestCase):
    def test_pack_emits_module_ops_and_counters(self) -> None:
        telemetry = _TelemetryStub()
        service = _make_service(telemetry=telemetry)
        pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-telemetry",
                agent_id="agent-telemetry",
                purpose="act",
                query="hello",
                budgets_override=_tight_budget(),
            )
        )
        self.assertTrue(pack.messages)

        ops = {item["operation"]: item for item in telemetry.operations}
        self.assertIn("pack_build", ops)

        drop_count = len(pack.context_manifest.dropped_segment_ids)
        counter_map = {item["counter_name"]: item for item in telemetry.counters}
        self.assertIn("dropped_segments", counter_map)
        self.assertEqual(counter_map["dropped_segments"]["value"], float(drop_count))

        truncated_value = counter_map["truncated_segments"]["value"]
        self.assertGreater(truncated_value, 0)

        if drop_count > 0:
            self.assertIn("drop", ops)
            self.assertEqual(ops["drop"]["count"], drop_count)
        self.assertIn("truncate", ops)
        self.assertEqual(ops["truncate"]["count"], int(truncated_value))

    def test_invalid_telemetry_payloads_do_not_break_pack(self) -> None:
        service = _make_service(telemetry=_ExplodingTelemetry())
        pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-telemetry-error",
                agent_id="agent-telemetry",
                purpose="act",
                query="hello",
                budgets_override=_tight_budget(),
            )
        )
        self.assertTrue(pack.context_manifest)

    def test_pack_manifest_event_includes_token_budget_buckets(self) -> None:
        sessctl = _CanonicalEventSessionStub()
        pack = ContextPack(
            session_id="sess-context-buckets",
            agent_id="agent-context",
            purpose="act",
            profile_version="prof:v1",
            render_version="rend:v1",
            slice_version="slice:v1",
            pack_version="pack:v1",
            pack_hash="hash:v1",
            context_manifest=ContextManifest(
                identity=IdentityManifest(
                    agent_id="agent-context",
                    profile_version="prof:v1",
                    render_version="rend:v1",
                ),
                session=SessionManifest(
                    session_id="sess-context-buckets",
                    slice_version="slice:v1",
                ),
                llm_call_id="call-context",
                pack_policy_used="position_aware_v1",
            ),
            token_budget_report=TokenBudgetReport(
                total_cap_tokens=200,
                total_used_tokens=100,
                buckets={
                    "recent_window": BucketAllocation(
                        bucket="recent_window",
                        cap_tokens=80,
                        used_tokens=40,
                        selected_count=2,
                        total_available=3,
                        dropped_count=1,
                        trim_applied=True,
                    ),
                    "retrieval": BucketAllocation(
                        bucket="retrieval",
                        cap_tokens=120,
                        used_tokens=60,
                        selected_count=1,
                        total_available=1,
                        dropped_count=0,
                    ),
                },
            ),
        )

        emit_pack_manifest_event(
            sessctl=sessctl,
            session_id="sess-context-buckets",
            agent_id="agent-context",
            pack=pack,
            cache_hit=False,
        )

        payload = sessctl.events[0]["payload"]
        self.assertEqual(payload["total_used_tokens"], 100)
        self.assertEqual(
            payload["token_budget_buckets"]["recent_window"],
            {
                "bucket": "recent_window",
                "cap_tokens": 80,
                "used_tokens": 40,
                "selected_count": 2,
                "total_available": 3,
                "dropped_count": 1,
                "trim_applied": True,
            },
        )
        self.assertEqual(
            payload["token_budget_buckets"]["retrieval"]["used_tokens"], 60
        )
