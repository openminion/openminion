from __future__ import annotations

import asyncio
from argparse import Namespace
from contextlib import redirect_stdout
import io
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openminion.modules.llm.providers.base import ProviderRequest, ProviderResponse
from openminion.modules.storage.runtime.session_store.turn_leases import (
    RuntimeSessionTurnFenceError,
)
from openminion.modules.telemetry.cli import main as telemetryctl_main
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService
from openminion.cli.commands.agent.check import run_agent_check
from openminion.cli.commands.agent.runner import run_agent
from openminion.cli.commands.doctor import _run_turn_smoke_check
from openminion.cli.commands.gateway import run_gateway
from openminion.services.context.session import SessionContextService
from openminion.services.agent.telemetry import generate_with_provider_call_telemetry
from openminion.services.gateway.turn_intent import BenchmarkHarnessTurnIntent

from tests.services.gateway._gateway_service_support import GatewayServiceTestCase


pytestmark = pytest.mark.e2e


class InvocationLifecycleConsistencyE2E(GatewayServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.telemetry_path = Path(os.environ["OPENMINION_DATA_ROOT"]) / "telemetry.db"
        self.telemetry = TelemetryService(str(self.telemetry_path))
        self.gateway._agent._telemetryctl = TelemetryCtl(self.telemetry)

    def tearDown(self) -> None:
        self.telemetry.close_sync()
        super().tearDown()

    def test_completion_repair_and_read_only_operator_views(self) -> None:
        emit = self.gateway._turn_runner._lifecycle_ops._emit_invocation_lifecycle

        def _drop_terminal(fact):  # noqa: ANN001
            if fact.event_type != "agent.invocation.started":
                return False
            return emit(fact)

        self.gateway._turn_runner._lifecycle_ops._emit_invocation_lifecycle = (
            _drop_terminal
        )
        response = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="hello",
                session_id="lifecycle-e2e",
                deliver=True,
            )
        )
        invocation_id = str(response.metadata["invocation_id"])

        app = SimpleNamespace(resolve_gateway=lambda _agent_id: self.gateway)
        repair_args = Namespace(
            gateway_command="repair-lifecycle",
            session_id="lifecycle-e2e",
            json=True,
            quiet=False,
        )
        repair_output = io.StringIO()
        with redirect_stdout(repair_output):
            assert run_gateway(repair_args, app) == 0
        repaired = json.loads(repair_output.getvalue())
        repair_output = io.StringIO()
        with redirect_stdout(repair_output):
            assert run_gateway(repair_args, app) == 0
        unchanged = json.loads(repair_output.getvalue())

        assert repaired["status"] == "repaired"
        assert unchanged["status"] == "unchanged"
        command_payloads = []
        for command in (
            ["invocation", "list", "--db", str(self.telemetry_path)],
            ["invocation", "show", invocation_id, "--db", str(self.telemetry_path)],
            ["invocation", "graph", invocation_id, "--db", str(self.telemetry_path)],
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                assert telemetryctl_main(command) == 0
            payload = json.loads(output.getvalue())
            assert invocation_id in json.dumps(payload)
            command_payloads.append(payload)

        listing, detail, graph = command_payloads
        listed = next(
            row
            for row in listing["invocations"]
            if row["invocation_id"] == invocation_id
        )
        assert listed["summary"] == detail["summary"] == graph["summary"]
        events = asyncio.run(self.telemetry.get_invocation_events(invocation_id))
        start = next(
            event for event in events if event.event_type == "agent.invocation.started"
        )
        terminal = next(
            event
            for event in events
            if event.event_type == "agent.invocation.completed"
        )
        assert listed["summary"]["duration_ms"] == round(
            (terminal.timestamp - start.timestamp) * 1000
        )
        assert [event.event_type for event in events].count(
            "agent.invocation.started"
        ) == 1
        assert [event.event_type for event in events].count(
            "agent.invocation.completed"
        ) == 1

    def test_takeover_before_delivery_rejects_the_stale_worker(self) -> None:
        original = self.gateway._turn_runner._renew_session_turn_lease

        def _takeover(*, session_id: str, owner: str, fence_token: int) -> None:
            assert self.sessions.release_session_turn_lease(
                session_id,
                owner=owner,
                fence_token=fence_token,
            )
            self.sessions.acquire_session_turn_lease(
                session_id,
                owner="takeover",
                request_id="takeover",
                ttl_s=60,
            )
            original(
                session_id=session_id,
                owner=owner,
                fence_token=fence_token,
            )

        self.gateway._turn_runner._renew_session_turn_lease = _takeover

        with pytest.raises(RuntimeSessionTurnFenceError):
            asyncio.run(
                self.gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="hello",
                    session_id="takeover-e2e",
                )
            )

        assert self.channel.sent == []
        event_types = [
            event.event_type
            for event in self.sessions.list_events(
                session_id="takeover-e2e",
                limit=100,
            )
        ]
        assert "response.persisted" in event_types
        assert "response.delivered" not in event_types
        assert "run.completed" not in event_types

    def test_context_compaction_and_deferred_enrichment_obey_turn_fences(
        self,
    ) -> None:
        session = self.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="context-e2e",
        )
        for role, body in (
            ("inbound", "u1"),
            ("outbound", "a1"),
            ("inbound", "u2"),
            ("outbound", "a2"),
        ):
            self.sessions.append_message(
                session_id=session.id,
                role=role,
                body=body,
            )
        foreground = self.sessions.acquire_session_turn_lease(
            session.id,
            owner="foreground",
            request_id="foreground",
        )
        deferred = []
        context = SessionContextService(
            self.sessions,
            keep_recent_messages=1,
            summary_enrichment_enabled=True,
            summary_enricher=lambda summary: summary + "\n- enriched",
            summary_enrichment_defer=deferred.append,
        )

        result = context.compact_session(
            session_id=session.id,
            session_turn_fence_token=foreground.fence_token,
        )
        assert result.compacted_count == 3
        assert len(deferred) == 1
        assert self.sessions.release_session_turn_lease(
            session.id,
            owner=foreground.owner,
            fence_token=foreground.fence_token,
        )
        takeover = self.sessions.acquire_session_turn_lease(
            session.id,
            owner="takeover",
            request_id="takeover",
        )

        deferred[0]()
        for role, body in (("inbound", "u3"), ("outbound", "a3")):
            self.sessions.append_message(
                session_id=session.id,
                role=role,
                body=body,
                session_turn_fence_token=takeover.fence_token,
            )
        with pytest.raises(RuntimeSessionTurnFenceError):
            context.compact_session(
                session_id=session.id,
                force=True,
                session_turn_fence_token=foreground.fence_token,
            )
        stored_context = self.sessions.get_session_context(session_id=session.id)
        assert stored_context is not None
        assert "enriched" not in stored_context.rolling_summary

    def test_user_facing_agent_command_uses_durable_gateway_lifecycle(self) -> None:
        app = SimpleNamespace(
            resolve_agent_profile=lambda _agent_id: SimpleNamespace(
                name="main",
                default_channel="console",
            ),
            resolve_gateway=lambda _agent_id: self.gateway,
        )
        args = Namespace(
            message="hello",
            target="local-user",
            channel="console",
            agent_id=None,
            session_id="agent-cli-e2e",
            deliver=False,
            json=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            assert run_agent(args, app) == 0
        payload = json.loads(output.getvalue())
        invocation_id = payload["metadata"]["invocation_id"]
        events = asyncio.run(self.telemetry.get_invocation_events(invocation_id))

        assert payload["metadata"]["session_id"] == "agent-cli-e2e"
        assert self.channel.sent == []
        assert [event.event_type for event in events].count(
            "agent.invocation.started"
        ) == 1
        assert [event.event_type for event in events].count(
            "agent.invocation.completed"
        ) == 1

    def test_runtime_diagnostic_commands_are_finite_and_sessionless(self) -> None:
        profile = SimpleNamespace(
            name="main",
            default_channel="console",
            provider="capture",
        )
        app = SimpleNamespace(
            resolve_agent_profile=lambda _agent_id: profile,
            resolve_agent_service=lambda _agent_id: self.gateway._agent,
            channels=self.gateway._channels,
        )
        args = Namespace(
            message="health check",
            target="doctor",
            channel="console",
            agent_id="main",
            deliver=False,
            json=True,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            assert run_agent_check(args, app) == 0
        check_payload = json.loads(output.getvalue())
        doctor = _run_turn_smoke_check(
            app,
            message="doctor check",
            target="doctor",
            channel="console",
            agent_id="main",
        )

        assert doctor.status == "ok"
        diagnostic_payloads = (
            ("agent-check", check_payload),
            ("doctor", doctor.details),
        )
        for diagnostic, payload in diagnostic_payloads:
            assert payload["scope"] == "runtime"
            assert payload["request_id"]
            assert payload["invocation_id"]
            assert payload["execution_id"]
            assert (
                self.sessions.get_session(
                    f"runtime:{diagnostic}:{payload['request_id']}"
                )
                is None
            )
            events = asyncio.run(
                self.telemetry.get_invocation_events(payload["invocation_id"])
            )
            assert events[-1].event_type == "agent.invocation.completed"

        failing_app = SimpleNamespace(
            resolve_agent_profile=lambda _agent_id: profile,
            resolve_agent_service=lambda _agent_id: self.gateway._agent,
            channels=SimpleNamespace(
                get=lambda _channel: (_ for _ in ()).throw(KeyError("missing"))
            ),
        )
        failed = _run_turn_smoke_check(
            failing_app,
            message="doctor check",
            target="doctor",
            channel="missing",
            agent_id="main",
        )
        assert failed.details["scope"] == "runtime"
        assert failed.details["request_id"]

    def test_interactive_loop_and_typed_terminal_takeover_are_fenced(self) -> None:
        implicit = self.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="local-user",
        )
        original_renew = self.gateway._turn_runner._renew_session_turn_lease

        def _takeover_before_loop_send(
            *, session_id: str, owner: str, fence_token: int
        ) -> None:
            assert self.sessions.release_session_turn_lease(
                session_id,
                owner=owner,
                fence_token=fence_token,
            )
            self.sessions.acquire_session_turn_lease(
                session_id,
                owner="loop-takeover",
                request_id="loop-takeover",
            )
            original_renew(
                session_id=session_id,
                owner=owner,
                fence_token=fence_token,
            )

        self.gateway._turn_runner._renew_session_turn_lease = _takeover_before_loop_send
        with (
            patch("builtins.input", return_value="hello"),
            pytest.raises(RuntimeSessionTurnFenceError),
        ):
            asyncio.run(
                self.gateway.run_loop(
                    channel="console",
                    target="local-user",
                    show_progress=False,
                )
            )
        assert implicit.id
        assert self.channel.sent == []

        typed_session = "typed-terminal-e2e"
        original_builder = self.gateway._turn_runner._build_gtgs_terminal_resolver

        def _build_takeover_resolver(**kwargs):  # noqa: ANN003
            resolver = original_builder(**kwargs)
            assert resolver is not None
            fence_token = kwargs["session_turn_fence_token"]

            def _resolver(**resolver_kwargs):  # noqa: ANN003
                assert self.sessions.release_session_turn_lease(
                    typed_session,
                    owner="gateway:typed-request",
                    fence_token=fence_token,
                )
                self.sessions.acquire_session_turn_lease(
                    typed_session,
                    owner="typed-takeover",
                    request_id="typed-takeover",
                )
                return resolver(**resolver_kwargs)

            return _resolver

        self.gateway._turn_runner._build_gtgs_terminal_resolver = (
            _build_takeover_resolver
        )
        intent = BenchmarkHarnessTurnIntent(
            goal_id="typed-goal",
            corpus_task_id="typed-task",
            description="typed takeover",
            mission_type="coding",
            success_criteria=(
                {
                    "criterion_id": "criterion",
                    "description": "criterion",
                    "structural_check": "success_criteria.tests_passed=true",
                },
            ),
            deliverables=(
                {
                    "deliverable_id": "deliverable",
                    "description": "deliverable",
                    "verification_hint": "artifact_presence",
                },
            ),
        )
        with pytest.raises(RuntimeSessionTurnFenceError):
            asyncio.run(
                self.gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="typed",
                    session_id=typed_session,
                    request_id="typed-request",
                    typed_turn_intent=intent,
                    deliver=False,
                )
            )
        typed_event_types = {
            event.event_type
            for event in self.sessions.list_events(
                session_id=typed_session,
                limit=100,
            )
        }
        assert not typed_event_types.intersection(
            {
                "verifier.completed",
                "verify.completed",
                "run.checkpoint",
                "run.completed",
            }
        )


@pytest.mark.asyncio
async def test_provider_outcomes_survive_telemetry_store_failure() -> None:
    class _FailingTelemetry:
        async def emit_canonical_event(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            raise OSError("telemetry unavailable")

    service_port = SimpleNamespace(
        _service=SimpleNamespace(
            _telemetryctl=_FailingTelemetry(),
            _logger=logging.getLogger(__name__),
        )
    )
    calls = 0

    async def _generate() -> ProviderResponse:
        nonlocal calls
        calls += 1
        return ProviderResponse(text="provider result", model="model")

    response = await generate_with_provider_call_telemetry(
        service_port=service_port,
        request=ProviderRequest(user_message="hello", system_prompt="system"),
        session_id="runtime:e2e",
        turn_id="turn-e2e",
        provider_name="provider",
        generate=_generate,
    )

    assert calls == 1
    assert response.text == "provider result"

    provider_error = ValueError("provider failure")
    failed_calls = 0

    async def _fail() -> ProviderResponse:
        nonlocal failed_calls
        failed_calls += 1
        raise provider_error

    with pytest.raises(ValueError) as raised:
        await generate_with_provider_call_telemetry(
            service_port=service_port,
            request=ProviderRequest(user_message="hello", system_prompt="system"),
            session_id="runtime:e2e-failure",
            turn_id="turn-e2e-failure",
            provider_name="provider",
            generate=_fail,
        )

    assert failed_calls == 1
    assert raised.value is provider_error
