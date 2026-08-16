from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import get_type_hints

from openminion.modules.telemetry.interfaces import TelemetryAdapterContract
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService
from openminion.modules.telemetry.invocation_repair import (
    InvocationLifecycleReconciler,
)
from openminion.services.agent import AgentService

from tests.services.gateway._gateway_service_support import GatewayServiceTestCase


class InvocationLifecycleReconciliationTests(GatewayServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.telemetry_service = TelemetryService(home_root=Path(self._tmp.name))
        self.telemetryctl = TelemetryCtl(self.telemetry_service)
        self.gateway._agent._telemetryctl = self.telemetryctl

    def tearDown(self) -> None:
        self.telemetry_service.close_sync()
        super().tearDown()

    def _reconciler(self) -> InvocationLifecycleReconciler:
        return InvocationLifecycleReconciler.for_runtime(
            sessions=self.sessions,
            telemetryctl=self.telemetryctl,
        )

    def test_agent_service_exposes_repair_capable_telemetry_contract(self) -> None:
        getter = AgentService.telemetry_contract.fget
        assert getter is not None
        assert get_type_hints(getter)["return"] == TelemetryAdapterContract | None
        assert self.gateway._agent.telemetry_contract is self.telemetryctl
        assert callable(
            self.gateway._agent.telemetry_contract.repair_canonical_event_sync
        )

    def test_repairs_missing_terminal_once(self) -> None:
        original = self.gateway._turn_runner._lifecycle_ops._emit_invocation_lifecycle

        def _drop_terminal(fact):  # noqa: ANN001
            if fact.event_type != "agent.invocation.started":
                return False
            return original(fact)

        self.gateway._turn_runner._lifecycle_ops._emit_invocation_lifecycle = (
            _drop_terminal
        )
        response = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="hello",
                session_id="repair-missing-terminal",
                deliver=True,
            )
        )
        invocation_id = response.metadata["invocation_id"]

        first = self._reconciler().repair_session("repair-missing-terminal")
        second = self._reconciler().repair_session("repair-missing-terminal")
        events = asyncio.run(
            self.telemetry_service.get_invocation_events(invocation_id)
        )

        assert first.status == "repaired"
        assert first.created_count == 1
        assert first.identical_count == 1
        assert second.status == "unchanged"
        assert second.created_count == 0
        assert second.identical_count == 2
        assert [event.event_type for event in events].count(
            "agent.invocation.completed"
        ) == 1

    def test_legacy_room_start_without_agent_identity_is_invalid(self) -> None:
        session = self.sessions.create_room(
            channel="console",
            target="room",
            session_id="repair-room",
        )
        source = self.sessions.append_event(
            session_id=session.id,
            event_type="run.queued",
            payload={
                "run_id": "run-room",
                "request_id": "request-room",
                "invocation_id": "invocation-room",
                "thread_id": "thread-room",
            },
        )

        report = self._reconciler().repair_session(session.id)

        assert report.status == "invalid_source"
        assert report.invalid_count == 1
        assert report.diagnostics == [
            {
                "code": "SOURCE_IDENTITY_MISSING",
                "event_id": "agent.invocation:invocation-room:start",
                "source_event_id": source.id,
            }
        ]

    def test_partial_creation_reports_invalid_source_precedence(self) -> None:
        session = self.sessions.create_room(
            channel="console",
            target="room",
            session_id="repair-partial-room",
        )
        for index, agent_id in enumerate(("main", "")):
            self.sessions.append_event(
                session_id=session.id,
                event_type="run.queued",
                payload={
                    **({"agent_id": agent_id} if agent_id else {}),
                    "run_id": f"run-{index}",
                    "request_id": f"request-{index}",
                    "invocation_id": f"invocation-{index}",
                },
            )

        report = self._reconciler().repair_session(session.id)

        assert report.status == "invalid_source"
        assert report.created_count == 1
        assert report.invalid_count == 1

    def test_diagnostics_are_bounded_and_marked_truncated(self) -> None:
        session = self.sessions.create_room(
            channel="console",
            target="room",
            session_id="repair-diagnostics-limit",
        )
        for index in range(101):
            self.sessions.append_event(
                session_id=session.id,
                event_type="run.queued",
                payload={
                    "run_id": f"run-{index}",
                    "request_id": f"request-{index}",
                    "invocation_id": f"invocation-{index}",
                },
            )

        report = self._reconciler().repair_session(session.id)

        assert report.status == "invalid_source"
        assert report.invalid_count == 101
        assert len(report.diagnostics) == 100
        assert report.diagnostics_truncated is True

    def test_missing_session_and_storage_failure_are_exact(self) -> None:
        missing = self._reconciler().repair_session("missing-session")
        assert missing.status == "not_found"
        assert missing.high_water_event_id is None
        assert missing.diagnostics[0]["code"] == "SESSION_NOT_FOUND"

        session = self.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="storage-failure",
            session_id="repair-storage-failure",
        )
        self.sessions.append_event(
            session_id=session.id,
            event_type="run.queued",
            payload={
                "agent_id": "main",
                "run_id": "run-failed",
                "request_id": "request-failed",
                "invocation_id": "invocation-failed",
            },
        )
        failed = InvocationLifecycleReconciler.for_runtime(
            sessions=self.sessions,
            telemetryctl=None,
        ).repair_session(session.id)

        assert failed.status == "error"
        assert failed.failed_count == 1
        assert failed.diagnostics[0]["code"] == "TELEMETRY_STORAGE_FAILED"

    def test_repair_pages_past_one_thousand_events(self) -> None:
        session = self.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="paged-repair",
            session_id="paged-repair",
        )
        self.sessions.append_event(
            session_id=session.id,
            event_type="run.queued",
            payload={
                "agent_id": "main",
                "run_id": "run-paged",
                "request_id": "request-paged",
                "invocation_id": "invocation-paged",
                "thread_id": "thread-paged",
                "state": "queued",
            },
        )
        for index in range(1001):
            self.sessions.append_event(
                session_id=session.id,
                event_type="noise",
                payload={"index": index},
            )
        for event_type in (
            "response.persisted",
            "response.delivered",
            "run.completed",
        ):
            self.sessions.append_event(
                session_id=session.id,
                event_type=event_type,
                payload={
                    "run_id": "run-paged",
                    "request_id": "request-paged",
                    "thread_id": "thread-paged",
                    "state": "completed",
                },
            )

        report = self._reconciler().repair_session(session.id)

        assert report.status == "repaired"
        assert report.created_count == 2
        assert report.high_water_event_id == self.sessions.event_high_water(
            session_id=session.id
        )

    def test_conflicting_deterministic_event_is_not_overwritten(self) -> None:
        session = self.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="conflict",
            session_id="repair-conflict",
        )
        source = self.sessions.append_event(
            session_id=session.id,
            event_type="run.queued",
            payload={
                "agent_id": "main",
                "run_id": "run-conflict",
                "request_id": "request-conflict",
                "invocation_id": "invocation-conflict",
                "thread_id": "thread-conflict",
            },
        )
        self.telemetryctl.emit_canonical_event_sync(
            session.id,
            "different-turn",
            "agent.invocation.started",
            {"scope": "durable", "source_event_id": 999},
            event_id="agent.invocation:invocation-conflict:start",
            timestamp=time.time(),
            invocation_id="invocation-conflict",
            agent_id="main",
        )

        report = self._reconciler().repair_session(session.id)

        assert report.status == "conflict"
        assert report.conflict_count == 1
        assert report.diagnostics[0]["source_event_id"] == source.id
