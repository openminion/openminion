from __future__ import annotations

import asyncio
import concurrent.futures
from dataclasses import replace
from datetime import datetime
from unittest.mock import patch

from openminion.modules.task.run import resolve_thread_lifecycle
from tests.services.gateway._gateway_service_support import (
    GatewayServiceTestCase,
    SessionStore,
    _FlakyProvider,
    connect_database,
)


class InvocationStartLifecycleTests(GatewayServiceTestCase):
    def _capture(self, gateway):
        facts = []
        gateway._turn_runner._lifecycle_ops._emit_invocation_lifecycle = lambda fact: (
            facts.append(fact) or True
        )
        return facts

    def _routing(self, gateway, *, session_id: str, thread_id: str = ""):
        metadata = {"thread_id": thread_id} if thread_id else None
        return gateway._turn_runner._resolve_routing(
            channel="console",
            target="local-user",
            session_id=session_id,
            request_id=f"request-{session_id}-{thread_id or 'new'}",
            inbound_metadata=metadata,
            deliver=False,
        )

    def test_start_uses_exact_inserted_queued_event(self) -> None:
        facts = self._capture(self.gateway)
        routing = self._routing(self.gateway, session_id="start-source")

        run_id, lifecycle_payload = self.gateway._turn_runner._setup_turn(
            routing,
            channel="console",
            target="local-user",
        )

        queued = self.sessions.list_events(
            session_id=routing.session.id,
            limit=20,
            event_type_prefix="run.",
        )
        assert len(queued) == 1
        assert len(facts) == 1
        fact = facts[0]
        assert fact.event_id == (
            f"agent.invocation:{lifecycle_payload['invocation_id']}:start"
        )
        assert fact.payload["source_event_id"] == queued[0].id
        assert fact.payload["run_id"] == run_id
        assert (
            fact.timestamp == datetime.fromisoformat(queued[0].created_at).timestamp()
        )

    def test_active_resume_reuses_original_start_source(self) -> None:
        facts = self._capture(self.gateway)
        first_routing = self._routing(self.gateway, session_id="active-resume")
        _, first_payload = self.gateway._turn_runner._setup_turn(
            first_routing,
            channel="console",
            target="local-user",
        )
        first_fact = facts[-1]

        second_routing = self._routing(
            self.gateway,
            session_id="active-resume",
            thread_id=first_payload["thread_id"],
        )
        _, second_payload = self.gateway._turn_runner._setup_turn(
            second_routing,
            channel="console",
            target="local-user",
        )

        assert second_payload["invocation_id"] == first_payload["invocation_id"]
        assert facts[-1].event_id == first_fact.event_id
        assert (
            facts[-1].payload["source_event_id"]
            == first_fact.payload["source_event_id"]
        )
        assert facts[-1].timestamp == first_fact.timestamp

    def test_terminal_resume_starts_child_invocation(self) -> None:
        facts = self._capture(self.gateway)
        routing = self._routing(self.gateway, session_id="terminal-parent")
        prior_invocation = "invocation-parent"
        routing = replace(
            routing,
            lifecycle=replace(
                routing.lifecycle,
                invocation_id=prior_invocation,
                invocation_source_event_id=3,
                invocation_started_at="2026-08-11T00:00:00+00:00",
                thread_state="settled",
            ),
        )

        _, payload = self.gateway._turn_runner._setup_turn(
            routing,
            channel="console",
            target="local-user",
        )

        assert payload["invocation_id"] != prior_invocation
        assert facts[-1].payload["parent_invocation_id"] == prior_invocation

    def test_failed_turn_keeps_start_before_durable_failure(self) -> None:
        gateway, _sink = self._build_gateway(
            provider=_FlakyProvider(),
            logger_name="openminion.tests.gateway.start.failure",
            agent_logger_name="openminion.tests.gateway.start.failure.agent",
            auto_resume=False,
        )
        facts = self._capture(gateway)

        with self.assertRaises(RuntimeError):
            asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="fail",
                    session_id="pre-bind-failure",
                    deliver=True,
                )
            )

        events = self.sessions.list_events(
            session_id="pre-bind-failure",
            limit=100,
        )
        failed = [event for event in events if event.event_type == "run.failed"]
        started = [
            fact for fact in facts if fact.event_type == "agent.invocation.started"
        ]
        terminal = [
            fact for fact in facts if fact.event_type == "agent.invocation.failed"
        ]
        assert len(started) == 1
        assert len(terminal) == 1
        assert len(failed) == 1
        assert started[0].payload["source_event_id"] < failed[0].id
        assert terminal[0].payload["source_event_id"] == failed[0].id

    def test_disabled_telemetry_callback_is_nonfatal(self) -> None:
        routing = self._routing(self.gateway, session_id="callback-failure")
        self.gateway._turn_runner._lifecycle_ops._emit_invocation_lifecycle = None

        run_id, _ = self.gateway._turn_runner._setup_turn(
            routing,
            channel="console",
            target="local-user",
        )

        assert run_id

    def test_projection_pages_to_original_start(self) -> None:
        session = self.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="paged",
            session_id="paged-start",
        )
        start = self.sessions.append_event(
            session_id=session.id,
            event_type="run.queued",
            payload={
                "run_id": "run-1",
                "state": "queued",
                "thread_id": "thread-1",
                "invocation_id": "invocation-1",
            },
        )
        for index in range(2001):
            self.sessions.append_event(
                session_id=session.id,
                event_type="noise",
                payload={"index": index, "thread_id": "thread-1"},
            )

        lifecycle = resolve_thread_lifecycle(
            self.sessions,
            session_id=session.id,
            thread_id="thread-1",
        )

        assert lifecycle.invocation_id == "invocation-1"
        assert lifecycle.invocation_source_event_id == start.id

    def test_same_timestamp_appends_return_their_own_rows(self) -> None:
        session = self.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="same-time",
            session_id="same-time",
        )
        with patch(
            "openminion.modules.storage.runtime.session_store.lifecycle.utc_now_iso",
            return_value="2026-08-11T00:00:00+00:00",
        ):
            first = self.sessions.append_event(
                session_id=session.id,
                event_type="run.queued",
                payload={"run_id": "first"},
            )
            second = self.sessions.append_event(
                session_id=session.id,
                event_type="run.queued",
                payload={"run_id": "second"},
            )

        assert first.id != second.id
        assert first.payload["run_id"] == "first"
        assert second.payload["run_id"] == "second"

    def test_concurrent_appends_return_their_own_rows(self) -> None:
        session = self.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="concurrent",
            session_id="concurrent",
        )

        def _append(index: int):
            connection = connect_database(self.database_path)
            try:
                return SessionStore(connection).append_event(
                    session_id=session.id,
                    event_type="run.queued",
                    payload={"run_id": f"run-{index}"},
                )
            finally:
                connection.close()

        with patch(
            "openminion.modules.storage.runtime.session_store.lifecycle.utc_now_iso",
            return_value="2026-08-11T00:00:00+00:00",
        ):
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                rows = list(pool.map(_append, range(4)))

        assert len({row.id for row in rows}) == 4
        assert {row.payload["run_id"] for row in rows} == {
            f"run-{index}" for index in range(4)
        }

    def test_before_id_page_has_a_stable_high_water(self) -> None:
        session = self.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="high-water",
            session_id="high-water",
        )
        first = self.sessions.append_event(
            session_id=session.id,
            event_type="one",
        )
        trigger = self.sessions.append_event(
            session_id=session.id,
            event_type="trigger",
        )
        later = self.sessions.append_event(
            session_id=session.id,
            event_type="later",
        )

        page = self.sessions.list_events_before_id(
            session_id=session.id,
            before_id=trigger.id + 1,
            limit=10,
        )

        assert [event.id for event in page] == [trigger.id, first.id]
        assert later.id not in {event.id for event in page}

    def test_new_store_process_recovers_original_invocation(self) -> None:
        routing = self._routing(self.gateway, session_id="restart-source")
        _, payload = self.gateway._turn_runner._setup_turn(
            routing,
            channel="console",
            target="local-user",
        )
        connection = connect_database(self.database_path)
        try:
            lifecycle = resolve_thread_lifecycle(
                SessionStore(connection),
                session_id=routing.session.id,
                thread_id=payload["thread_id"],
            )
        finally:
            connection.close()

        assert lifecycle.invocation_id == payload["invocation_id"]
        assert lifecycle.invocation_source_event_id > 0
