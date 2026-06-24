import unittest

from openminion.modules.context.prefix import PinnedPrefixBuilder
from openminion.modules.context.schemas import (
    ArtifactDigest,
    BuildConstraints,
    BuildPackRequest,
    ContextBudgets,
    IdentitySnippet,
    SessionSlice,
    SessionToolEvent,
    SessionTurn,
    TaskPlan,
    decide_budget_for_turn_depth,
    default_budgets_for,
)
from openminion.modules.context.service import ContextCtlService


class _IdentityClient:
    contract_version = "v1"

    def render(
        self, *, agent_id: str, purpose: str, max_tokens: int, provider_pref=None
    ) -> IdentitySnippet:
        return IdentitySnippet(
            agent_id=agent_id,
            profile_version="prof-v1",
            render_version="rend-v1",
            text=f"Agent: {agent_id}\nPurpose: {purpose}",
        )


class _SessionClient:
    contract_version = "v1"

    def __init__(self, tool_events=None):
        self.events = []
        self._tool_events = tool_events or []

    def get_slice(
        self, *, session_id: str, purpose: str, limits: dict[str, int]
    ) -> SessionSlice:
        del purpose, limits
        return SessionSlice(
            session_id=session_id,
            slice_version="slice-v1",
            last_event_id="evt-last",
            summary_short="summary short",
            recent_tool_events=self._tool_events,
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
        self, session_id: str, event_type: str, payload: dict, **kwargs
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

    def query_facts(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
        mode_name: str | None = None,
    ):
        del session_id, agent_id, query, limit, mode_name
        return []

    def query_memory_cards(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
        mode_name: str | None = None,
    ):
        del session_id, agent_id, query, limit, mode_name
        return []

    def recall_session_start_memory(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        turn_index: int,
        limit: int,
        mode_name: str | None = None,
    ):
        del session_id, agent_id, query, turn_index, limit, mode_name
        return []

    def recall_mid_session_memory(self, **kwargs):
        del kwargs
        return []

    def recall_recent_session_artifacts(self, **kwargs):
        del kwargs
        return []

    def get_procedure(self, *, procedure_id: str):
        del procedure_id
        return


class _ArtifactClient:
    contract_version = "v1"

    def __init__(self, digests=None):
        self._digests = digests or []

    def query_digests(self, *, session_id: str, agent_id: str, query: str, limit: int):
        del session_id, agent_id, query
        return self._digests[:limit]


def _make_service(session_client=None, artifact_client=None) -> ContextCtlService:
    return ContextCtlService(
        identityctl=_IdentityClient(),
        sessctl=session_client or _SessionClient(),
        memctl=_MemoryClient(),
        artifactctl=artifact_client or _ArtifactClient(),
    )


class PinnedPrefixBuilderTests(unittest.TestCase):
    def test_prefix_is_byte_stable_for_equivalent_input(self) -> None:
        builder = PinnedPrefixBuilder()
        identity = "Agent: openminion\nPurpose: act"

        first = builder.build(
            identity_text=identity,
            tool_schemas=[
                {"name": "b", "schema": {"z": 1, "a": 2}},
                {"name": "a", "schema": {"x": True}},
            ],
            policy_rules=["z-rule", "a-rule"],
        )
        second = builder.build(
            identity_text=identity,
            tool_schemas=[
                {"name": "a", "schema": {"x": True}},
                {"name": "b", "schema": {"a": 2, "z": 1}},
            ],
            policy_rules=["a-rule", "z-rule"],
        )

        self.assertEqual(first, second)
        self.assertEqual(builder.hash(first), builder.hash(second))


class V15PackingBehaviorTests(unittest.TestCase):
    def test_position_aware_order_keeps_query_last(self) -> None:
        service = _make_service()
        pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-1",
                agent_id="agent-1",
                purpose="act",
                query="final user query",
            )
        )
        self.assertTrue(pack.segments)
        self.assertEqual(pack.segments[0].bucket, "static_prefix")
        self.assertEqual(pack.messages[-1].role, "user")
        self.assertEqual(pack.messages[-1].content, "final user query")
        self.assertTrue(bool(pack.prompt_cache_key))
        self.assertTrue(bool(pack.static_prefix_hash))

    def test_tool_outputs_are_distilled_to_tool_summary_segments(self) -> None:
        huge_excerpt = "X" * 1200
        session = _SessionClient(
            tool_events=[
                SessionToolEvent(
                    event_id="evt-tool-1",
                    tool_name="os.exec",
                    excerpt=huge_excerpt,
                    artifact_refs=["artifact:abc"],
                )
            ]
        )
        service = _make_service(session_client=session)
        pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-2",
                agent_id="agent-1",
                purpose="act",
                query="use tool result",
            )
        )
        tool_segments = [seg for seg in pack.segments if seg.id.startswith("toolsum:")]
        self.assertEqual(len(tool_segments), 1)
        seg = tool_segments[0]
        self.assertEqual(seg.bucket, "evidence_refs")
        self.assertTrue(seg.is_artifact_preview)
        self.assertNotIn("X" * 400, seg.content)

    def test_bucket_caps_enforced_for_evidence_refs(self) -> None:
        digests = [
            ArtifactDigest(
                ref=f"artifact:{idx}", bullets=["Y" * 600], excerpt="Y" * 1200
            )
            for idx in range(8)
        ]
        service = _make_service(artifact_client=_ArtifactClient(digests=digests))
        pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-3",
                agent_id="agent-1",
                purpose="act",
                query="heavy artifacts",
                budgets_override=ContextBudgets(
                    total_max_tokens=400,
                    identity_tokens=80,
                    summary_tokens=60,
                    recent_turn_tokens=60,
                    facts_tokens=20,
                    memory_tokens=20,
                    skills_tokens=20,
                    artifact_tokens=40,
                    instructions_tokens=40,
                ),
            )
        )
        self.assertIsNotNone(pack.token_budget_report)
        evidence_bucket = pack.token_budget_report.buckets["evidence_refs"]
        self.assertLessEqual(evidence_bucket.used_tokens, evidence_bucket.cap_tokens)

    def test_recent_window_drops_duplicate_current_user_turn(self) -> None:
        class _DupTurnSessionClient(_SessionClient):
            def get_slice(
                self, *, session_id: str, purpose: str, limits: dict[str, int]
            ) -> SessionSlice:
                del purpose, limits
                return SessionSlice(
                    session_id=session_id,
                    slice_version="slice-v1",
                    last_event_id="evt-last",
                    summary_short="summary short",
                    recent_turns=[
                        SessionTurn(
                            turn_id="t1", role="assistant", content="previous answer"
                        ),
                        SessionTurn(turn_id="t2", role="user", content="test"),
                    ],
                )

        service = _make_service(session_client=_DupTurnSessionClient())
        pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-dup",
                agent_id="agent-1",
                purpose="decide",
                query="test",
            )
        )

        user_messages = [msg.content for msg in pack.messages if msg.role == "user"]
        self.assertEqual(user_messages.count("test"), 1)

    def test_decide_recent_turn_budget_retains_clarify_exchange(self) -> None:
        self.assertEqual(default_budgets_for("decide").recent_turn_tokens, 1400)

    def test_decide_budget_scales_by_canonical_turn_depth(self) -> None:
        cases = [
            (1, 1500, 1000, 0),
            (3, 2200, 1400, 300),
            (7, 2800, 1600, 500),
            (15, 3200, 1600, 800),
        ]
        for turn_count, total, recent, conversation_summary in cases:
            with self.subTest(turn_count=turn_count):
                budgets = decide_budget_for_turn_depth(turn_count)
                self.assertEqual(budgets.total_max_tokens, total)
                self.assertEqual(budgets.recent_turn_tokens, recent)
                self.assertEqual(
                    budgets.conversation_summary_tokens,
                    conversation_summary,
                )

    def test_context_pack_uses_scaled_decide_budget(self) -> None:
        class _DeepSessionClient(_SessionClient):
            def get_slice(
                self, *, session_id: str, purpose: str, limits: dict[str, int]
            ) -> SessionSlice:
                del purpose, limits
                return SessionSlice(
                    session_id=session_id,
                    slice_version="slice-v1",
                    last_event_id="evt-last",
                    summary_short="summary short",
                    total_turn_count=7,
                    recent_turns=[
                        SessionTurn(turn_id="t1", role="user", content="hello"),
                    ],
                )

        service = _make_service(session_client=_DeepSessionClient())
        pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-scaled",
                agent_id="agent-1",
                purpose="decide",
                query="continue",
            )
        )

        self.assertIsNotNone(pack.token_budget_report)
        assert pack.token_budget_report is not None
        self.assertEqual(pack.token_budget_report.total_cap_tokens, 2800)
        self.assertEqual(
            pack.token_budget_report.buckets["recent_window"].cap_tokens,
            1600,
        )

    def test_decide_recent_window_protects_latest_clarify_exchange(self) -> None:
        current_reply = "you can figure all those out. no budget. free style and solo"
        trip_request = (
            "please plan trip to japan from next monday for 2 weeks. "
            "give me detail for each day plan"
        )
        clarify_response = (
            "To plan the best two-week Japan trip for you, I need some details. "
            "What are your interests, such as culture, food, nature, shopping, "
            "anime, or history? What pace do you prefer? Are there any specific "
            "places or experiences you definitely want included? " * 3
        )

        class _ClarifySessionClient(_SessionClient):
            def get_slice(
                self, *, session_id: str, purpose: str, limits: dict[str, int]
            ) -> SessionSlice:
                del purpose, limits
                return SessionSlice(
                    session_id=session_id,
                    slice_version="slice-v1",
                    last_event_id="evt-last",
                    summary_short="summary short",
                    recent_turns=[
                        SessionTurn(
                            turn_id="old-user",
                            role="user",
                            content="old context marker " * 120,
                        ),
                        SessionTurn(
                            turn_id="old-assistant",
                            role="assistant",
                            content="old answer marker " * 120,
                        ),
                        SessionTurn(
                            turn_id="trip-user",
                            role="user",
                            content=trip_request,
                        ),
                        SessionTurn(
                            turn_id="trip-assistant",
                            role="assistant",
                            content=clarify_response,
                        ),
                        SessionTurn(
                            turn_id="current-user",
                            role="user",
                            content=current_reply,
                        ),
                    ],
                )

        service = _make_service(session_client=_ClarifySessionClient())
        pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-dcbt",
                agent_id="agent-1",
                purpose="decide",
                query=current_reply,
                budgets_override=ContextBudgets(
                    total_max_tokens=5000,
                    identity_tokens=80,
                    summary_tokens=60,
                    recent_turn_tokens=80,
                    facts_tokens=20,
                    memory_tokens=20,
                    skills_tokens=20,
                    artifact_tokens=20,
                    instructions_tokens=40,
                ),
            )
        )

        rendered = "\n".join(msg.content for msg in pack.messages)
        self.assertIn(trip_request, rendered)
        self.assertIn("What are your interests", rendered)
        self.assertNotIn("old context marker", rendered)
        self.assertNotIn("old answer marker", rendered)
        user_messages = [msg.content for msg in pack.messages if msg.role == "user"]
        self.assertEqual(user_messages.count(current_reply), 1)

    def test_decide_recent_window_tail_truncates_long_assistant_turn(self) -> None:
        current_reply = "based on your last research for japan"
        long_assistant = ("HEAD_MARKER old details " * 500) + (
            "TAIL_HOTEL_ANCHOR Tokyo Shinjuku Kyoto Gion Osaka Namba "
            "Hiroshima Peace Park "
        ) * 20
        recent_hotel_question = "what's list of hotels I can choose from?"
        recent_hotel_answer = (
            "I can recommend hotels based on Tokyo, Kyoto, Hiroshima, and Osaka."
        )

        class _LongTurnSessionClient(_SessionClient):
            def get_slice(
                self, *, session_id: str, purpose: str, limits: dict[str, int]
            ) -> SessionSlice:
                del purpose, limits
                return SessionSlice(
                    session_id=session_id,
                    slice_version="slice-v1",
                    last_event_id="evt-last",
                    summary_short="summary short",
                    total_turn_count=5,
                    recent_turns=[
                        SessionTurn(
                            turn_id="trip-user",
                            role="user",
                            content="Please plan a trip to Japan for two weeks.",
                        ),
                        SessionTurn(
                            turn_id="trip-assistant",
                            role="assistant",
                            content=long_assistant,
                        ),
                        SessionTurn(
                            turn_id="hotel-user",
                            role="user",
                            content=recent_hotel_question,
                        ),
                        SessionTurn(
                            turn_id="hotel-assistant",
                            role="assistant",
                            content=recent_hotel_answer,
                        ),
                    ],
                )

        service = _make_service(session_client=_LongTurnSessionClient())
        pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-long-turn",
                agent_id="agent-1",
                purpose="decide",
                query=current_reply,
            )
        )

        rendered = "\n".join(msg.content for msg in pack.messages)
        self.assertIn("[...truncated", rendered)
        self.assertIn("TAIL_HOTEL_ANCHOR", rendered)
        self.assertNotIn("HEAD_MARKER", rendered)
        self.assertIn(recent_hotel_question, rendered)
        self.assertIn(recent_hotel_answer, rendered)

    def test_decide_recent_window_keeps_latest_assistant_turn_full(self) -> None:
        current_reply = "what total budget range did that plan imply?"
        budget_detail = "BUDGET_TABLE_ANCHOR ¥150,000 to ¥200,000"
        long_assistant = (
            "Japan itinerary opening. "
            + ("middle activity detail " * 260)
            + budget_detail
            + (" later logistics detail " * 260)
        )

        class _LatestAssistantSessionClient(_SessionClient):
            def get_slice(
                self, *, session_id: str, purpose: str, limits: dict[str, int]
            ) -> SessionSlice:
                del purpose, limits
                return SessionSlice(
                    session_id=session_id,
                    slice_version="slice-v1",
                    last_event_id="evt-last",
                    summary_short="",
                    total_turn_count=2,
                    recent_turns=[
                        SessionTurn(
                            turn_id="trip-user",
                            role="user",
                            content="Create a Japan trip planning brief.",
                        ),
                        SessionTurn(
                            turn_id="trip-assistant",
                            role="assistant",
                            content=long_assistant,
                        ),
                    ],
                )

        service = _make_service(session_client=_LatestAssistantSessionClient())
        pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-latest-assistant",
                agent_id="agent-1",
                purpose="decide",
                query=current_reply,
            )
        )

        rendered = "\n".join(msg.content for msg in pack.messages)
        self.assertIn(budget_detail, rendered)
        self.assertNotIn("[...truncated", rendered)

    def test_decide_pack_injects_dedicated_conversation_summary(self) -> None:
        class _SummarySessionClient(_SessionClient):
            def get_slice(
                self, *, session_id: str, purpose: str, limits: dict[str, int]
            ) -> SessionSlice:
                del purpose, limits
                return SessionSlice(
                    session_id=session_id,
                    slice_version="slice-v1",
                    last_event_id="evt-last",
                    summary_short="droppable generic summary",
                    conversation_summary=(
                        'turn_index=1; user_preview="Japan trip"; '
                        'route_type="act"; assistant_response_tokens=3000; '
                        'tool_families_used=["web"]; '
                        'assistant_tail_preview="Tokyo Kyoto Osaka hotels"'
                    ),
                    total_turn_count=7,
                    recent_turns=[
                        SessionTurn(
                            turn_id="old-user",
                            role="user",
                            content="old raw turn " * 500,
                        ),
                        SessionTurn(
                            turn_id="old-assistant",
                            role="assistant",
                            content="old assistant detail " * 500,
                        ),
                        SessionTurn(
                            turn_id="recent-user",
                            role="user",
                            content="what was the budget?",
                        ),
                        SessionTurn(
                            turn_id="recent-assistant",
                            role="assistant",
                            content="$1,980 to $3,280.",
                        ),
                    ],
                )

        service = _make_service(session_client=_SummarySessionClient())
        pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-summary",
                agent_id="agent-1",
                purpose="decide",
                query="what hotels did you mean?",
                budgets_override=ContextBudgets(
                    total_max_tokens=260,
                    identity_tokens=80,
                    summary_tokens=40,
                    conversation_summary_tokens=120,
                    recent_turn_tokens=120,
                    facts_tokens=20,
                    memory_tokens=20,
                    skills_tokens=20,
                    artifact_tokens=20,
                    instructions_tokens=40,
                ),
            )
        )

        summary_segments = [
            segment
            for segment in pack.segments
            if segment.bucket == "conversation_summary" and segment.content.strip()
        ]
        self.assertEqual(len(summary_segments), 1)
        self.assertIn("Tokyo Kyoto Osaka hotels", summary_segments[0].content)
        assert pack.context_manifest is not None
        self.assertNotIn(
            "conversation_summary",
            pack.context_manifest.dropped_segment_ids,
        )
        rendered = "\n".join(msg.content for msg in pack.messages)
        self.assertIn("[CONVERSATION SUMMARY]", rendered)
        self.assertNotIn("old raw turn old raw turn", rendered)

    def test_task_plan_schema_rejects_cycles_and_invalid_tool_families(self) -> None:
        valid = {
            "plan_id": "plan-1",
            "objective": "Ship the feature",
            "steps": [
                {
                    "step_id": "inspect",
                    "description": "Inspect current code",
                    "tool_families": ["file", "exec"],
                },
                {
                    "step_id": "patch",
                    "description": "Patch implementation",
                    "depends_on": ["inspect"],
                    "tool_families": ["code"],
                },
            ],
        }
        plan = TaskPlan.model_validate(valid)
        self.assertEqual(plan.steps[1].depends_on, ["inspect"])

        invalid_family = {
            **valid,
            "steps": [
                {
                    "step_id": "bad",
                    "description": "Bad family",
                    "tool_families": ["web.search"],
                }
            ],
        }
        with self.assertRaises(ValueError):
            TaskPlan.model_validate(invalid_family)

        cyclic = {
            **valid,
            "steps": [
                {
                    "step_id": "a",
                    "description": "A",
                    "depends_on": ["b"],
                },
                {
                    "step_id": "b",
                    "description": "B",
                    "depends_on": ["a"],
                },
            ],
        }
        with self.assertRaises(ValueError):
            TaskPlan.model_validate(cyclic)

    def test_decide_pack_injects_pinned_active_plan(self) -> None:
        class _PlanSessionClient(_SessionClient):
            def get_slice(
                self, *, session_id: str, purpose: str, limits: dict[str, int]
            ) -> SessionSlice:
                del purpose, limits
                return SessionSlice(
                    session_id=session_id,
                    slice_version="slice-v1",
                    last_event_id="evt-last",
                    summary_short="generic summary",
                    active_task_plan={
                        "plan_id": "plan-1",
                        "objective": "Plan the Japan trip",
                        "steps": [
                            {
                                "step_id": "research",
                                "description": "Research cities and logistics",
                                "status": "completed",
                                "tool_families": ["web"],
                                "output_summary": "Tokyo Kyoto Osaka budget done",
                            },
                            {
                                "step_id": "hotels",
                                "description": "Recommend hotels",
                                "depends_on": ["research"],
                                "tool_families": ["web", "search"],
                            },
                        ],
                    },
                    total_turn_count=9,
                    recent_turns=[
                        SessionTurn(
                            turn_id="old-user",
                            role="user",
                            content="old raw turn " * 500,
                        ),
                    ],
                )

        service = _make_service(session_client=_PlanSessionClient())
        pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-active-plan",
                agent_id="agent-1",
                purpose="decide",
                query="continue with hotels",
                budgets_override=ContextBudgets(
                    total_max_tokens=280,
                    identity_tokens=80,
                    summary_tokens=40,
                    conversation_summary_tokens=0,
                    active_plan_tokens=120,
                    recent_turn_tokens=80,
                    facts_tokens=20,
                    memory_tokens=20,
                    skills_tokens=20,
                    artifact_tokens=20,
                    instructions_tokens=40,
                ),
            )
        )

        plan_segments = [
            segment
            for segment in pack.segments
            if segment.bucket == "active_plan" and segment.content.strip()
        ]
        self.assertEqual(len(plan_segments), 1)
        self.assertTrue(plan_segments[0].pinned)
        self.assertIn("[ACTIVE PLAN]", plan_segments[0].content)
        self.assertIn("Plan the Japan trip", plan_segments[0].content)
        assert pack.context_manifest is not None
        self.assertNotIn("active_plan", pack.context_manifest.dropped_segment_ids)
        rendered = "\n".join(msg.content for msg in pack.messages)
        self.assertIn("[ACTIVE PLAN]", rendered)
        self.assertNotIn("old raw turn old raw turn", rendered)

    def test_decide_pack_injects_pinned_task_digest_before_summaries(self) -> None:
        class _TaskDigestSessionClient(_SessionClient):
            def get_slice(
                self, *, session_id: str, purpose: str, limits: dict[str, int]
            ) -> SessionSlice:
                del purpose, limits
                return SessionSlice(
                    session_id=session_id,
                    slice_version="slice-v1",
                    last_event_id="evt-last",
                    summary_short="generic summary",
                    task_digest={
                        "current_task": {
                            "task_id": "task-1",
                            "title": "Ship rollout",
                            "status": "ACTIVE",
                            "next_step_id": "step-1",
                            "next_step_title": "Run validation",
                        },
                        "tasks_active": [],
                        "tasks_ready": [],
                        "blockers": [],
                    },
                    total_turn_count=9,
                    recent_turns=[],
                )

        service = _make_service(session_client=_TaskDigestSessionClient())
        pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-task-digest",
                agent_id="agent-1",
                purpose="decide",
                query="continue rollout",
                budgets_override=ContextBudgets(
                    total_max_tokens=520,
                    identity_tokens=80,
                    summary_tokens=40,
                    conversation_summary_tokens=0,
                    active_plan_tokens=0,
                    task_digest_tokens=120,
                    recent_turn_tokens=80,
                    facts_tokens=20,
                    memory_tokens=20,
                    skills_tokens=20,
                    artifact_tokens=20,
                    instructions_tokens=40,
                ),
            )
        )

        buckets = [segment.bucket for segment in pack.segments]
        digest_segments = [
            segment
            for segment in pack.segments
            if segment.bucket == "task_digest" and segment.content.strip()
        ]
        self.assertEqual(len(digest_segments), 1)
        self.assertTrue(digest_segments[0].pinned)
        self.assertIn("[TASK DIGEST]", digest_segments[0].content)
        self.assertIn("next_step_id=step-1", digest_segments[0].content)
        self.assertLess(buckets.index("task_digest"), buckets.index("summaries"))
        assert pack.context_manifest is not None
        self.assertNotIn("task_digest", pack.context_manifest.dropped_segment_ids)
        rendered = "\n".join(msg.content for msg in pack.messages)
        self.assertIn("[TASK DIGEST]", rendered)

    def test_pack_manifest_event_is_written_and_cache_hits_are_flagged(self) -> None:
        session = _SessionClient()
        service = _make_service(session_client=session)
        request = BuildPackRequest(
            session_id="sess-4",
            agent_id="agent-1",
            purpose="act",
            query="manifest check",
        )

        pack1 = service.build_pack(request)
        pack2 = service.build_pack(request)

        self.assertEqual(pack1.pack_version, pack2.pack_version)
        manifest_events = [
            evt
            for evt in session.events
            if evt["event_type"] in {"context.manifest.created", "context.manifest"}
        ]
        self.assertEqual(len(manifest_events), 2)
        self.assertFalse(manifest_events[0]["payload"]["cache_hit"])
        self.assertTrue(manifest_events[1]["payload"]["cache_hit"])
        self.assertEqual(
            manifest_events[0]["payload"]["pack_version"], pack1.pack_version
        )

    def test_tool_schemas_are_shortlisted_and_stubbed_for_prompt(self) -> None:
        service = _make_service()
        raw_tools = [
            {
                "name": "weather.openmeteo.current",
                "description": "Get weather by city name",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"},
                        "location": {"type": "string", "description": "Alias field"},
                    },
                    "required": ["city"],
                    "additionalProperties": False,
                },
            }
        ]
        for idx in range(12):
            raw_tools.append(
                {
                    "name": f"tool_{idx}",
                    "description": f"Generic helper {idx}",
                    "parameters": {
                        "type": "object",
                        "properties": {f"arg_{idx}": {"type": "string"}},
                        "required": [f"arg_{idx}"],
                        "additionalProperties": False,
                    },
                }
            )

        pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-tools",
                agent_id="agent-1",
                purpose="decide",
                query="weather in san francisco",
                constraints=BuildConstraints(tool_schemas=raw_tools),
            )
        )

        static_seg = next(seg for seg in pack.segments if seg.id == "static_prefix")
        content = static_seg.content
        self.assertIn("[TOOL SCHEMAS]", content)
        self.assertIn('"required":["city"]', content)
        self.assertNotIn('"location"', content)

        tool_block = content.split("[TOOL SCHEMAS]\n", 1)[1]
        if "\n\n[POLICY]" in tool_block:
            tool_block = tool_block.split("\n\n[POLICY]", 1)[0]
        if "\n\n[TOOL RESULT FORMAT]" in tool_block:
            tool_block = tool_block.split("\n\n[TOOL RESULT FORMAT]", 1)[0]
        tool_lines = [
            line for line in tool_block.splitlines() if line.strip().startswith("- ")
        ]
        self.assertLessEqual(len(tool_lines), 8)
