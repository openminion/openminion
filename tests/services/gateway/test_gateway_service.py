from __future__ import annotations

from tests.services.gateway._gateway_service_support import (
    GatewayServiceTestCase,
    IdempotencyStore,
    Message,
    RUN_STATE_RUNNING,
    SessionStore,
    _CaptureProvider,
    _FlakyProvider,
    _QuotaThenRecoverMemoryAdapter,
    _SequenceTextProvider,
    _SlowCaptureProvider,
    _WriteFailOnceMemoryAdapter,
    append_run_state_event,
    asyncio,
    connect_database,
)


class GatewayServiceCoreTests(GatewayServiceTestCase):
    def test_gateway_threads_knowledge_graph_service_to_turn_runner(self) -> None:
        marker = object()
        gateway, _sink = self._build_gateway(
            provider=self.provider,
            logger_name="openminion.tests.gateway.knowledge_graphs",
            agent_logger_name="openminion.tests.gateway.agent.knowledge_graphs",
            knowledge_graphs=marker,
        )

        self.assertIs(getattr(gateway, "_knowledge_graphs"), marker)
        self.assertIs(getattr(gateway._turn_runner, "_knowledge_graphs"), marker)

    def test_gateway_persists_transcript_and_reuses_history(self) -> None:
        first = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="hello",
                inbound_metadata={"attach_id": "att-1"},
            )
        )
        second = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="follow up",
                inbound_metadata={"attach_id": "att-1"},
            )
        )

        self.assertIn("session_id", first.metadata)
        self.assertIn("session_id", second.metadata)
        self.assertEqual(first.metadata["session_id"], second.metadata["session_id"])
        self.assertIn("run_id", first.metadata)
        self.assertIn("run_id", second.metadata)
        self.assertEqual(first.metadata.get("run_state"), "completed")
        self.assertEqual(second.metadata.get("run_state"), "completed")
        self.assertNotEqual(first.metadata["run_id"], second.metadata["run_id"])
        self.assertEqual(len(self.provider.requests), 2)
        self.assertEqual(len(self.provider.requests[0].history), 0)
        self.assertEqual(len(self.provider.requests[1].history), 2)
        self.assertEqual(self.provider.requests[1].history[0].role, "user")
        self.assertEqual(self.provider.requests[1].history[1].role, "assistant")

        session_id = first.metadata["session_id"]
        transcript = self.sessions.list_messages(session_id=session_id, limit=10)
        self.assertEqual(
            [row.role for row in transcript],
            ["inbound", "outbound", "inbound", "outbound"],
        )
        self.assertEqual(transcript[0].body, "hello")
        self.assertEqual(transcript[2].body, "follow up")
        self.assertEqual(len(self.channel.sent), 2)

        run_events = self.sessions.list_events(
            session_id=session_id,
            limit=100,
            event_type_prefix="run.",
        )
        self.assertGreaterEqual(len(run_events), 8)
        self.assertEqual(run_events[0].payload.get("state"), "queued")
        self.assertEqual(run_events[-1].payload.get("state"), "completed")

    def test_gateway_forks_settled_thread_by_default(self) -> None:
        gateway, _sink = self._build_gateway(
            provider=self.provider,
            logger_name="openminion.tests.gateway.fork",
            agent_logger_name="openminion.tests.gateway.agent.fork",
            auto_resume=False,
        )
        session_id = "fork-default"
        first = asyncio.run(
            gateway.run_once(
                channel="console",
                target="user",
                message="hello",
                session_id=session_id,
                deliver=True,
            )
        )
        second = asyncio.run(
            gateway.run_once(
                channel="console",
                target="user",
                message="new topic",
                session_id=session_id,
                deliver=True,
            )
        )
        self.assertEqual(len(self.provider.requests), 2)
        self.assertEqual(len(self.provider.requests[-1].history), 0)
        self.assertNotEqual(
            first.metadata.get("thread_id"),
            second.metadata.get("thread_id"),
        )
        self.assertEqual(second.metadata.get("thread_decision_action"), "fork_thread")
        self.assertEqual(
            second.metadata.get("thread_decision_reason"),
            "settled_without_resume",
        )

    def test_gateway_reset_session_forks_thread_and_clears_history(self) -> None:
        gateway, _sink = self._build_gateway(
            provider=self.provider,
            logger_name="openminion.tests.gateway.reset_session",
            agent_logger_name="openminion.tests.gateway.agent.reset_session",
            auto_resume=False,
        )
        session_id = "reset-session-target"
        first = asyncio.run(
            gateway.run_once(
                channel="console",
                target="user",
                message="initial turn",
                session_id=session_id,
                deliver=True,
                inbound_metadata={"resume": "true"},
            )
        )
        second = asyncio.run(
            gateway.run_once(
                channel="console",
                target="user",
                message="post-reset turn",
                session_id=session_id,
                deliver=True,
                inbound_metadata={"reset_session": "true"},
            )
        )
        # Two provider calls, second call gets no prior-thread history.
        self.assertEqual(len(self.provider.requests), 2)
        self.assertEqual(len(self.provider.requests[-1].history), 0)
        # Fresh thread_id guaranteed by the resolver's reset branch.
        self.assertNotEqual(
            first.metadata.get("thread_id"),
            second.metadata.get("thread_id"),
        )
        # Decision surface reflects the explicit reset request, not
        # implicit settled-without-resume.
        self.assertEqual(second.metadata.get("thread_decision_action"), "fork_thread")
        self.assertEqual(
            second.metadata.get("thread_decision_reason"),
            "reset_requested",
        )

    def test_gateway_resume_reuses_settled_thread(self) -> None:
        gateway, _sink = self._build_gateway(
            provider=self.provider,
            logger_name="openminion.tests.gateway.resume",
            agent_logger_name="openminion.tests.gateway.agent.resume",
            auto_resume=False,
        )
        session_id = "resume-settled"
        first = asyncio.run(
            gateway.run_once(
                channel="console",
                target="user",
                message="hello",
                session_id=session_id,
                deliver=True,
            )
        )
        second = asyncio.run(
            gateway.run_once(
                channel="console",
                target="user",
                message="follow up",
                session_id=session_id,
                deliver=True,
                inbound_metadata={"resume": "true"},
            )
        )
        self.assertEqual(len(self.provider.requests), 2)
        self.assertGreater(len(self.provider.requests[-1].history), 0)
        self.assertEqual(
            first.metadata.get("thread_id"),
            second.metadata.get("thread_id"),
        )
        self.assertEqual(second.metadata.get("thread_decision_action"), "resume_thread")
        self.assertEqual(
            second.metadata.get("thread_decision_reason"),
            "resume_requested",
        )

    def test_gateway_does_not_rewrite_blank_slate_reply_by_regex(self) -> None:
        provider = _SequenceTextProvider(
            [
                "hi there",
                "I don't have access to any previous conversations or sessions.",
            ]
        )
        gateway, _sink = self._build_gateway(
            provider=provider,
            logger_name="openminion.tests.gateway.continuity_guard",
            agent_logger_name="openminion.tests.gateway.agent.continuity_guard",
            auto_resume=False,
        )
        session_id = "continuity-guard"

        asyncio.run(
            gateway.run_once(
                channel="console",
                target="user",
                message="hello",
                session_id=session_id,
                deliver=True,
            )
        )
        second = asyncio.run(
            gateway.run_once(
                channel="console",
                target="user",
                message="hey do you remember me?",
                session_id=session_id,
                deliver=True,
                inbound_metadata={"resume": "true"},
            )
        )

        self.assertEqual(len(provider.requests), 2)
        self.assertIn(
            "don't have access to any previous conversations",
            second.body.lower(),
        )
        self.assertNotIn("continuity_guard_applied", second.metadata)
        self.assertEqual(second.metadata.get("session_history_available"), "true")
        events = self.sessions.list_events(
            session_id=session_id,
            limit=50,
            event_type_prefix="response.continuity_guard_applied",
        )
        self.assertEqual(events, [])

    def test_gateway_preserves_same_reply_without_prior_history(
        self,
    ) -> None:
        provider = _SequenceTextProvider(
            ["I don't have access to any previous conversations or sessions."]
        )
        gateway, _sink = self._build_gateway(
            provider=provider,
            logger_name="openminion.tests.gateway.continuity_guard.new",
            agent_logger_name="openminion.tests.gateway.agent.continuity_guard.new",
            auto_resume=False,
        )
        session_id = "continuity-guard-new-session"

        first = asyncio.run(
            gateway.run_once(
                channel="console",
                target="user",
                message="do you remember me?",
                session_id=session_id,
                deliver=True,
            )
        )
        self.assertEqual(
            first.body,
            "I don't have access to any previous conversations or sessions.",
        )
        self.assertNotIn("continuity_guard_applied", first.metadata)
        self.assertEqual(first.metadata.get("session_history_available"), "false")

    def test_gateway_replays_undelivered_response(self) -> None:
        gateway, _sink = self._build_gateway(
            provider=self.provider,
            logger_name="openminion.tests.gateway.replay",
            agent_logger_name="openminion.tests.gateway.agent.replay",
            auto_resume=False,
        )
        session_id = "replay-undelivered"
        first = asyncio.run(
            gateway.run_once(
                channel="console",
                target="user",
                message="hello",
                session_id=session_id,
                deliver=False,
            )
        )
        second = asyncio.run(
            gateway.run_once(
                channel="console",
                target="user",
                message="ignored",
                session_id=session_id,
                deliver=False,
            )
        )
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(first.body, second.body)
        self.assertEqual(
            str(second.metadata.get("replayed_response", "")).lower(),
            "true",
        )
        self.assertEqual(second.metadata.get("thread_decision_action"), "replay")
        self.assertEqual(
            second.metadata.get("thread_decision_reason"),
            "undelivered_response_pending",
        )

    def test_gateway_does_not_replay_when_local_caller_handles_delivery(self) -> None:
        gateway, _sink = self._build_gateway(
            provider=self.provider,
            logger_name="openminion.tests.gateway.local_delivery",
            agent_logger_name="openminion.tests.gateway.agent.local_delivery",
            auto_resume=False,
        )
        session_id = "local-delivery"
        first = asyncio.run(
            gateway.run_once(
                channel="console",
                target="focus",
                message="hello",
                session_id=session_id,
                deliver=False,
                inbound_metadata={"caller_handles_delivery": "true"},
            )
        )
        second = asyncio.run(
            gateway.run_once(
                channel="console",
                target="focus",
                message="follow up",
                session_id=session_id,
                deliver=False,
                inbound_metadata={"caller_handles_delivery": "true"},
            )
        )
        self.assertEqual(len(self.provider.requests), 2)
        self.assertNotEqual(first.body, second.body)
        self.assertEqual(
            str(second.metadata.get("replayed_response", "")).lower(),
            "",
        )
        events = self.sessions.list_events(session_id=session_id, limit=50)
        delivered = [
            event
            for event in events
            if event.event_type == "response.delivered"
            and event.payload.get("delivery_mode") == "return"
        ]
        self.assertEqual(len(delivered), 2)

    def test_gateway_marks_stale_replay_delivered_for_local_callers(self) -> None:
        gateway, _sink = self._build_gateway(
            provider=self.provider,
            logger_name="openminion.tests.gateway.local_delivery.replay",
            agent_logger_name="openminion.tests.gateway.agent.local_delivery.replay",
            auto_resume=False,
        )
        session_id = "local-delivery-stale-replay"
        first = asyncio.run(
            gateway.run_once(
                channel="console",
                target="focus",
                message="hello",
                session_id=session_id,
                deliver=False,
            )
        )
        replay = asyncio.run(
            gateway.run_once(
                channel="console",
                target="focus",
                message="ignored",
                session_id=session_id,
                deliver=False,
                inbound_metadata={"caller_handles_delivery": "true"},
            )
        )
        third = asyncio.run(
            gateway.run_once(
                channel="console",
                target="focus",
                message="follow up",
                session_id=session_id,
                deliver=False,
                inbound_metadata={"caller_handles_delivery": "true"},
            )
        )
        self.assertEqual(first.body, replay.body)
        self.assertEqual(
            str(replay.metadata.get("replayed_response", "")).lower(),
            "true",
        )
        self.assertEqual(len(self.provider.requests), 2)
        self.assertNotEqual(replay.body, third.body)
        events = self.sessions.list_events(session_id=session_id, limit=50)
        delivered = [
            event
            for event in events
            if event.event_type == "response.delivered"
            and event.payload.get("delivery_mode") == "return"
        ]
        self.assertEqual(len(delivered), 2)

    def test_gateway_failed_run_reuses_thread(self) -> None:
        flaky = _FlakyProvider()
        gateway, _sink = self._build_gateway(
            provider=flaky,
            logger_name="openminion.tests.gateway.flaky",
            agent_logger_name="openminion.tests.gateway.flaky.agent",
            auto_resume=False,
        )
        session_id = "failed-run"
        with self.assertRaises(RuntimeError):
            asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="user",
                    message="first",
                    session_id=session_id,
                    deliver=True,
                )
            )
        second = asyncio.run(
            gateway.run_once(
                channel="console",
                target="user",
                message="retry",
                session_id=session_id,
                deliver=True,
            )
        )
        self.assertEqual(len(flaky.requests), 2)
        self.assertEqual(len(flaky.requests[1].history), 1)
        self.assertEqual(
            flaky.requests[1].history[-1].body,
            "first",
        )
        self.assertTrue(second.body.startswith("ok::"))

    def test_gateway_attach_conflict_raises(self) -> None:
        gateway, _sink = self._build_gateway(
            provider=self.provider,
            logger_name="openminion.tests.gateway.attach",
            agent_logger_name="openminion.tests.gateway.agent.attach",
            auto_resume=False,
        )
        session_id = "attach-conflict"
        session = self.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="user",
            session_id=session_id,
        )
        conversation_id = session.id
        thread_id = session.id
        self.sessions.append_event(
            session_id=session.id,
            event_type="client.attach",
            payload={
                "conversation_id": conversation_id,
                "thread_id": thread_id,
                "attach_id": "att-writer",
            },
        )
        append_run_state_event(
            self.sessions,
            session_id=session.id,
            run_id="run-attach",
            state=RUN_STATE_RUNNING,
            current_step="agent.generate",
            conversation_id=conversation_id,
            thread_id=thread_id,
        )
        with self.assertRaises(RuntimeError):
            asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="user",
                    message="hello",
                    session_id=session_id,
                    deliver=True,
                    inbound_metadata={
                        "conversation_id": conversation_id,
                        "thread_id": thread_id,
                        "attach_id": "att-other",
                    },
                )
            )

    def test_gateway_scopes_history_by_conversation_id(self) -> None:
        first = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="hello",
                session_id="session-conv",
                inbound_metadata={"conversation_id": "conv-a", "attach_id": "att-a"},
            )
        )
        second = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="follow up",
                session_id="session-conv",
                inbound_metadata={"conversation_id": "conv-a", "attach_id": "att-a"},
            )
        )
        third = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="new thread",
                session_id="session-conv",
                inbound_metadata={"conversation_id": "conv-b", "attach_id": "att-b"},
            )
        )

        self.assertEqual(first.metadata.get("session_id"), "session-conv")
        self.assertEqual(second.metadata.get("session_id"), "session-conv")
        self.assertEqual(third.metadata.get("session_id"), "session-conv")
        self.assertEqual(len(self.provider.requests), 3)
        self.assertEqual(len(self.provider.requests[0].history), 0)
        self.assertEqual(len(self.provider.requests[1].history), 2)
        self.assertEqual(len(self.provider.requests[2].history), 0)

        conv_a_messages = self.sessions.list_messages(
            session_id="session-conv",
            limit=10,
            conversation_id="conv-a",
        )
        self.assertEqual(len(conv_a_messages), 4)
        self.assertTrue(all(row.conversation_id == "conv-a" for row in conv_a_messages))

    def test_gateway_conversation_scope_skips_compaction_system_context(self) -> None:
        provider = _CaptureProvider()
        gateway, _sink = self._build_gateway(
            provider=provider,
            logger_name="openminion.tests.gateway.conv_scope",
            agent_logger_name="openminion.tests.gateway.agent.conv_scope",
            history_limit=2,
        )
        for text in ["m1", "m2", "m3"]:
            asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message=text,
                    session_id="session-conv-compact",
                    inbound_metadata={"conversation_id": "conv-compact"},
                )
            )
        latest_history = provider.requests[-1].history
        self.assertGreaterEqual(len(latest_history), 2)
        self.assertTrue(all(item.role != "system" for item in latest_history))

    def test_gateway_handle_message_without_delivery_skips_channel_send(self) -> None:
        response = asyncio.run(
            self.gateway.handle_message(
                channel="console",
                target="local-user",
                body="hello",
                deliver=False,
            )
        )

        self.assertIn("session_id", response.metadata)
        self.assertEqual(len(self.channel.sent), 0)

        transcript = self.sessions.list_messages(
            session_id=response.metadata["session_id"],
            limit=10,
        )
        self.assertEqual([row.role for row in transcript], ["inbound", "outbound"])

    def test_gateway_workspace_root_prompt_metadata_is_ephemeral(self) -> None:
        response = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="summarize this workspace",
                session_id="session-workspace-ephemeral",
                inbound_metadata={"workspace_root": "/tmp/project"},
            )
        )
        self.assertIn("session_id", response.metadata)
        self.assertEqual(len(self.provider.requests), 1)

        transcript = self.sessions.list_messages(
            session_id="session-workspace-ephemeral",
            limit=10,
        )
        self.assertGreaterEqual(len(transcript), 1)
        self.assertEqual(transcript[0].role, "inbound")
        self.assertEqual(transcript[0].body, "summarize this workspace")
        self.assertNotIn("/tmp/project", transcript[0].body)

    def test_gateway_idempotency_returns_cached_result(self) -> None:
        first = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="hello",
                idempotency_key="idem-1",
            )
        )
        second = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="hello",
                idempotency_key="idem-1",
            )
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.body, second.body)
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(len(self.channel.sent), 1)
        run_events = self.sessions.list_events(
            session_id=first.metadata["session_id"],
            limit=100,
            event_type_prefix="run.",
        )
        run_ids = {str(event.payload.get("run_id", "")) for event in run_events}
        self.assertEqual(len(run_ids), 1)

    def test_gateway_idempotency_inflight_dedupe(self) -> None:
        provider = _SlowCaptureProvider()
        gateway, sink = self._build_gateway(
            provider=provider,
            logger_name="openminion.tests.gateway.slow",
            agent_logger_name="openminion.tests.gateway.agent.slow",
        )

        async def _run_concurrent():
            return await asyncio.gather(
                gateway.handle_message(
                    channel="console",
                    target="local-user",
                    body="slow",
                    idempotency_key="idem-inflight",
                ),
                gateway.handle_message(
                    channel="console",
                    target="local-user",
                    body="slow",
                    idempotency_key="idem-inflight",
                ),
            )

        first, second = asyncio.run(_run_concurrent())
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(len(sink.sent), 1)

    def test_session_continuity_across_restart_with_explicit_session_id(self) -> None:
        explicit_session_id = "session-restart-1"
        asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="first turn",
                session_id=explicit_session_id,
            )
        )

        self.connection.close()
        reopened = connect_database(self.database_path)
        self.connection = reopened
        self.sessions = SessionStore(reopened)
        self.idempotency = IdempotencyStore(reopened)

        provider = _CaptureProvider()
        gateway, _sink = self._build_gateway(
            provider=provider,
            logger_name="openminion.tests.gateway.restart",
            agent_logger_name="openminion.tests.gateway.agent.restart",
        )

        second = asyncio.run(
            gateway.run_once(
                channel="console",
                target="local-user",
                message="second turn",
                session_id=explicit_session_id,
            )
        )
        self.assertEqual(second.metadata.get("session_id"), explicit_session_id)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(len(provider.requests[0].history), 2)
        self.assertEqual(provider.requests[0].history[0].role, "user")
        self.assertEqual(provider.requests[0].history[1].role, "assistant")

    def test_gateway_injects_compacted_session_context_when_history_compacts(
        self,
    ) -> None:
        provider = _CaptureProvider()
        gateway, _sink = self._build_gateway(
            provider=provider,
            logger_name="openminion.tests.gateway.compaction",
            agent_logger_name="openminion.tests.gateway.agent.compaction",
            history_limit=2,
        )
        for text in ["m1", "m2", "m3"]:
            asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message=text,
                    session_id="session-compaction",
                )
            )

        self.assertGreaterEqual(len(provider.requests), 3)
        latest_history = provider.requests[-1].history
        self.assertGreaterEqual(len(latest_history), 3)
        self.assertEqual(latest_history[0].role, "system")
        self.assertIn("Session context (compacted)", latest_history[0].content)


class GatewayTurnRunnerCharacterizationTests(GatewayServiceTestCase):
    def test_gateway_turn_runner_resolve_routing_replays_pending_response(self) -> None:
        gateway, _sink = self._build_gateway(
            provider=self.provider,
            logger_name="openminion.tests.gateway.runner.routing",
            agent_logger_name="openminion.tests.gateway.agent.runner.routing",
            auto_resume=False,
        )
        first = asyncio.run(
            gateway.run_once(
                channel="console",
                target="local-user",
                message="hello",
                session_id="runner-replay",
                deliver=False,
            )
        )

        routing = gateway._turn_runner._resolve_routing(
            channel="console",
            target="local-user",
            session_id="runner-replay",
            request_id="req-runner-replay",
            inbound_metadata=None,
            deliver=False,
        )

        self.assertIsNotNone(routing.early_return)
        self.assertEqual(routing.early_return.body, first.body)
        self.assertEqual(
            routing.early_return.metadata.get("thread_decision_action"),
            "replay",
        )

    def test_gateway_turn_runner_resolve_routing_skips_internal_error_replay(
        self,
    ) -> None:
        gateway, _sink = self._build_gateway(
            provider=self.provider,
            logger_name="openminion.tests.gateway.runner.routing.error",
            agent_logger_name="openminion.tests.gateway.agent.runner.routing.error",
            auto_resume=False,
        )
        session_id = "runner-error-replay"
        conversation_id = "conv-error-replay"
        thread_id = "thread-error-replay"
        self.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="local-user",
            session_id=session_id,
        )
        append_run_state_event(
            self.sessions,
            session_id=session_id,
            run_id="run-error-replay",
            state="completed",
            current_step="turn.completed",
            conversation_id=conversation_id,
            thread_id=thread_id,
        )
        self.sessions.append_message(
            session_id=session_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            role="outbound",
            body=(
                "General act work ended without the required typed "
                "finalization_status contract."
            ),
            metadata={"brain_status": "error", "finish_reason": "error"},
        )

        routing = gateway._turn_runner._resolve_routing(
            channel="console",
            target="local-user",
            session_id=session_id,
            request_id="req-runner-error-replay",
            inbound_metadata=None,
            deliver=False,
        )

        self.assertIsNone(routing.early_return)
        self.assertNotEqual(routing.routing_action, "replay")

    def test_gateway_turn_runner_setup_turn_emits_decision_and_queued_state(
        self,
    ) -> None:
        gateway, _sink = self._build_gateway(
            provider=self.provider,
            logger_name="openminion.tests.gateway.runner.setup",
            agent_logger_name="openminion.tests.gateway.agent.runner.setup",
            auto_resume=False,
        )
        routing = gateway._turn_runner._resolve_routing(
            channel="console",
            target="local-user",
            session_id="runner-setup",
            request_id="req-runner-setup",
            inbound_metadata=None,
            deliver=False,
        )

        run_id, lifecycle_payload = gateway._turn_runner._setup_turn(
            routing,
            channel="console",
            target="local-user",
        )

        self.assertEqual(lifecycle_payload.get("thread_decision_action"), "fork_thread")
        events = self.sessions.list_events(session_id=routing.session.id, limit=20)
        self.assertTrue(any(event.event_type == "thread.decision" for event in events))
        queued = [
            event
            for event in events
            if event.event_type.startswith("run.")
            and event.payload.get("state") == "queued"
        ]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0].payload.get("run_id"), run_id)

    def test_gateway_turn_runner_execute_agent_persists_inbound_and_sets_metadata(
        self,
    ) -> None:
        gateway, _sink = self._build_gateway(
            provider=self.provider,
            logger_name="openminion.tests.gateway.runner.execute",
            agent_logger_name="openminion.tests.gateway.agent.runner.execute",
            auto_resume=False,
        )

        async def _run():
            routing = gateway._turn_runner._resolve_routing(
                channel="console",
                target="local-user",
                session_id="runner-execute",
                request_id="req-runner-execute",
                inbound_metadata=None,
                deliver=False,
            )
            run_id, lifecycle_payload = gateway._turn_runner._setup_turn(
                routing,
                channel="console",
                target="local-user",
            )
            return await gateway._turn_runner._execute_agent(
                routing,
                channel="console",
                target="local-user",
                body="hello runner execute",
                run_id=run_id,
                lifecycle_payload=lifecycle_payload,
                history=[],
                forced_tools=None,
                capability_category=None,
                prior_transcript_available=False,
            )

        response = asyncio.run(_run())

        self.assertEqual(response.metadata.get("session_history_available"), "false")
        self.assertEqual(response.metadata.get("thread_decision_action"), "fork_thread")
        transcript = self.sessions.list_messages(session_id="runner-execute", limit=10)
        self.assertEqual(len(transcript), 1)
        self.assertEqual(transcript[0].role, "inbound")
        self.assertEqual(transcript[0].body, "hello runner execute")

    def test_gateway_turn_runner_build_outbound_and_persist_sets_envelope_metadata(
        self,
    ) -> None:
        gateway, _sink = self._build_gateway(
            provider=self.provider,
            logger_name="openminion.tests.gateway.runner.outbound",
            agent_logger_name="openminion.tests.gateway.agent.runner.outbound",
            auto_resume=False,
        )
        routing = gateway._turn_runner._resolve_routing(
            channel="console",
            target="local-user",
            session_id="runner-outbound",
            request_id="req-runner-outbound",
            inbound_metadata=None,
            deliver=False,
        )
        response = type(
            "_Response",
            (),
            {
                "channel": "console",
                "target": "local-user",
                "text": "hello outbound",
                "metadata": {"provider": "capture", "model": "capture-model"},
            },
        )()

        outbound, outbound_record = gateway._turn_runner._build_outbound_and_persist(
            routing,
            run_id="run-runner-outbound",
            response=response,
            memory_context_meta={
                "memory_envelope_truncated": "true",
                "memory_envelope_truncation_reasons": "capsule",
                "memory_envelope_limit_chars": "42",
            },
            memory_retrieval_meta={
                "memory_envelope_truncated": "true",
                "memory_envelope_truncation_reasons": "retrieval",
                "memory_envelope_limit_chars": "21",
            },
        )

        self.assertEqual(outbound.metadata.get("run_state"), "completed")
        self.assertEqual(outbound.metadata.get("memory_envelope_truncated"), "true")
        self.assertEqual(
            outbound.metadata.get("memory_envelope_truncation_reasons"),
            "capsule,retrieval",
        )
        self.assertEqual(outbound_record.body, "hello outbound")

    def test_gateway_memory_context_failure_emits_typed_error_and_recovers(
        self,
    ) -> None:
        adapter = _QuotaThenRecoverMemoryAdapter()
        gateway, _sink = self._build_gateway(
            provider=self.provider,
            logger_name="openminion.tests.gateway.memory.context.failure",
            agent_logger_name="openminion.tests.gateway.agent.memory.context.failure",
            agent_memory=adapter,
            auto_resume=False,
        )

        first = asyncio.run(
            gateway.run_once(
                channel="console",
                target="local-user",
                message="hello memory quota",
                session_id="memory-context-recovery",
                deliver=True,
            )
        )
        second = asyncio.run(
            gateway.run_once(
                channel="console",
                target="local-user",
                message="hello memory recovered",
                session_id="memory-context-recovery",
                deliver=True,
                inbound_metadata={"resume": "true"},
            )
        )

        self.assertEqual(
            first.metadata.get("memory_context_error_code"),
            "CONSTRAINT_VIOLATION",
        )
        self.assertEqual(
            first.metadata.get("memory_context_reason_code"),
            "memory_quota_exceeded",
        )
        self.assertNotIn("memory_context_error_code", second.metadata)
        self.assertEqual(len(self.provider.requests), 2)
        self.assertIn("hello memory recovered", second.body)

        events = self.sessions.list_events(
            session_id="memory-context-recovery",
            limit=50,
            event_type_prefix="memory.",
        )
        failed = [
            event for event in events if event.event_type == "memory.context.failed"
        ]
        built = [
            event for event in events if event.event_type == "memory.context.built"
        ]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].payload.get("error_code"), "CONSTRAINT_VIOLATION")
        self.assertEqual(failed[0].payload.get("reason_code"), "memory_quota_exceeded")
        self.assertTrue(built)

    def test_gateway_memory_write_failure_emits_typed_error_and_recovers(
        self,
    ) -> None:
        adapter = _WriteFailOnceMemoryAdapter()
        gateway, _sink = self._build_gateway(
            provider=self.provider,
            logger_name="openminion.tests.gateway.memory.write.failure",
            agent_logger_name="openminion.tests.gateway.agent.memory.write.failure",
            agent_memory=adapter,
            auto_resume=False,
        )

        first = asyncio.run(
            gateway.run_once(
                channel="console",
                target="local-user",
                message="write fail first",
                session_id="memory-write-recovery",
                deliver=True,
            )
        )
        second = asyncio.run(
            gateway.run_once(
                channel="console",
                target="local-user",
                message="write recover second",
                session_id="memory-write-recovery",
                deliver=True,
                inbound_metadata={"resume": "true"},
            )
        )

        self.assertEqual(first.metadata.get("memory_enabled"), "false")
        self.assertEqual(
            first.metadata.get("memory_write_error_code"), "STORE_WRITE_FAILED"
        )
        self.assertEqual(
            first.metadata.get("memory_write_reason_code"),
            "memory_store_unavailable",
        )
        self.assertEqual(second.metadata.get("memory_enabled"), "true")
        self.assertEqual(second.metadata.get("memory_facts_added"), "1")
        self.assertNotIn("memory_write_error_code", second.metadata)

        events = self.sessions.list_events(
            session_id="memory-write-recovery",
            limit=50,
            event_type_prefix="memory.",
        )
        write_failed = [
            event for event in events if event.event_type == "memory.write.failed"
        ]
        recorded = [
            event for event in events if event.event_type == "memory.turn.recorded"
        ]
        self.assertEqual(len(write_failed), 1)
        self.assertEqual(
            write_failed[0].payload.get("error_code"), "STORE_WRITE_FAILED"
        )
        self.assertEqual(
            write_failed[0].payload.get("reason_code"),
            "memory_store_unavailable",
        )
        self.assertTrue(recorded)

    def test_gateway_turn_runner_run_preserves_public_runtime_contract(self) -> None:
        gateway, _sink = self._build_gateway(
            provider=self.provider,
            logger_name="openminion.tests.gateway.runner.run",
            agent_logger_name="openminion.tests.gateway.agent.runner.run",
            auto_resume=False,
        )

        outbound = asyncio.run(
            gateway._turn_runner.run(
                channel="console",
                target="local-user",
                body="hello runner run",
                session_id="runner-run",
                request_id="req-runner-run",
                inbound_metadata=None,
                deliver=False,
            )
        )

        self.assertIsInstance(outbound, Message)
        self.assertEqual(outbound.body, "history=0::hello runner run")
        self.assertEqual(outbound.metadata.get("run_state"), "completed")
