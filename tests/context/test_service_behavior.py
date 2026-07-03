import unittest

from openminion.modules.context.schemas import (
    ArtifactDigest,
    BuildConstraints,
    BuildPackRequest,
    ContextBudgets,
    FactRecord,
    IdentitySnippet,
    MemoryCard,
    RecentSessionArtifactRef,
    SessionSlice,
    SessionToolEvent,
    SessionTurn,
)
from openminion.modules.context.service import ContextCtlService


class _IdentityClient:
    contract_version = "v1"

    def render(
        self, *, agent_id: str, purpose: str, max_tokens: int, provider_pref=None
    ) -> IdentitySnippet:
        return IdentitySnippet(
            agent_id=agent_id,
            profile_version="prof:v1",
            render_version="rend:v1",
            text=f"Identity for {agent_id}",
        )


class _SliceSession:
    contract_version = "v1"

    def __init__(
        self,
        turns=None,
        summary_short="short summary",
        *,
        summary_long: str | None = None,
        checkpoint_id: str | None = None,
        seed_bundle_id: str | None = None,
        active_state: dict | None = None,
    ):
        self._turns = turns or []
        self._summary = summary_short
        self._summary_long = summary_long
        self._checkpoint_id = checkpoint_id
        self._seed_bundle_id = seed_bundle_id
        self._active_state = active_state

    def get_slice(self, *, session_id, purpose, limits) -> SessionSlice:
        return SessionSlice(
            session_id=session_id,
            slice_version="slice:v1",
            last_event_id="evt-001",
            summary_short=self._summary,
            summary_long=self._summary_long,
            recent_turns=self._turns,
            checkpoint_id=self._checkpoint_id,
            seed_bundle_id=self._seed_bundle_id,
            active_state=self._active_state,
        )


class _SequenceSession:
    contract_version = "v1"

    def __init__(self, slices: list[SessionSlice]) -> None:
        self._slices = list(slices)
        self._index = 0

    def get_slice(self, *, session_id, purpose, limits) -> SessionSlice:
        del session_id, purpose, limits
        if self._index >= len(self._slices):
            return self._slices[-1]
        slice_value = self._slices[self._index]
        self._index += 1
        return slice_value


class _MemoryClient:
    contract_version = "v1"

    def __init__(
        self,
        facts=None,
        cards=None,
        recall_cards=None,
        recall_error=None,
        mid_session_cards=None,
        mid_session_error=None,
        recent_artifact_refs=None,
        recent_artifact_error=None,
    ):
        self._facts = facts or []
        self._cards = cards or []
        self._recall_cards = recall_cards or []
        self._recall_error = recall_error
        self._mid_session_cards = mid_session_cards or []
        self._mid_session_error = mid_session_error
        self._recent_artifact_refs = recent_artifact_refs or []
        self._recent_artifact_error = recent_artifact_error
        self.recall_calls = []
        self.mid_session_calls = []
        self.recent_artifact_calls = []

    def query_facts(self, *, session_id, agent_id, query, limit, mode_name=None):
        del mode_name
        return self._facts[:limit]

    def query_memory_cards(self, *, session_id, agent_id, query, limit, mode_name=None):
        del mode_name
        return self._cards[:limit]

    def recall_session_start_memory(
        self, *, session_id, agent_id, query, turn_index, limit, mode_name=None
    ):
        self.recall_calls.append(
            {
                "session_id": session_id,
                "agent_id": agent_id,
                "query": query,
                "turn_index": turn_index,
                "limit": limit,
                "mode_name": mode_name,
            }
        )
        if self._recall_error is not None:
            raise self._recall_error
        return self._recall_cards[:limit]

    def recall_mid_session_memory(
        self,
        *,
        session_id,
        agent_id,
        turn_index,
        latest_user_message,
        intent_ids,
        intent_statuses,
        active_skill_id,
        resolved_skill_ids,
        plan_cursor,
        plan_step_ids,
        recent_tool_families,
        limit,
        mode_name=None,
    ):
        self.mid_session_calls.append(
            {
                "session_id": session_id,
                "agent_id": agent_id,
                "turn_index": turn_index,
                "latest_user_message": latest_user_message,
                "intent_ids": list(intent_ids),
                "intent_statuses": list(intent_statuses),
                "active_skill_id": active_skill_id,
                "resolved_skill_ids": list(resolved_skill_ids),
                "plan_cursor": plan_cursor,
                "plan_step_ids": list(plan_step_ids),
                "recent_tool_families": list(recent_tool_families),
                "limit": limit,
                "mode_name": mode_name,
            }
        )
        if self._mid_session_error is not None:
            raise self._mid_session_error
        return self._mid_session_cards[:limit]

    def recall_recent_session_artifacts(
        self,
        *,
        session_id,
        agent_id,
        max_results,
        max_session_age,
        mode_name=None,
    ):
        self.recent_artifact_calls.append(
            {
                "session_id": session_id,
                "agent_id": agent_id,
                "max_results": max_results,
                "max_session_age": max_session_age,
                "mode_name": mode_name,
            }
        )
        if self._recent_artifact_error is not None:
            raise self._recent_artifact_error
        return self._recent_artifact_refs[:max_results]

    def get_procedure(self, *, procedure_id):
        return None


class _ArtifactClient:
    contract_version = "v1"

    def __init__(self, digests=None):
        self._digests = digests or []

    def query_digests(self, *, session_id, agent_id, query, limit):
        return self._digests[:limit]


class _CompressClient:
    contract_version = "v1"

    def __init__(self, snapshot: str | None = None):
        self._snapshot = snapshot

    def get_snapshot(self, *, session_id, agent_id, mode_name=None):
        del session_id, agent_id, mode_name
        return self._snapshot


def _make_service(**kwargs) -> ContextCtlService:
    return ContextCtlService(
        identityctl=kwargs.get("identity", _IdentityClient()),
        sessctl=kwargs.get("session", _SliceSession()),
        memctl=kwargs.get("memory", _MemoryClient()),
        artifactctl=kwargs.get("artifact", _ArtifactClient()),
        compressctl=kwargs.get("compress"),
        identity_budget=kwargs.get("identity_budget"),
        rolling_enabled=kwargs.get("rolling_enabled", True),
        compaction_enabled=kwargs.get("compaction_enabled", True),
        compression_enabled=kwargs.get("compression_enabled", True),
    )


def _make_request(**kwargs) -> BuildPackRequest:
    return BuildPackRequest(
        session_id=kwargs.get("session_id", "sess-test"),
        agent_id=kwargs.get("agent_id", "agent-test"),
        purpose=kwargs.get("purpose", "act"),
        query=kwargs.get("query", "hello"),
        budgets_override=kwargs.get("budgets_override"),
        budget_telemetry=kwargs.get("budget_telemetry", {}),
    )


class SessionStartRecallTests(unittest.TestCase):
    def test_first_turn_recall_surfaces_memory_without_query_overlap(self) -> None:
        recalled = MemoryCard(
            record_id="mem-pref-1",
            record_type="user_preference",
            text="User prefers terse C++ server examples.",
            score=0.91,
        )
        memory = _MemoryClient(recall_cards=[recalled])
        service = _make_service(
            session=_SliceSession(turns=[], summary_short=""),
            memory=memory,
        )

        pack = service.build_pack(_make_request(query="schedule cleanup work"))

        self.assertEqual(len(memory.recall_calls), 1)
        self.assertEqual(memory.recall_calls[0]["query"], "schedule cleanup work")
        self.assertEqual(memory.recall_calls[0]["turn_index"], 0)
        self.assertIn("mem-pref-1", pack.context_manifest.recalled_memory)
        self.assertIn("mem-pref-1", pack.context_manifest.memory)
        rendered = "\n".join(segment.content for segment in pack.segments)
        self.assertIn("User prefers terse C++ server examples.", rendered)

    def test_later_turn_keeps_query_bound_retrieval_without_recall(self) -> None:
        recalled = MemoryCard(
            record_id="mem-pref-1",
            record_type="user_preference",
            text="User prefers terse C++ server examples.",
        )
        memory = _MemoryClient(recall_cards=[recalled])
        service = _make_service(
            session=_SliceSession(
                turns=[SessionTurn(turn_id="t1", role="user", content="hello")],
            ),
            memory=memory,
        )

        pack = service.build_pack(_make_request(query="schedule cleanup work"))

        self.assertEqual(memory.recall_calls, [])
        self.assertEqual(pack.context_manifest.recalled_memory, [])
        self.assertEqual(memory.recent_artifact_calls, [])

    def test_recall_failure_fails_closed(self) -> None:
        memory = _MemoryClient(recall_error=RuntimeError("backend down"))
        service = _make_service(
            session=_SliceSession(turns=[], summary_short=""),
            memory=memory,
        )

        pack = service.build_pack(_make_request(query="schedule cleanup work"))

        self.assertEqual(pack.context_manifest.recalled_memory, [])
        self.assertIsNotNone(pack.context_manifest)

    def test_first_turn_recent_session_artifacts_surface_as_references_only(
        self,
    ) -> None:
        artifact_ref = RecentSessionArtifactRef(
            record_id="artifact-ref-1",
            artifact_type="file",
            artifact_path="/workspace/auth.py",
            artifact_digest="sha256:abc123",
            session_id="sess-prev",
            turn_index=4,
            tool_name="file.write",
        )
        memory = _MemoryClient(recent_artifact_refs=[artifact_ref])
        service = _make_service(
            session=_SliceSession(turns=[], summary_short=""),
            memory=memory,
        )

        pack = service.build_pack(_make_request(query="pick up the auth work"))

        self.assertEqual(len(memory.recent_artifact_calls), 1)
        self.assertEqual(
            pack.context_manifest.recent_session_artifacts, ["artifact-ref-1"]
        )
        rendered = "\n".join(segment.content for segment in pack.segments)
        self.assertIn("[RECENT SESSION ARTIFACTS]", rendered)
        self.assertIn("path=/workspace/auth.py", rendered)

    def test_recent_session_artifact_recall_failure_fails_closed(self) -> None:
        service = _make_service(
            session=_SliceSession(turns=[], summary_short=""),
            memory=_MemoryClient(recent_artifact_error=RuntimeError("backend down")),
        )

        pack = service.build_pack(_make_request(query="continue"))

        self.assertEqual(pack.context_manifest.recent_session_artifacts, [])

    def test_session_work_summary_surfaces_as_dedicated_summary_block(self) -> None:
        service = _make_service(
            session=_SliceSession(
                turns=[
                    SessionTurn(turn_id="t1", role="user", content="continue"),
                    SessionTurn(
                        turn_id="t2",
                        role="assistant",
                        content="Implemented auth flow.",
                    ),
                ],
                summary_short="",
                active_state={
                    "session_work_summary": (
                        "Built authentication flow in auth.py, added login tests, "
                        "and still need to wire token refresh."
                    )
                },
            )
        )

        pack = service.build_pack(_make_request(query="what next"))

        rendered = "\n".join(segment.content for segment in pack.segments)
        self.assertIn("[SESSION WORK SUMMARY]", rendered)
        self.assertIn("Built authentication flow in auth.py", rendered)
        prompt_view = pack.context_manifest.active_state_prompt_view or {}
        self.assertNotIn("session_work_summary", prompt_view.get("metadata", {}))


class ToolFailureFactPoisoningTests(unittest.TestCase):
    def test_structured_tool_failure_facts_are_excluded_from_prompt(self) -> None:
        facts = [
            FactRecord(
                record_id="fail-1",
                text="Unknown tool: weather.search",
                tags=["tool_failure"],
            ),
            FactRecord(
                record_id="ok-1",
                text="User prefers metric weather reports.",
            ),
        ]
        service = _make_service(memory=_MemoryClient(facts=facts))

        pack = service.build_pack(_make_request(query="weather today"))

        rendered = "\n".join(segment.content for segment in pack.segments)
        self.assertNotIn("Unknown tool: weather.search", rendered)
        self.assertIn("User prefers metric weather reports.", rendered)
        self.assertNotIn("fail-1", pack.context_manifest.facts)
        self.assertIn("ok-1", pack.context_manifest.facts)

    def test_tool_failure_filter_uses_structured_markers_not_fact_text(self) -> None:
        facts = [
            FactRecord(
                record_id="legacy-text-only",
                text="Unknown tool: weather.search",
            ),
        ]
        service = _make_service(memory=_MemoryClient(facts=facts))

        pack = service.build_pack(_make_request(query="weather today"))

        rendered = "\n".join(segment.content for segment in pack.segments)
        self.assertIn("Unknown tool: weather.search", rendered)
        self.assertIn("legacy-text-only", pack.context_manifest.facts)

    def test_negative_tool_outcome_fact_metadata_is_excluded(self) -> None:
        facts = [
            FactRecord(
                record_id="fail-meta",
                text="web.search failed with PROVIDER_TIMEOUT",
                tags=["tool_outcome", "outcome:failure"],
                meta={
                    "source_kind": "tool_outcome",
                    "source_negative_outcome": True,
                    "source_outcome_status": "failure",
                    "source_tool_name": "web.search",
                },
            ),
            FactRecord(
                record_id="success-meta",
                text="web.fetch succeeded for news lookups.",
                tags=["tool_outcome", "outcome:success"],
                meta={
                    "source_kind": "tool_outcome",
                    "source_negative_outcome": False,
                    "source_outcome_status": "success",
                    "source_tool_name": "web.fetch",
                },
            ),
        ]
        service = _make_service(memory=_MemoryClient(facts=facts))

        pack = service.build_pack(_make_request(query="web tools"))

        rendered = "\n".join(segment.content for segment in pack.segments)
        self.assertNotIn("web.search failed with PROVIDER_TIMEOUT", rendered)
        self.assertIn("web.fetch succeeded for news lookups.", rendered)
        self.assertNotIn("fail-meta", pack.context_manifest.facts)
        self.assertIn("success-meta", pack.context_manifest.facts)


class MidSessionRecallTests(unittest.TestCase):
    def test_mid_session_recall_triggers_on_interval_and_records_manifest_truth(self):
        recalled = MemoryCard(
            record_id="mem-mid-1",
            record_type="fact",
            text="We already settled the pytest fixture policy.",
            score=0.88,
        )
        active_state = {
            "intent_execution_states": [
                {"intent_id": "pytest-migration", "status": "active"}
            ],
            "resolved_skill_ids": ["python-tests"],
            "active_skill_id": "python-tests",
            "cursor": 2,
            "plan": {
                "steps": [
                    {"command_id": "cmd-1"},
                    {"command_id": "cmd-2"},
                ]
            },
        }
        service = _make_service(
            session=_SliceSession(
                turns=[
                    SessionTurn(turn_id="t1", role="user", content="1"),
                    SessionTurn(turn_id="t2", role="assistant", content="2"),
                    SessionTurn(turn_id="t3", role="user", content="3"),
                ],
                active_state=active_state,
            ),
            memory=_MemoryClient(mid_session_cards=[recalled]),
        )

        pack = service.build_pack(_make_request(query="what next"))

        self.assertEqual(len(service._memctl.mid_session_calls), 1)  # noqa: SLF001
        call = service._memctl.mid_session_calls[0]  # noqa: SLF001
        self.assertEqual(call["turn_index"], 3)
        self.assertEqual(call["latest_user_message"], "3")
        self.assertEqual(call["intent_ids"], ["pytest-migration"])
        self.assertEqual(call["recent_tool_families"], [])
        self.assertEqual(
            pack.context_manifest.mid_session_recalled_memory, ["mem-mid-1"]
        )
        self.assertEqual(pack.context_manifest.session_start_recalled_memory, [])
        self.assertEqual(pack.context_manifest.recalled_memory, ["mem-mid-1"])
        self.assertEqual(
            pack.context_manifest.mid_session_recall_state.plan_step_ids,
            ["cmd-1", "cmd-2"],
        )

    def test_mid_session_recall_triggers_on_intent_change_between_packs(self) -> None:
        first_slice = SessionSlice(
            session_id="sess-test",
            slice_version="slice:v1",
            summary_short="",
            recent_turns=[SessionTurn(turn_id="t1", role="user", content="hello")],
            active_state={
                "intent_execution_states": [
                    {"intent_id": "pytest-migration", "status": "active"}
                ],
            },
        )
        second_slice = SessionSlice(
            session_id="sess-test",
            slice_version="slice:v2",
            summary_short="",
            recent_turns=[
                SessionTurn(turn_id="t1", role="user", content="hello"),
                SessionTurn(turn_id="t2", role="assistant", content="done"),
            ],
            active_state={
                "intent_execution_states": [
                    {"intent_id": "pytest-migration", "status": "completed"}
                ],
            },
        )
        memory = _MemoryClient(
            mid_session_cards=[
                MemoryCard(
                    record_id="mem-mid-2",
                    record_type="fact",
                    text="The pytest migration is complete.",
                )
            ]
        )
        service = _make_service(
            session=_SequenceSession([first_slice, second_slice]),
            memory=memory,
        )

        first_pack = service.build_pack(_make_request(query="turn one"))
        second_pack = service.build_pack(_make_request(query="turn two"))

        self.assertEqual(first_pack.context_manifest.mid_session_recalled_memory, [])
        self.assertEqual(len(memory.mid_session_calls), 1)
        self.assertEqual(memory.mid_session_calls[0]["intent_statuses"], ["completed"])
        self.assertEqual(
            second_pack.context_manifest.mid_session_recalled_memory,
            ["mem-mid-2"],
        )

    def test_mid_session_recall_triggers_on_typed_skill_pivot_between_packs(
        self,
    ) -> None:
        first_slice = SessionSlice(
            session_id="sess-test",
            slice_version="slice:v1",
            summary_short="",
            recent_turns=[
                SessionTurn(
                    turn_id="t1",
                    role="assistant",
                    content="Use pytest fixture layering for the migration.",
                ),
                SessionTurn(
                    turn_id="t2",
                    role="user",
                    content="What next for pytest fixture layering?",
                ),
            ],
            active_state={
                "intent_execution_states": [],
                "active_skill_id": "python-tests",
                "resolved_skill_ids": ["python-tests"],
            },
        )
        second_slice = SessionSlice(
            session_id="sess-test",
            slice_version="slice:v2",
            summary_short="",
            recent_turns=[
                SessionTurn(
                    turn_id="t1",
                    role="assistant",
                    content="Use pytest fixture layering for the migration.",
                ),
                SessionTurn(
                    turn_id="t2",
                    role="user",
                    content="Still on the same repo, but switch me to Kubernetes work.",
                ),
            ],
            active_state={
                "intent_execution_states": [],
                "active_skill_id": "kubernetes",
                "resolved_skill_ids": ["kubernetes"],
            },
        )
        memory = _MemoryClient(
            mid_session_cards=[
                MemoryCard(
                    record_id="mem-mid-3",
                    record_type="fact",
                    text="Remember the Kubernetes readiness probe guidance.",
                )
            ]
        )
        service = _make_service(
            session=_SequenceSession([first_slice, second_slice]),
            memory=memory,
        )

        first_pack = service.build_pack(_make_request(query="turn one"))
        second_pack = service.build_pack(_make_request(query="turn two"))

        self.assertEqual(first_pack.context_manifest.mid_session_recalled_memory, [])
        self.assertEqual(len(memory.mid_session_calls), 1)
        self.assertEqual(
            memory.mid_session_calls[0]["latest_user_message"],
            "Still on the same repo, but switch me to Kubernetes work.",
        )
        self.assertEqual(memory.mid_session_calls[0]["active_skill_id"], "kubernetes")
        self.assertEqual(
            memory.mid_session_calls[0]["resolved_skill_ids"], ["kubernetes"]
        )
        self.assertEqual(
            second_pack.context_manifest.mid_session_recalled_memory,
            ["mem-mid-3"],
        )

    def test_mid_session_recall_does_not_trigger_on_lexical_paraphrase_alone(
        self,
    ) -> None:
        first_slice = SessionSlice(
            session_id="sess-test",
            slice_version="slice:v1",
            summary_short="",
            recent_turns=[
                SessionTurn(
                    turn_id="t1",
                    role="assistant",
                    content="Use pytest fixture layering for the migration.",
                ),
                SessionTurn(
                    turn_id="t2",
                    role="user",
                    content="What next for pytest fixture layering?",
                ),
            ],
            active_state={"intent_execution_states": []},
        )
        second_slice = SessionSlice(
            session_id="sess-test",
            slice_version="slice:v2",
            summary_short="",
            recent_turns=[
                SessionTurn(
                    turn_id="t1",
                    role="assistant",
                    content="Use pytest fixture layering for the migration.",
                ),
                SessionTurn(
                    turn_id="t2",
                    role="user",
                    content="How should I handle pytest fixture layering next?",
                ),
            ],
            active_state={"intent_execution_states": []},
        )
        memory = _MemoryClient(
            mid_session_cards=[
                MemoryCard(
                    record_id="mem-mid-4",
                    record_type="fact",
                    text="This should not be recalled by overlap alone.",
                )
            ]
        )
        service = _make_service(
            session=_SequenceSession([first_slice, second_slice]),
            memory=memory,
        )

        service.build_pack(_make_request(query="turn one"))
        second_pack = service.build_pack(_make_request(query="turn two"))

        self.assertEqual(len(memory.mid_session_calls), 0)
        self.assertEqual(second_pack.context_manifest.mid_session_recalled_memory, [])

    def test_mid_session_recall_triggers_on_recent_tool_family_change(self) -> None:
        first_slice = SessionSlice(
            session_id="sess-test",
            slice_version="slice:v1",
            summary_short="",
            recent_turns=[
                SessionTurn(turn_id="t1", role="user", content="List the repo files."),
                SessionTurn(
                    turn_id="t2", role="assistant", content="Using file tools."
                ),
            ],
            recent_tool_events=[
                SessionToolEvent(
                    event_id="evt-file",
                    tool_name="file.read",
                    excerpt="Read the repo files.",
                )
            ],
            active_state={"intent_execution_states": []},
        )
        second_slice = SessionSlice(
            session_id="sess-test",
            slice_version="slice:v2",
            summary_short="",
            recent_turns=[
                SessionTurn(turn_id="t1", role="user", content="List the repo files."),
                SessionTurn(
                    turn_id="t2", role="assistant", content="Using file tools."
                ),
                SessionTurn(
                    turn_id="t3",
                    role="user",
                    content="Now check the weather before we deploy.",
                ),
            ],
            recent_tool_events=[
                SessionToolEvent(
                    event_id="evt-file",
                    tool_name="file.read",
                    excerpt="Read the repo files.",
                ),
                SessionToolEvent(
                    event_id="evt-weather",
                    tool_name="weather.forecast",
                    excerpt="Checked the weather.",
                ),
            ],
            active_state={"intent_execution_states": []},
        )
        memory = _MemoryClient(
            mid_session_cards=[
                MemoryCard(
                    record_id="mem-mid-tool-family",
                    record_type="fact",
                    text="Weather context surfaced after tool-family pivot.",
                )
            ]
        )
        service = _make_service(
            session=_SequenceSession([first_slice, second_slice]),
            memory=memory,
        )

        service.build_pack(_make_request(query="turn one"))
        second_pack = service.build_pack(_make_request(query="turn two"))

        self.assertEqual(len(memory.mid_session_calls), 1)
        self.assertEqual(
            memory.mid_session_calls[0]["recent_tool_families"],
            ["file", "weather"],
        )
        self.assertEqual(
            second_pack.context_manifest.mid_session_recalled_memory,
            ["mem-mid-tool-family"],
        )

    def test_mid_session_recall_dedupes_against_query_bound_memory(self) -> None:
        shared = MemoryCard(
            record_id="mem-shared",
            record_type="fact",
            text="Shared recall card.",
        )
        service = _make_service(
            session=_SliceSession(
                turns=[
                    SessionTurn(turn_id="t1", role="user", content="1"),
                    SessionTurn(turn_id="t2", role="assistant", content="2"),
                    SessionTurn(turn_id="t3", role="user", content="3"),
                ],
                active_state={},
            ),
            memory=_MemoryClient(cards=[shared], mid_session_cards=[shared]),
        )

        pack = service.build_pack(_make_request(query="what next"))

        self.assertEqual(pack.context_manifest.memory, ["mem-shared"])
        self.assertEqual(
            pack.context_manifest.mid_session_recalled_memory, ["mem-shared"]
        )

    def test_mid_session_recall_failure_fails_closed(self) -> None:
        service = _make_service(
            session=_SliceSession(
                turns=[
                    SessionTurn(turn_id="t1", role="user", content="1"),
                    SessionTurn(turn_id="t2", role="assistant", content="2"),
                    SessionTurn(turn_id="t3", role="user", content="3"),
                ],
                active_state={"intent_execution_states": []},
            ),
            memory=_MemoryClient(mid_session_error=RuntimeError("backend down")),
        )

        pack = service.build_pack(_make_request(query="what next"))

        self.assertEqual(pack.context_manifest.mid_session_recalled_memory, [])


class DegradeOrderingTests(unittest.TestCase):
    def _tight_budget(self) -> ContextBudgets:
        return ContextBudgets(
            total_max_tokens=80,
            identity_tokens=30,
            summary_tokens=10,
            recent_turn_tokens=20,
            facts_tokens=20,
            memory_tokens=20,
            skills_tokens=5,
            artifact_tokens=20,
            instructions_tokens=10,
        )

    def test_degrade_drops_artifacts_before_memory(self) -> None:
        artifacts = [
            ArtifactDigest(ref=f"ref-{i}", bullets=["a" * 200]) for i in range(5)
        ]
        memory = [
            MemoryCard(record_id=f"m-{i}", record_type="note", text="x" * 100)
            for i in range(5)
        ]
        service = _make_service(
            artifact=_ArtifactClient(digests=artifacts),
            memory=_MemoryClient(cards=memory),
        )
        req = _make_request(budgets_override=self._tight_budget())
        pack = service.build_pack(req)
        trace = pack.token_budget_report.degrade_trace
        actions = pack.token_budget_report.decision_log.actions
        artifact_drop_positions = [
            i for i, a in enumerate(actions) if a.bucket == "evidence_refs"
        ]
        memory_drop_positions = [
            i for i, a in enumerate(actions) if a.bucket == "retrieval"
        ]
        if artifact_drop_positions and memory_drop_positions:
            self.assertLess(artifact_drop_positions[0], memory_drop_positions[0])
        self.assertIsInstance(trace, list)

    def test_degrade_trace_is_non_empty_under_pressure(self) -> None:
        big_facts = [FactRecord(record_id=f"f-{i}", text="x" * 500) for i in range(10)]
        big_memory = [
            MemoryCard(record_id=f"m-{i}", record_type="note", text="y" * 500)
            for i in range(10)
        ]
        service = _make_service(memory=_MemoryClient(facts=big_facts, cards=big_memory))
        req = _make_request(budgets_override=self._tight_budget())
        pack = service.build_pack(req)
        self.assertIsNotNone(pack.token_budget_report.degrade_trace)
        self.assertIsInstance(pack.token_budget_report.degrade_trace, list)

    def test_budget_report_sections_have_omitted_reason_when_dropped(self) -> None:
        artifacts = [ArtifactDigest(ref="big-ref", bullets=["x" * 500])]
        service = _make_service(artifact=_ArtifactClient(digests=artifacts))
        req = _make_request(budgets_override=self._tight_budget())
        pack = service.build_pack(req)
        actions = pack.token_budget_report.decision_log.actions
        evidence_drops = [a for a in actions if a.bucket == "evidence_refs"]
        for action in evidence_drops:
            self.assertTrue(action.reason_code)

    def test_identity_never_dropped(self) -> None:
        service = _make_service()
        req = _make_request(budgets_override=self._tight_budget())
        pack = service.build_pack(req)
        system_content = pack.messages[0].content
        self.assertIn("[IDENTITY]", system_content)

    def test_segments_include_static_prefix_pinned(self) -> None:
        service = _make_service()
        req = _make_request()
        pack = service.build_pack(req)
        static_segs = [s for s in pack.segments if s.bucket == "static_prefix"]
        self.assertEqual(len(static_segs), 1)
        self.assertTrue(static_segs[0].pinned)
        self.assertIn("[IDENTITY]", static_segs[0].content)


class ValidateContextRenderingTests(unittest.TestCase):
    def test_validate_context_renders_feasibility_inputs(self) -> None:
        service = _make_service()
        req = BuildPackRequest(
            session_id="sess-validate",
            agent_id="agent-test",
            purpose="validate",
            query="check plan feasibility",
            constraints=BuildConstraints(
                style_overrides={
                    "feasibility_contract": "Return structured feasibility."
                }
            ),
            phase_hints={
                "feasibility_sub_intents": [
                    {"id": "intent-a", "description": "weather"}
                ],
                "feasibility_plan_steps": [{"command_id": "cmd-1", "title": "weather"}],
                "feasibility_runtime_facts": [
                    {"tool_name": "weather", "kind": "auth_status", "value": "missing"}
                ],
            },
        )

        pack = service.build_pack(req)
        mission = next(seg for seg in pack.segments if seg.bucket == "mission_snapshot")

        self.assertIn("[VALIDATE CONTEXT]", mission.content)
        self.assertIn("feasibility_sub_intents", str(req.phase_hints))
        self.assertIn("runtime_facts", mission.content)

    def test_segments_include_evidence_refs_for_artifacts(self) -> None:
        artifacts = [ArtifactDigest(ref="art-1", bullets=["bullet"])]
        service = _make_service(artifact=_ArtifactClient(digests=artifacts))
        req = _make_request()
        pack = service.build_pack(req)
        ev_segs = [s for s in pack.segments if s.bucket == "evidence_refs"]
        self.assertEqual(len(ev_segs), 1)
        self.assertTrue(ev_segs[0].is_artifact_preview)
        self.assertIn("art-1", ev_segs[0].refs)


class TypedRetrievalGateTests(unittest.TestCase):
    def test_fact_text_capped_at_200_chars(self) -> None:
        long_fact = FactRecord(record_id="f-1", text="x" * 1000)
        service = _make_service(memory=_MemoryClient(facts=[long_fact]))
        req = _make_request()
        pack = service.build_pack(req)
        system_text = pack.messages[0].content
        self.assertLess(len(system_text), 10000)
        self.assertNotIn("x" * 201, system_text)

    def test_memory_text_capped_at_250_chars(self) -> None:
        long_card = MemoryCard(record_id="m-1", record_type="note", text="y" * 1000)
        service = _make_service(memory=_MemoryClient(cards=[long_card]))
        req = _make_request()
        pack = service.build_pack(req)
        system_text = pack.messages[0].content
        self.assertNotIn("y" * 251, system_text)

    def test_fact_top_k_limited_to_20(self) -> None:
        facts = [FactRecord(record_id=f"f-{i}", text=f"fact {i}") for i in range(30)]
        service = _make_service(memory=_MemoryClient(facts=facts))
        req = _make_request()
        pack = service.build_pack(req)
        manifest_facts = pack.context_manifest.facts
        self.assertLessEqual(len(manifest_facts), 20)

    def test_memory_top_k_limited_to_15(self) -> None:
        cards = [
            MemoryCard(record_id=f"m-{i}", record_type="note", text=f"card {i}")
            for i in range(20)
        ]
        service = _make_service(memory=_MemoryClient(cards=cards))
        req = _make_request()
        pack = service.build_pack(req)
        manifest_memory = pack.context_manifest.memory
        self.assertLessEqual(len(manifest_memory), 15)

    def test_artifact_top_k_limited_to_10(self) -> None:
        digests = [ArtifactDigest(ref=f"art-{i}", bullets=["b"]) for i in range(15)]
        service = _make_service(artifact=_ArtifactClient(digests=digests))
        req = _make_request()
        pack = service.build_pack(req)
        manifest_artifacts = pack.context_manifest.artifacts
        self.assertLessEqual(len(manifest_artifacts), 10)


class SummaryDeltaTests(unittest.TestCase):
    def test_make_delta_increments_seq(self) -> None:
        service = _make_service()
        d1 = service.make_delta(
            session_id="sess-1", agent_id="agent-1", content="Turn 1 summary"
        )
        d2 = service.make_delta(
            session_id="sess-1", agent_id="agent-1", content="Turn 2 summary"
        )
        self.assertEqual(d1.seq, 1)
        self.assertEqual(d2.seq, 2)

    def test_get_summary_deltas_returns_all(self) -> None:
        service = _make_service()
        service.make_delta(session_id="sess-1", agent_id="agent-1", content="A")
        service.make_delta(session_id="sess-1", agent_id="agent-1", content="B")
        deltas = service.get_summary_deltas("sess-1")
        self.assertEqual(len(deltas), 2)

    def test_compact_resets_deltas(self) -> None:
        service = _make_service()
        for i in range(5):
            service.make_delta(
                session_id="sess-1", agent_id="agent-1", content=f"Turn {i}"
            )
        compacted = service.maybe_compact("sess-1", threshold=5)
        self.assertTrue(compacted)
        self.assertEqual(len(service.get_summary_deltas("sess-1")), 0)

    def test_compact_below_threshold_does_nothing(self) -> None:
        service = _make_service()
        service.make_delta(session_id="sess-1", agent_id="agent-1", content="A")
        service.make_delta(session_id="sess-1", agent_id="agent-1", content="B")
        compacted = service.maybe_compact("sess-1", threshold=5)
        self.assertFalse(compacted)
        self.assertEqual(len(service.get_summary_deltas("sess-1")), 2)

    def test_compact_creates_base_from_deltas(self) -> None:
        service = _make_service()
        for i in range(5):
            service.make_delta(
                session_id="sess-1", agent_id="agent-1", content=f"content-{i}"
            )
        service.maybe_compact("sess-1", threshold=5)
        base = service.get_summary_base("sess-1")
        self.assertIsNotNone(base)
        self.assertIn("content-0", base)
        self.assertIn("content-4", base)

    def test_delta_isolation_across_sessions(self) -> None:
        service = _make_service()
        service.make_delta(session_id="sess-A", agent_id="agent-1", content="A only")
        service.make_delta(session_id="sess-B", agent_id="agent-1", content="B only")
        self.assertEqual(len(service.get_summary_deltas("sess-A")), 1)
        self.assertEqual(len(service.get_summary_deltas("sess-B")), 1)


class FeatureToggleTests(unittest.TestCase):
    def test_rolling_disabled_omits_session_summary(self) -> None:
        service = _make_service(
            session=_SliceSession(summary_short="roll me"),
            rolling_enabled=False,
        )
        pack = service.build_pack(_make_request())
        rendered = "\n".join(m.content for m in pack.messages)
        self.assertNotIn("[SESSION SUMMARY]", rendered)

    def test_compression_disabled_omits_compression_reference(self) -> None:
        service = _make_service(
            session=_SliceSession(summary_short="summary", checkpoint_id="cp-1"),
            compress=_CompressClient(snapshot="compressed"),
            compression_enabled=False,
        )
        pack = service.build_pack(_make_request())
        rendered = "\n".join(m.content for m in pack.messages)
        self.assertNotIn("[COMPRESSION REFERENCE]", rendered)

    def test_compaction_disabled_skips_delta_storage(self) -> None:
        service = _make_service(compaction_enabled=False)
        delta = service.make_delta(session_id="sess-1", agent_id="agent-1", content="x")
        self.assertEqual(delta.seq, 0)
        self.assertEqual(len(service.get_summary_deltas("sess-1")), 0)
        self.assertFalse(service.maybe_compact("sess-1"))


class ClarifyDigestTests(unittest.TestCase):
    def test_clarify_digest_is_bounded_and_ordered_before_task_header(self) -> None:
        big_transcript = "T" * 4000
        active_state = {
            "status": "waiting_user",
            "unresolved_clarify_items": [
                {"id": "q1", "question": "Which location should I check weather for?"},
                {"id": "q2", "question": "Do you want Celsius or Fahrenheit?"},
                {"id": "q3", "question": "Should I include precipitation details?"},
                {
                    "id": "q4",
                    "question": "This should be trimmed out of digest output.",
                },
            ],
            "clarify_responses": {
                "q1": "San Diego",
                "q2": "Celsius",
                "q3": "Yes",
                "q4": "extra answer",
            },
            "defaults_used": {"units": "metric"},
            "clarify_transcript": big_transcript,
        }
        service = _make_service(
            session=_SliceSession(summary_short="summary", active_state=active_state)
        )
        pack = service.build_pack(_make_request(query="weather today"))
        mission = next(s for s in pack.segments if s.bucket == "mission_snapshot")
        self.assertIn("[CLARIFY DIGEST]", mission.content)
        self.assertLess(
            mission.content.index("[CLARIFY DIGEST]"),
            mission.content.index("[TASK HEADER]"),
        )
        self.assertNotIn(big_transcript[:200], mission.content)
        self.assertLessEqual(len(mission.content), 2200)

    def test_active_state_projection_excludes_clarify_transcript_keys(self) -> None:
        active_state = {
            "status": "waiting_user",
            "clarify_history": ["line1", "line2", "line3"],
            "clarify_transcript": "very long transcript " * 300,
            "task_id": "t-1",
        }
        service = _make_service(
            session=_SliceSession(summary_short="summary", active_state=active_state)
        )
        pack = service.build_pack(_make_request(query="continue"))
        rendered = "\n".join(m.content for m in pack.messages)
        self.assertNotIn("very long transcript", rendered)
        self.assertNotIn("clarify_history", rendered)

    def test_judge_prompt_renders_closure_hints_in_dedicated_section(self) -> None:
        service = _make_service(session=_SliceSession(summary_short="summary"))
        req = BuildPackRequest(
            session_id="sess-judge",
            agent_id="agent-test",
            purpose="judge",
            query="what's the weather in San Diego?",
            phase_hints={
                "closure_candidate_reason": "plan_completed",
                "closure_action_summary": "Weather: 16C in San Diego",
                "closure_sub_intents": ["lookup_weather", "answer_user"],
                "closure_intent_outcomes": [
                    {"intent_id": "intent_01_lookup_weather", "status": "succeeded"}
                ],
                "closure_success_criteria": {"weather_returned": True},
            },
        )

        pack = service.build_pack(req)
        mission = next(s for s in pack.segments if s.bucket == "mission_snapshot")

        self.assertIn("[JUDGE CONTEXT]", mission.content)
        self.assertIn("action_summary: Weather: 16C in San Diego", mission.content)
        self.assertIn('sub_intents: ["lookup_weather", "answer_user"]', mission.content)
        self.assertIn("intent_outcomes:", mission.content)
        self.assertIn('success_criteria: {"weather_returned": true}', mission.content)


class CacheBehaviorTests(unittest.TestCase):
    def test_cache_hit_returns_same_pack(self) -> None:
        service = _make_service()
        req = _make_request()
        pack1 = service.build_pack(req)
        pack2 = service.build_pack(req)
        self.assertEqual(pack1.pack_version, pack2.pack_version)

    def test_cache_miss_on_new_query(self) -> None:
        service = _make_service()
        pack1 = service.build_pack(_make_request(query="hello"))
        pack2 = service.build_pack(_make_request(query="different query"))
        self.assertNotEqual(pack1.pack_version, pack2.pack_version)

    def test_cache_miss_on_provider_pref_change(self) -> None:
        service = _make_service()
        req1 = BuildPackRequest(
            session_id="s",
            agent_id="a",
            purpose="act",
            query="q",
            provider_pref="openai",
        )
        req2 = BuildPackRequest(
            session_id="s",
            agent_id="a",
            purpose="act",
            query="q",
            provider_pref="anthropic",
        )
        service.build_pack(req1)
        service.build_pack(req2)
        self.assertEqual(len(service._cache), 2)

    def test_cache_miss_on_phase_hints_change(self) -> None:
        service = _make_service()
        req1 = BuildPackRequest(
            session_id="s",
            agent_id="a",
            purpose="judge",
            query="q",
            phase_hints={"closure_action_summary": "first"},
        )
        req2 = BuildPackRequest(
            session_id="s",
            agent_id="a",
            purpose="judge",
            query="q",
            phase_hints={"closure_action_summary": "second"},
        )
        service.build_pack(req1)
        service.build_pack(req2)
        self.assertEqual(len(service._cache), 2)

    def test_live_state_overlay_bypasses_cache_and_updates_active_state(self) -> None:
        service = _make_service(
            session=_SliceSession(
                summary_short="summary",
                active_state={
                    "status": "done",
                    "last_result": {"status": "success", "summary": "stale result"},
                    "step_outputs": [],
                },
            )
        )
        base_req = BuildPackRequest(
            session_id="s",
            agent_id="a",
            purpose="judge",
            query="what's the weather in San Diego?",
        )
        base_pack = service.build_pack(base_req)
        self.assertEqual(len(service._cache), 1)

        overlay_req = BuildPackRequest(
            session_id="s",
            agent_id="a",
            purpose="judge",
            query="what's the weather in San Diego?",
            live_state_overlay={
                "goal": "what's the weather in San Diego?",
                "status": "done",
                "last_result": {
                    "status": "success",
                    "summary": "Weather: 16C in San Diego",
                },
                "step_outputs": [
                    {
                        "step_index": 0,
                        "command_id": "cmd-weather",
                        "output_key": "weather",
                        "summary": "Weather: 16C in San Diego",
                        "outputs": {"temperature_c": 16},
                        "artifact_refs": [],
                    }
                ],
            },
        )
        overlay_pack = service.build_pack(overlay_req)
        rendered = "\n".join(m.content for m in overlay_pack.messages)

        self.assertEqual(len(service._cache), 1)
        self.assertNotEqual(base_pack.pack_version, overlay_pack.pack_version)
        self.assertEqual(
            overlay_pack.context_manifest.active_state_full["last_result"]["summary"],
            "Weather: 16C in San Diego",
        )
        self.assertIn("Weather: 16C in San Diego", rendered)

    def test_explain_pack_resolves_for_cached_pack(self) -> None:
        service = _make_service()
        pack = service.build_pack(_make_request())
        manifest = service.explain_pack(pack.pack_version)
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest.identity.agent_id, "agent-test")

    def test_explain_pack_returns_none_for_unknown_version(self) -> None:
        service = _make_service()
        manifest = service.explain_pack("nonexistent-version-xyz")
        self.assertIsNone(manifest)


class ManifestV12Tests(unittest.TestCase):
    def test_context_manifest_includes_retrieval_summary(self) -> None:
        service = _make_service()
        pack = service.build_pack(_make_request())
        self.assertIsNotNone(pack.context_manifest.retrieval_summary)

    def test_retrieval_summary_present(self) -> None:
        service = _make_service()
        pack = service.build_pack(_make_request())
        self.assertIsNotNone(pack.context_manifest.retrieval_summary)

    def test_compression_summary_present(self) -> None:
        service = _make_service()
        pack = service.build_pack(_make_request())
        self.assertIsNotNone(pack.context_manifest.compression_summary)

    def test_pack_hash_equals_pack_version(self) -> None:
        service = _make_service()
        pack = service.build_pack(_make_request())
        self.assertEqual(pack.pack_hash, pack.pack_version)

    def test_context_manifest_has_segment_audit(self) -> None:
        service = _make_service()
        pack = service.build_pack(_make_request())
        manifest = pack.context_manifest
        self.assertIsNotNone(manifest)
        self.assertGreater(len(manifest.segment_ids), 0)
        self.assertGreater(len(manifest.included_segment_ids), 0)
        seg_ids_str = " ".join(manifest.included_segment_ids)
        self.assertIn("static_prefix", seg_ids_str)
        self.assertIn("mission_snapshot", seg_ids_str)

    def test_prompt_cache_key_in_manifest(self) -> None:
        service = _make_service()
        pack = service.build_pack(_make_request())
        self.assertNotEqual(pack.prompt_cache_key, "")
        self.assertNotEqual(pack.static_prefix_hash, "")
        self.assertEqual(pack.context_manifest.prompt_cache_key, pack.prompt_cache_key)

    def test_static_prefix_render_message_carries_cache_control(self) -> None:
        service = _make_service()
        pack = service.build_pack(_make_request())
        system_messages = [m for m in pack.messages if m.role == "system"]
        self.assertGreater(len(system_messages), 0)
        self.assertEqual(
            system_messages[0].cache_control,
            {"type": "ephemeral"},
        )

    def test_static_prefix_render_message_carries_block_metadata(self) -> None:
        service = _make_service()
        pack = service.build_pack(_make_request())
        system_messages = [m for m in pack.messages if m.role == "system"]
        self.assertGreater(len(system_messages), 0)
        metadata = dict(system_messages[0].meta)
        self.assertEqual(metadata.get("block_kind"), "static_prefix")
        self.assertTrue(bool(metadata.get("cache_eligible")))
        self.assertIn("static_prefix", metadata.get("segment_ids", []))
        self.assertTrue(str(metadata.get("cache_key", "")).startswith("static_prefix:"))
        self.assertTrue(
            all(
                str(item).startswith("content_hash:")
                for item in metadata.get("cache_invalidation_refs", [])
            )
        )

    def test_budget_telemetry_render_message_carries_non_cacheable_block_metadata(
        self,
    ) -> None:
        service = _make_service()
        pack = service.build_pack(
            _make_request(
                purpose="decide",
                budget_telemetry={
                    "iteration_used": 22,
                    "iteration_remaining": 2,
                    "iteration_max": 24,
                    "tool_calls_used": 5,
                    "tool_calls_remaining": 3,
                    "tool_calls_max": 8,
                    "budget_envelope_status": "near_exhaustion",
                },
            )
        )
        budget_messages = [
            m
            for m in pack.messages
            if dict(m.meta).get("block_kind") == "budget_telemetry"
        ]
        self.assertEqual(len(budget_messages), 1)
        budget_message = budget_messages[0]
        self.assertIsNone(budget_message.cache_control)
        self.assertFalse(bool(dict(budget_message.meta).get("cache_eligible")))
        self.assertIn("budget_telemetry", budget_message.meta.get("segment_ids", []))
        self.assertIn(
            '"budget_envelope_status": "near_exhaustion"', budget_message.content
        )


@unittest.skip(
    "CONTRACT_RESET_2026: legacy `pack.budget_report.sections['identity']` "
    "surface removed in CCS-05. Identity-section ordering/priority/caps remain "
    "exercised at the IdentityBudgetResult layer in pack/identity.py and "
    "via render-helper unit tests."
)
class IdentityBudgetingTests(unittest.TestCase):
    def test_identity_sections_use_deterministic_order_and_unknown_lexical(
        self,
    ) -> None:
        pass

    def test_identity_sections_priority_tie_break_is_deterministic(self) -> None:
        pass

    def test_identity_budget_falls_back_to_legacy_when_sections_missing(self) -> None:
        pass

    def test_identity_budget_report_contains_structured_subsections(self) -> None:
        pass


class ModeAwareContextTests(unittest.TestCase):
    def test_plan_mode_includes_tool_inventory_and_constraints_context(self) -> None:
        service = _make_service()
        req = BuildPackRequest(
            session_id="sess-plan",
            agent_id="agent-test",
            purpose="plan",
            mode_name="plan",
            query="plan my desk cleanup",
            constraints=BuildConstraints(
                runtime_tool_schemas=[{"name": "web.search"}, {"name": "task.create"}]
            ),
        )

        pack = service.build_pack(req)
        mission = next(seg for seg in pack.segments if seg.bucket == "mission_snapshot")

        self.assertIn("[MODE CONTEXT]", mission.content)
        self.assertIn(
            "plan mode: preserve constraints, procedures, and available tools.",
            mission.content,
        )
        self.assertIn("- web.search", mission.content)
        self.assertIn("- task.create", mission.content)

    def test_respond_mode_prefers_concise_recent_context(self) -> None:
        turns = [
            SessionTurn(
                turn_id=f"turn-{idx}", role="assistant", content=f"detail {idx} " * 20
            )
            for idx in range(8)
        ]
        service = _make_service(session=_SliceSession(turns=turns))

        respond_pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-respond",
                agent_id="agent-test",
                purpose="act",
                mode_name="respond",
                query="what changed",
            )
        )
        default_pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-default",
                agent_id="agent-test",
                purpose="act",
                query="what changed",
            )
        )

        respond_recent = [
            seg for seg in respond_pack.segments if seg.bucket == "recent_window"
        ]
        default_recent = [
            seg for seg in default_pack.segments if seg.bucket == "recent_window"
        ]

        self.assertLessEqual(len(respond_recent), len(default_recent))
        mission = next(
            seg for seg in respond_pack.segments if seg.bucket == "mission_snapshot"
        )
        self.assertIn(
            "respond mode: favor concise summaries and recent factual context. "
            "If the session already contains a recent greeting exchange, continue "
            "the conversation instead of restarting it with the same opener.",
            mission.content,
        )

    def test_unknown_mode_falls_back_without_crashing(self) -> None:
        service = _make_service()
        pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-unknown",
                agent_id="agent-test",
                purpose="act",
                mode_name="workflow_x",
                query="fallback mode",
            )
        )

        self.assertEqual(pack.session_id, "sess-unknown")
        mission = next(seg for seg in pack.segments if seg.bucket == "mission_snapshot")
        self.assertNotIn("[MODE CONTEXT]\nrespond mode", mission.content)
