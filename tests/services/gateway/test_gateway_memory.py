from __future__ import annotations

import logging

from tests.services.gateway._gateway_service_support import (
    GatewayServiceTestCase,
    Message,
    Path,
    _CaptureProvider,
    _FailingMemoryAdapter,
    _EphemeralSmokeMemoryAdapter,
    _StaticSecurityEventAgent,
    asyncio,
    create_memory_adapter,
    os,
    patch,
)

from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from openminion.services.gateway.memory import MemoryFollowupQueue, record_memory_turn


def _make_v2_memory(
    tmp_path: Path,
    suffix: str = "",
    *,
    capsule_max_chars: int = 1600,
) -> MemoryServiceGatewayAdapter:
    db_path = tmp_path / f"memory{suffix}.db"
    store = SQLiteMemoryStore(db_path)
    service = MemoryService(store=store)
    return MemoryServiceGatewayAdapter(
        service, agent_id="main", capsule_max_chars=capsule_max_chars
    )


class GatewayServiceMemoryTests(GatewayServiceTestCase):
    def test_record_memory_turn_triggers_session_summary_checkpoint_when_available(
        self,
    ) -> None:
        class _CheckpointingMemory:
            def __init__(self) -> None:
                self.called: list[str] = []

            def record_turn(self, **_kwargs):
                from types import SimpleNamespace

                return SimpleNamespace(
                    facts_added=0,
                    todos_added=0,
                    todos_completed=0,
                    patch_id="p1",
                    generation=1,
                    replayed_patches=0,
                    lock_recovered=False,
                )

            def maybe_checkpoint_session_summary(self, session_id: str) -> str | None:
                self.called.append(session_id)
                return "summary-1"

        memory = _CheckpointingMemory()

        def _emit_memory_event(**_kwargs):
            return None

        outbound_metadata: dict[str, str] = {}
        record_memory_turn(
            agent_memory=memory,
            logger=logging.getLogger(__name__),
            agent_id="main",
            memory_capsule_strategy="dynamic_turn",
            memory_capsule_cache={},
            session_id="sess-1",
            run_id="run-1",
            request_id="req-1",
            channel="console",
            target="local-user",
            user_message="hello",
            assistant_message="hi",
            conversation_id="",
            thread_id="",
            attach_id="",
            emit_memory_event=_emit_memory_event,
            outbound_metadata=outbound_metadata,
        )

        self.assertEqual(memory.called, ["sess-1"])

    def test_record_memory_turn_can_defer_capsule_and_summary_followup(self) -> None:
        class _DeferredMemory:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def record_turn(self, **_kwargs):
                from types import SimpleNamespace

                self.calls.append(("record_turn", str(_kwargs["session_id"])))
                return SimpleNamespace(
                    facts_added=1,
                    todos_added=0,
                    todos_completed=0,
                    patch_id="p1",
                    generation=1,
                    replayed_patches=0,
                    lock_recovered=False,
                )

            def build_context(self, *, session_id: str, user_message: str) -> str:
                self.calls.append(("build_context", session_id))
                del user_message
                return "updated capsule"

            def maybe_checkpoint_session_summary(self, session_id: str) -> str:
                self.calls.append(("checkpoint", session_id))
                return "summary-1"

        events: list[dict[str, object]] = []

        def _emit_memory_event(**kwargs):
            events.append(dict(kwargs))

        memory = _DeferredMemory()
        metadata: dict[str, str] = {}
        queue = MemoryFollowupQueue(auto_start=False)
        record_memory_turn(
            agent_memory=memory,
            logger=logging.getLogger(__name__),
            agent_id="main",
            memory_capsule_strategy="refresh_on_write",
            memory_capsule_cache={},
            session_id="sess-1",
            run_id="run-1",
            request_id="req-1",
            channel="console",
            target="local-user",
            user_message="remember: project codename is Orion",
            assistant_message="ok",
            conversation_id="",
            thread_id="",
            attach_id="",
            emit_memory_event=_emit_memory_event,
            outbound_metadata=metadata,
            followup_queue=queue,
            defer_followup=True,
        )

        self.assertEqual(memory.calls, [("record_turn", "sess-1")])
        self.assertEqual(metadata.get("memory_enabled"), "true")
        self.assertEqual(metadata.get("memory_capsule_refreshed"), "pending")
        self.assertEqual(metadata.get("memory_followup_deferred"), "true")
        self.assertEqual(queue.pending_count(session_id="sess-1"), 1)
        self.assertIn(
            "memory.followup.pending",
            [str(event.get("event_type")) for event in events],
        )

        queue.flush(session_id="sess-1")

        self.assertEqual(
            memory.calls,
            [
                ("record_turn", "sess-1"),
                ("build_context", "sess-1"),
                ("checkpoint", "sess-1"),
            ],
        )
        self.assertEqual(metadata.get("memory_capsule_refreshed"), "true")
        self.assertIn(
            "memory.followup.completed",
            [str(event.get("event_type")) for event in events],
        )

    def test_gateway_injects_agent_memory_across_sessions(self) -> None:
        memory_service = _make_v2_memory(Path(self._tmp.name), "-inject")
        provider = _CaptureProvider()
        gateway, _sink = self._build_gateway(
            provider=provider,
            logger_name="openminion.tests.gateway.agent_memory",
            agent_logger_name="openminion.tests.gateway.agent.agent_memory",
            history_limit=2,
            agent_memory=memory_service,
        )
        asyncio.run(
            gateway.run_once(
                channel="console",
                target="local-user",
                message="remember: project codename is Orion",
                session_id="memory-s1",
            )
        )
        asyncio.run(
            gateway.run_once(
                channel="console",
                target="local-user",
                message="what is the codename?",
                session_id="memory-s2",
            )
        )
        latest_history = provider.requests[-1].history
        self.assertGreaterEqual(len(latest_history), 1)
        self.assertEqual(latest_history[0].role, "system")
        # V2 uses "## Agent Memory" section header (not V1's "Agent canonical memory")
        self.assertIn("## Agent Memory", latest_history[0].content)
        self.assertIn("project codename is Orion", latest_history[0].content)

    def test_gateway_cross_session_memory_visibility_strategy_matrix(self) -> None:
        for strategy in ("dynamic_turn", "frozen_session", "refresh_on_write"):
            memory_service = _make_v2_memory(
                Path(self._tmp.name), f"-cross-session-{strategy}"
            )
            provider = _CaptureProvider()
            with patch.dict(
                os.environ,
                {"OPENMINION_MEMORY_CAPSULE_STRATEGY": strategy},
                clear=False,
            ):
                gateway, _sink = self._build_gateway(
                    provider=provider,
                    logger_name=f"openminion.tests.gateway.memory_cross_session.{strategy}",
                    agent_logger_name=f"openminion.tests.gateway.agent.memory_cross_session.{strategy}",
                    history_limit=2,
                    agent_memory=memory_service,
                )
                asyncio.run(
                    gateway.run_once(
                        channel="console",
                        target="local-user",
                        message="remember: team alias is Nebula",
                        session_id=f"cross-session-a-{strategy}",
                    )
                )
                asyncio.run(
                    gateway.run_once(
                        channel="console",
                        target="local-user",
                        message="what is team alias?",
                        session_id=f"cross-session-b-{strategy}",
                    )
                )

            latest_history = provider.requests[-1].history
            self.assertGreaterEqual(len(latest_history), 1)
            self.assertEqual(latest_history[0].role, "system")
            # V2 agent-scope promotion via "remember:" prefix makes fact visible cross-session
            self.assertIn("## Agent Memory", latest_history[0].content)
            self.assertIn("team alias is Nebula", latest_history[0].content)

    def test_gateway_memory_strategy_matrix_with_memctl_contract_adapter(self) -> None:
        for strategy in ("dynamic_turn", "frozen_session", "refresh_on_write"):
            provider = _CaptureProvider()
            memory_api = create_memory_adapter(
                mode="auto",
                db_path=Path(self._tmp.name) / f"memctl-memory-{strategy}",
            )
            with patch.dict(
                os.environ,
                {
                    "OPENMINION_MEMORY_CAPSULE_STRATEGY": strategy,
                    "OPENMINION_MEMORY_DYNAMIC_RETRIEVAL_ENABLED": "false",
                },
                clear=False,
            ):
                gateway, _sink = self._build_gateway(
                    provider=provider,
                    logger_name=f"openminion.tests.gateway.memory_memctl.{strategy}",
                    agent_logger_name=f"openminion.tests.gateway.agent.memory_memctl.{strategy}",
                    history_limit=2,
                    agent_memory=memory_api,  # type: ignore[arg-type]
                )
                session_id = f"memctl-strategy-{strategy}"
                turn1 = asyncio.run(
                    gateway.run_once(
                        channel="console",
                        target="local-user",
                        message="hello",
                        session_id=session_id,
                    )
                )
                turn2 = asyncio.run(
                    gateway.run_once(
                        channel="console",
                        target="local-user",
                        message="remember: memctl codename is Orion",
                        session_id=session_id,
                    )
                )
                turn3 = asyncio.run(
                    gateway.run_once(
                        channel="console",
                        target="local-user",
                        message="what is the codename?",
                        session_id=session_id,
                    )
                )

            self.assertEqual(turn1.metadata.get("memory_enabled"), "true")
            self.assertEqual(turn2.metadata.get("memory_enabled"), "true")
            self.assertEqual(turn3.metadata.get("memory_enabled"), "true")
            self.assertGreaterEqual(len(provider.requests[-1].history), 1)
            final_capsule = provider.requests[-1].history[0].content
            if strategy == "frozen_session":
                self.assertNotIn("memctl codename is Orion", final_capsule)
            else:
                self.assertIn("memctl codename is Orion", final_capsule)
            if strategy == "refresh_on_write":
                self.assertIn(
                    turn2.metadata.get("memory_capsule_refreshed"),
                    ("pending", "true"),
                )

    def test_gateway_merges_memory_with_compacted_session_context(self) -> None:
        memory_service = _make_v2_memory(Path(self._tmp.name), "-merged")
        provider = _CaptureProvider()
        gateway, _sink = self._build_gateway(
            provider=provider,
            logger_name="openminion.tests.gateway.memory_context_merged",
            agent_logger_name="openminion.tests.gateway.agent.memory_context_merged",
            history_limit=2,
            agent_memory=memory_service,
        )
        for text in [
            "remember: project codename is Orion",
            "acknowledged",
            "what is the codename?",
        ]:
            asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message=text,
                    session_id="session-memory-context-merged",
                )
            )

        latest_history = provider.requests[-1].history
        system_messages = [item for item in latest_history if item.role == "system"]
        self.assertEqual(len(system_messages), 1)
        self.assertIn("## Agent Memory", system_messages[0].content)
        self.assertIn("Session context (compacted)", system_messages[0].content)

    def test_gateway_can_freeze_memory_context_within_session(self) -> None:
        memory_service = _make_v2_memory(Path(self._tmp.name), "-frozen")
        memory_service.record_turn(
            session_id="bootstrap",
            run_id="r1",
            request_id="q1",
            channel="console",
            target="local-user",
            user_message="remember: favorite color is blue",
            assistant_message="ok",
        )
        provider = _CaptureProvider()
        with patch.dict(
            os.environ,
            {"OPENMINION_MEMORY_CAPSULE_STRATEGY": "frozen_session"},
            clear=False,
        ):
            gateway, _sink = self._build_gateway(
                provider=provider,
                logger_name="openminion.tests.gateway.memory_frozen",
                agent_logger_name="openminion.tests.gateway.agent.memory_frozen",
                history_limit=2,
                agent_memory=memory_service,
            )
            asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="hello",
                    session_id="frozen-session-1",
                )
            )
            first_history = provider.requests[-1].history
            self.assertGreaterEqual(len(first_history), 1)
            self.assertEqual(first_history[0].role, "system")
            first_capsule = first_history[0].content
            self.assertIn("favorite color is blue", first_capsule)

            asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="remember: project codename is Orion",
                    session_id="frozen-session-1",
                )
            )
            second_history = provider.requests[-1].history
            self.assertGreaterEqual(len(second_history), 1)
            self.assertEqual(second_history[0].role, "system")
            second_capsule = second_history[0].content
            self.assertEqual(first_capsule, second_capsule)
            self.assertNotIn("project codename is Orion", second_capsule)

            asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="what is the codename?",
                    session_id="frozen-session-2",
                )
            )
            third_history = provider.requests[-1].history
            self.assertGreaterEqual(len(third_history), 1)
            self.assertEqual(third_history[0].role, "system")
            third_capsule = third_history[0].content
            self.assertIn("project codename is Orion", third_capsule)

    def test_gateway_refresh_on_write_updates_capsule_within_session(self) -> None:
        memory_service = _make_v2_memory(Path(self._tmp.name), "-refresh-on-write")
        memory_service.record_turn(
            session_id="bootstrap",
            run_id="r1",
            request_id="q1",
            channel="console",
            target="local-user",
            user_message="remember: favorite color is blue",
            assistant_message="ok",
        )
        provider = _CaptureProvider()
        with patch.dict(
            os.environ,
            {"OPENMINION_MEMORY_CAPSULE_STRATEGY": "refresh_on_write"},
            clear=False,
        ):
            gateway, _sink = self._build_gateway(
                provider=provider,
                logger_name="openminion.tests.gateway.memory_refresh_on_write",
                agent_logger_name="openminion.tests.gateway.agent.memory_refresh_on_write",
                history_limit=2,
                agent_memory=memory_service,
            )
            asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="hello",
                    session_id="refresh-session-1",
                )
            )
            first_capsule = provider.requests[-1].history[0].content
            self.assertIn("favorite color is blue", first_capsule)
            self.assertNotIn("project codename is Orion", first_capsule)

            turn2 = asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="remember: project codename is Orion",
                    session_id="refresh-session-1",
                )
            )
            self.assertEqual(turn2.metadata.get("memory_capsule_refreshed"), "pending")

            asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="what is the codename?",
                    session_id="refresh-session-1",
                )
            )
            third_capsule = provider.requests[-1].history[0].content
            self.assertIn("favorite color is blue", third_capsule)
            self.assertIn("project codename is Orion", third_capsule)
            self.assertNotEqual(first_capsule, third_capsule)

    def test_gateway_can_append_dynamic_memory_retrieval_when_enabled(self) -> None:
        memory_service = _make_v2_memory(Path(self._tmp.name), "-dynamic-retrieval")
        memory_service.record_turn(
            session_id="bootstrap",
            run_id="r1",
            request_id="q1",
            channel="console",
            target="local-user",
            user_message="remember: project codename is Orion",
            assistant_message="ok",
        )
        memory_service.record_turn(
            session_id="bootstrap",
            run_id="r2",
            request_id="q2",
            channel="console",
            target="local-user",
            user_message="remember: favorite color is blue",
            assistant_message="ok",
        )
        provider = _CaptureProvider()
        with patch.dict(
            os.environ,
            {
                "OPENMINION_MEMORY_CAPSULE_STRATEGY": "frozen_session",
                "OPENMINION_MEMORY_DYNAMIC_RETRIEVAL_ENABLED": "true",
            },
            clear=False,
        ):
            gateway, _sink = self._build_gateway(
                provider=provider,
                logger_name="openminion.tests.gateway.memory_dynamic_retrieval",
                agent_logger_name="openminion.tests.gateway.agent.memory_dynamic_retrieval",
                history_limit=2,
                agent_memory=memory_service,
            )
            asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="what is the codename?",
                    session_id="dynamic-retrieval-session",
                )
            )
            first_history = provider.requests[-1].history
            system_messages = [item for item in first_history if item.role == "system"]
            self.assertEqual(len(system_messages), 2)
            capsule_1 = system_messages[0].content
            retrieval_1 = system_messages[1].content
            # V2 capsule uses "## Agent Memory" header; retrieval uses "## Memory (dynamic retrieval)"
            self.assertIn("## Agent Memory", capsule_1)
            self.assertIn("## Memory (dynamic retrieval)", retrieval_1)
            self.assertIn("project codename is Orion", retrieval_1)

            asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="what is the color?",
                    session_id="dynamic-retrieval-session",
                )
            )
            second_history = provider.requests[-1].history
            system_messages = [item for item in second_history if item.role == "system"]
            self.assertEqual(len(system_messages), 2)
            capsule_2 = system_messages[0].content
            retrieval_2 = system_messages[1].content
            self.assertEqual(capsule_1, capsule_2)
            self.assertNotEqual(retrieval_1, retrieval_2)
            self.assertIn("favorite color is blue", retrieval_2)

    def test_gateway_memory_strategy_matrix_regression_e2e(self) -> None:
        scenarios = [
            ("dynamic_turn", True),
            ("frozen_session", False),
            ("refresh_on_write", True),
        ]
        for strategy, expect_same_session_visibility in scenarios:
            memory_service = _make_v2_memory(
                Path(self._tmp.name), f"-matrix-{strategy}"
            )
            memory_service.record_turn(
                session_id="bootstrap",
                run_id="r1",
                request_id="q1",
                channel="console",
                target="local-user",
                user_message="remember: favorite color is blue",
                assistant_message="ok",
            )
            provider = _CaptureProvider()
            with patch.dict(
                os.environ,
                {"OPENMINION_MEMORY_CAPSULE_STRATEGY": strategy},
                clear=False,
            ):
                gateway, _sink = self._build_gateway(
                    provider=provider,
                    logger_name=f"openminion.tests.gateway.memory_matrix.{strategy}",
                    agent_logger_name=f"openminion.tests.gateway.agent.memory_matrix.{strategy}",
                    history_limit=2,
                    agent_memory=memory_service,
                )
                turn1 = asyncio.run(
                    gateway.run_once(
                        channel="console",
                        target="local-user",
                        message="hello",
                        session_id=f"matrix-session-{strategy}",
                    )
                )
                turn2 = asyncio.run(
                    gateway.run_once(
                        channel="console",
                        target="local-user",
                        message="remember: project codename is Orion",
                        session_id=f"matrix-session-{strategy}",
                    )
                )
                turn3 = asyncio.run(
                    gateway.run_once(
                        channel="console",
                        target="local-user",
                        message="what is the codename?",
                        session_id=f"matrix-session-{strategy}",
                    )
                )

            self.assertEqual(turn1.metadata.get("memory_enabled"), "true")
            self.assertEqual(turn2.metadata.get("memory_enabled"), "true")
            self.assertEqual(turn3.metadata.get("memory_enabled"), "true")
            self.assertEqual(len(provider.requests), 3)
            self.assertGreaterEqual(len(provider.requests[-1].history), 1)
            self.assertEqual(provider.requests[-1].history[0].role, "system")
            final_capsule = provider.requests[-1].history[0].content

            if expect_same_session_visibility:
                self.assertIn("project codename is Orion", final_capsule)
            else:
                self.assertNotIn("project codename is Orion", final_capsule)

            if strategy == "refresh_on_write":
                self.assertIn(
                    turn2.metadata.get("memory_capsule_refreshed"),
                    ("pending", "true"),
                )
            else:
                self.assertIn(
                    turn2.metadata.get("memory_capsule_refreshed"),
                    (None, "false"),
                )

    def test_gateway_memory_trace_events_capture_before_after_refresh(self) -> None:
        memory_service = _make_v2_memory(Path(self._tmp.name), "-trace-events")
        provider = _CaptureProvider()
        with patch.dict(
            os.environ,
            {
                "OPENMINION_MEMORY_CAPSULE_STRATEGY": "refresh_on_write",
                "OPENMINION_MEMORY_DYNAMIC_RETRIEVAL_ENABLED": "true",
            },
            clear=False,
        ):
            gateway, _sink = self._build_gateway(
                provider=provider,
                logger_name="openminion.tests.gateway.memory_trace_events",
                agent_logger_name="openminion.tests.gateway.agent.memory_trace_events",
                history_limit=2,
                agent_memory=memory_service,
            )
            session_id = "memory-trace-session"
            asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="hello",
                    session_id=session_id,
                )
            )
            asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="remember: project codename is Orion",
                    session_id=session_id,
                )
            )
            gateway.flush_memory_followups(session_id=session_id)

        events = self.sessions.list_events(session_id=session_id, limit=200)
        event_types = [event.event_type for event in events]
        self.assertIn("memory.context.built", event_types)
        self.assertIn("memory.retrieval.built", event_types)
        self.assertIn("memory.turn.recorded", event_types)
        self.assertIn("memory.capsule.refresh_skipped", event_types)
        self.assertIn("memory.capsule.refreshed", event_types)

        record_events = [
            event for event in events if event.event_type == "memory.turn.recorded"
        ]
        self.assertGreaterEqual(len(record_events), 2)
        self.assertEqual(record_events[0].payload.get("changed"), "false")
        self.assertEqual(record_events[-1].payload.get("changed"), "true")

        refresh_payload = [
            event.payload
            for event in events
            if event.event_type == "memory.capsule.refreshed"
        ][-1]
        self.assertEqual(refresh_payload.get("reason"), "on_write")
        self.assertEqual(refresh_payload.get("changed"), "true")
        self.assertNotEqual(refresh_payload.get("after_fingerprint"), "")
        self.assertNotEqual(
            refresh_payload.get("before_fingerprint"),
            refresh_payload.get("after_fingerprint"),
        )

    def test_gateway_memory_envelope_metadata_is_emitted_when_caps_apply(self) -> None:
        # V2 uses char-based truncation (not count-based caps); create adapter with small budget
        db_path = Path(self._tmp.name) / "memory-envelope-caps.db"
        store = SQLiteMemoryStore(db_path)
        service = MemoryService(store=store)
        memory_service = MemoryServiceGatewayAdapter(
            service, agent_id="main", capsule_max_chars=256
        )

        # Seed enough facts in the query session to exceed 256-char capsule limit.
        # Writing to session_id="memory-envelope-session" fills both session and agent scopes
        # (via "remember:" prefix), so both capsule sections are populated and jointly overflow.
        seed_lines = [
            f"remember: envelope-capsule-fact-{idx}-with-extra-padding"
            for idx in range(8)
        ]
        memory_service.record_turn(
            session_id="memory-envelope-session",
            run_id="seed-run",
            request_id="seed-req",
            channel="console",
            target="local-user",
            user_message="\n".join(seed_lines),
            assistant_message="stored",
        )

        provider = _CaptureProvider()
        gateway, _sink = self._build_gateway(
            provider=provider,
            logger_name="openminion.tests.gateway.memory_envelope_caps",
            agent_logger_name="openminion.tests.gateway.agent.memory_envelope_caps",
            agent_memory=memory_service,
        )
        session_id = "memory-envelope-session"
        response = asyncio.run(
            gateway.run_once(
                channel="console",
                target="local-user",
                message="what do we remember about envelope facts?",
                session_id=session_id,
            )
        )

        self.assertEqual(response.metadata.get("memory_envelope_truncated"), "true")
        reasons = str(
            response.metadata.get("memory_envelope_truncation_reasons", "") or ""
        )
        # V2 truncation reason is "capsule_limit" (char budget exceeded)
        self.assertIn("capsule_limit", reasons)

        events = self.sessions.list_events(session_id=session_id, limit=100)
        context_events = [
            event for event in events if event.event_type == "memory.context.built"
        ]
        self.assertGreaterEqual(len(context_events), 1)
        payload = context_events[-1].payload
        self.assertEqual(payload.get("envelope_truncated"), "true")
        self.assertIn("capsule_limit", str(payload.get("envelope_reasons", "")))

    def test_gateway_memory_record_event_has_patch_id_and_run_id_correlation(
        self,
    ) -> None:
        memory_service = _make_v2_memory(Path(self._tmp.name), "-correlation")
        provider = _CaptureProvider()
        gateway, _sink = self._build_gateway(
            provider=provider,
            logger_name="openminion.tests.gateway.memory_correlation",
            agent_logger_name="openminion.tests.gateway.agent.memory_correlation",
            agent_memory=memory_service,
        )
        session_id = "memory-correlation-session"
        response = asyncio.run(
            gateway.run_once(
                channel="console",
                target="local-user",
                message="remember: correlation fact",
                session_id=session_id,
            )
        )

        patch_id = str(response.metadata.get("memory_patch_id", "") or "")
        # V2 patch_id is a 12-char lowercase hex SHA1 digest (no "patch-" prefix)
        self.assertEqual(len(patch_id), 12)
        self.assertRegex(patch_id, r"^[0-9a-f]+$")

        events = self.sessions.list_events(session_id=session_id, limit=200)
        record_events = [
            event for event in events if event.event_type == "memory.turn.recorded"
        ]
        self.assertEqual(len(record_events), 1)
        record_payload = record_events[0].payload
        self.assertEqual(record_payload.get("patch_id"), patch_id)
        run_id = str(record_payload.get("run_id", "") or "")
        request_id = str(record_payload.get("request_id", "") or "")
        self.assertNotEqual(run_id, "")
        self.assertNotEqual(request_id, "")

        write_started = [
            event for event in events if event.event_type == "memory.write.started"
        ]
        write_completed = [
            event for event in events if event.event_type == "memory.write.completed"
        ]
        self.assertEqual(len(write_started), 1)
        self.assertEqual(len(write_completed), 1)
        self.assertEqual(write_started[0].payload.get("patch_id"), patch_id)
        self.assertEqual(write_completed[0].payload.get("patch_id"), patch_id)
        # V2 generation counter starts at 0 and is incremented before return; first call = 1
        self.assertEqual(write_completed[0].payload.get("generation"), "1")

        run_events = [
            event
            for event in events
            if event.event_type.startswith("run.")
            and event.payload.get("run_id") == run_id
            and event.payload.get("request_id") == request_id
        ]
        self.assertGreaterEqual(len(run_events), 1)

    def test_gateway_emits_memory_write_failed_event(self) -> None:
        provider = _CaptureProvider()
        gateway, _sink = self._build_gateway(
            provider=provider,
            logger_name="openminion.tests.gateway.memory_write_failed",
            agent_logger_name="openminion.tests.gateway.agent.memory_write_failed",
            agent_memory=_FailingMemoryAdapter(),  # type: ignore[arg-type]
        )
        session_id = "memory-write-failed-session"
        response = asyncio.run(
            gateway.run_once(
                channel="console",
                target="local-user",
                message="remember: this will fail",
                session_id=session_id,
            )
        )

        self.assertEqual(response.metadata.get("memory_enabled"), "false")
        events = self.sessions.list_events(session_id=session_id, limit=200)
        write_started = [
            event for event in events if event.event_type == "memory.write.started"
        ]
        write_failed = [
            event for event in events if event.event_type == "memory.write.failed"
        ]
        self.assertEqual(len(write_started), 1)
        self.assertEqual(len(write_failed), 1)
        self.assertIn(
            "simulated memory write failure", write_failed[0].payload.get("error", "")
        )

    def test_gateway_emits_memory_policy_snapshot_event_from_agent_metadata(
        self,
    ) -> None:
        agent = _StaticSecurityEventAgent(
            {
                "memory_policy_route": "runtime_policy_snapshot",
                "memory_policy_source": "runtime.config",
                "memory_policy_version": "memory_policy_snapshot.v1",
                "reason_code": "memory_policy_snapshot",
            }
        )
        gateway, _sink = self._build_gateway(
            agent=agent,
            logger_name="openminion.tests.gateway.memory_policy_snapshot",
            agent_logger_name="openminion.tests.gateway.agent.memory_policy_snapshot",
        )
        session_id = "memory-policy-snapshot-session"
        response = asyncio.run(
            gateway.run_once(
                channel="console",
                target="local-user",
                message="what is your memory policy?",
                session_id=session_id,
            )
        )

        self.assertEqual(
            response.metadata.get("memory_policy_route"), "runtime_policy_snapshot"
        )
        self.assertEqual(
            response.metadata.get("memory_policy_source"), "runtime.config"
        )
        self.assertEqual(
            response.metadata.get("memory_policy_version"),
            "memory_policy_snapshot.v1",
        )

        events = self.sessions.list_events(session_id=session_id, limit=50)
        policy_events = [
            event for event in events if event.event_type == "memory.policy.snapshot"
        ]
        self.assertEqual(len(policy_events), 1)
        payload = policy_events[0].payload
        self.assertEqual(payload.get("route"), "runtime_policy_snapshot")
        self.assertEqual(payload.get("source"), "runtime.config")
        self.assertEqual(payload.get("version"), "memory_policy_snapshot.v1")
        self.assertEqual(payload.get("reason_code"), "memory_policy_snapshot")
        self.assertNotEqual(str(payload.get("run_id", "")).strip(), "")

    def test_gateway_memory_v2_smoke_swap_smoke(self) -> None:
        provider = _CaptureProvider()
        memory_v2 = _EphemeralSmokeMemoryAdapter()
        with patch.dict(
            os.environ,
            {"OPENMINION_MEMORY_CAPSULE_STRATEGY": "dynamic_turn"},
            clear=False,
        ):
            gateway, _sink = self._build_gateway(
                provider=provider,
                logger_name="openminion.tests.gateway.memory_v2_swap_smoke",
                agent_logger_name="openminion.tests.gateway.agent.memory_v2_swap_smoke",
                history_limit=2,
                agent_memory=memory_v2,  # type: ignore[arg-type]
            )
            response = asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="hello from swap smoke",
                    session_id="memory-v2-swap-smoke",
                )
            )

        self.assertEqual(response.metadata.get("memory_enabled"), "true")
        self.assertEqual(len(provider.requests), 1)
        history = provider.requests[0].history
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[0].role, "system")
        self.assertIn("ephemeral-memory-smoke is active", history[0].content)
        self.assertTrue(any(name == "record_turn" for name, _ in memory_v2.calls))

    def test_gateway_memory_v2_smoke_swap_keeps_memory_trace_signals(
        self,
    ) -> None:
        provider = _CaptureProvider()
        memory_v2 = _EphemeralSmokeMemoryAdapter()
        session_id = "memory-v2-trace-swap"
        with patch.dict(
            os.environ,
            {"OPENMINION_MEMORY_CAPSULE_STRATEGY": "dynamic_turn"},
            clear=False,
        ):
            gateway, _sink = self._build_gateway(
                provider=provider,
                logger_name="openminion.tests.gateway.memory_v2_swap_trace",
                agent_logger_name="openminion.tests.gateway.agent.memory_v2_swap_trace",
                history_limit=2,
                agent_memory=memory_v2,  # type: ignore[arg-type]
            )
            response = asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="remember: parity fact from smoke memory",
                    session_id=session_id,
                )
            )

        self.assertEqual(response.metadata.get("memory_enabled"), "true")
        events = self.sessions.list_events(session_id=session_id, limit=100)
        event_types = [event.event_type for event in events]
        self.assertIn("memory.context.built", event_types)
        self.assertIn("memory.write.started", event_types)
        self.assertIn("memory.turn.recorded", event_types)
        context_event = [
            event for event in events if event.event_type == "memory.context.built"
        ][-1]
        self.assertEqual(context_event.payload.get("strategy"), "dynamic_turn")
        self.assertNotEqual(str(context_event.payload.get("capsule_chars", "0")), "0")

    def test_gateway_turn_runner_build_memory_context_returns_turn_context(
        self,
    ) -> None:
        memory_service = _make_v2_memory(Path(self._tmp.name), "-runner-context")
        memory_service.record_turn(
            session_id="bootstrap",
            run_id="bootstrap-run",
            request_id="bootstrap-request",
            channel="console",
            target="local-user",
            user_message="remember: runner context fact",
            assistant_message="ok",
        )
        provider = _CaptureProvider()
        with patch.dict(
            os.environ,
            {
                "OPENMINION_MEMORY_CAPSULE_STRATEGY": "dynamic_turn",
                "OPENMINION_MEMORY_DYNAMIC_RETRIEVAL_ENABLED": "false",
            },
            clear=False,
        ):
            gateway, _sink = self._build_gateway(
                provider=provider,
                logger_name="openminion.tests.gateway.runner.memory_context",
                agent_logger_name="openminion.tests.gateway.agent.runner.memory_context",
                history_limit=2,
                agent_memory=memory_service,
                auto_resume=False,
            )
            routing = gateway._turn_runner._resolve_routing(
                channel="console",
                target="local-user",
                session_id="runner-memory-context",
                request_id="req-runner-memory-context",
                inbound_metadata=None,
                deliver=False,
            )
            turn_context = gateway._turn_runner._build_memory_context(
                routing,
                channel="console",
                target="local-user",
                body="remember: direct runner context",
                run_id="run-runner-memory-context",
                history=[],
            )

        self.assertFalse(turn_context.prior_transcript_available)
        self.assertEqual(turn_context.memory_strategy, "dynamic_turn")
        self.assertIn("## Agent Memory", turn_context.memory_context)
        self.assertGreaterEqual(len(turn_context.history), 1)
        self.assertEqual(turn_context.history[0].metadata.get("role"), "system")

    def test_gateway_turn_runner_write_turn_memory_updates_metadata_and_events(
        self,
    ) -> None:
        memory_service = _make_v2_memory(Path(self._tmp.name), "-runner-write")
        provider = _CaptureProvider()
        gateway, _sink = self._build_gateway(
            provider=provider,
            logger_name="openminion.tests.gateway.runner.memory_write",
            agent_logger_name="openminion.tests.gateway.agent.runner.memory_write",
            history_limit=2,
            agent_memory=memory_service,
            auto_resume=False,
        )
        routing = gateway._turn_runner._resolve_routing(
            channel="console",
            target="local-user",
            session_id="runner-memory-write",
            request_id="req-runner-memory-write",
            inbound_metadata=None,
            deliver=False,
        )
        outbound = Message(
            channel="console",
            target="local-user",
            body="reply from runner memory write",
            metadata={},
        )

        gateway._turn_runner._write_turn_memory(
            routing,
            channel="console",
            target="local-user",
            body="remember: runner write fact",
            run_id="run-runner-memory-write",
            outbound=outbound,
        )

        self.assertEqual(outbound.metadata.get("memory_enabled"), "true")
        self.assertNotEqual(
            str(outbound.metadata.get("memory_patch_id", "")).strip(), ""
        )
        events = self.sessions.list_events(session_id="runner-memory-write", limit=100)
        event_types = [event.event_type for event in events]
        self.assertIn("memory.write.started", event_types)
        self.assertIn("memory.turn.recorded", event_types)
