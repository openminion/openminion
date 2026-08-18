from __future__ import annotations

import asyncio
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.modules.llm.providers.base import (
    LLMProvider,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService
from openminion.services.agent import AgentService
from openminion.services.runtime.plugins import PluginRegistry
from tests._csc_fixtures import _csc_install_default_agent
from tests.services.gateway._gateway_service_support import (
    GatewayServiceTestCase,
    _FlakyProvider,
)


class InvocationTerminalLifecycleTests(GatewayServiceTestCase):
    @staticmethod
    def _capture(gateway):
        facts = []
        gateway._turn_runner._lifecycle_ops._emit_invocation_lifecycle = lambda fact: (
            facts.append(fact) or True
        )
        return facts

    def test_completed_gateway_turn_projects_one_terminal(self) -> None:
        facts = self._capture(self.gateway)

        response = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="complete",
                session_id="terminal-completed",
                deliver=True,
            )
        )

        assert [fact.event_type for fact in facts] == [
            "agent.invocation.started",
            "agent.invocation.completed",
        ]
        terminal = facts[-1]
        assert terminal.payload["resolved_state"] == "settled"
        assert terminal.payload["source_event_type"] == "run.completed"
        assert terminal.payload["run_id"] == response.metadata["run_id"]

    def test_gateway_persists_real_start_and_terminal_rows(self) -> None:
        telemetry = TelemetryService(home_root=Path(self._tmp.name) / "telemetry")
        config = OpenMinionConfig()
        _csc_install_default_agent(config, name="main")
        agent = AgentService(
            config,
            PluginRegistry([]),
            self.provider,
            self.gateway._logger,
            telemetryctl=TelemetryCtl(telemetry),
        )
        gateway, _sink = self._build_gateway(
            agent=agent,
            logger_name="openminion.tests.gateway.terminal.storage",
            agent_logger_name="openminion.tests.gateway.terminal.storage.agent",
        )
        try:
            response = asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="persist lifecycle",
                    session_id="terminal-storage",
                    deliver=True,
                )
            )
            stored = asyncio.run(
                telemetry.get_invocation_events(response.metadata["invocation_id"])
            )
        finally:
            telemetry.close_sync()

        lifecycle = [
            event
            for event in stored
            if event.event_type.startswith("agent.invocation.")
        ]
        assert [event.event_type for event in lifecycle] == [
            "agent.invocation.started",
            "agent.invocation.completed",
        ]
        assert lifecycle[0].invocation_id == lifecycle[-1].invocation_id

    def test_failed_gateway_turn_projects_failed_terminal(self) -> None:
        gateway, _sink = self._build_gateway(
            provider=_FlakyProvider(),
            logger_name="openminion.tests.gateway.terminal.failure",
            agent_logger_name="openminion.tests.gateway.terminal.failure.agent",
            auto_resume=False,
        )
        facts = self._capture(gateway)

        with self.assertRaises(RuntimeError):
            asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="fail",
                    session_id="terminal-failed",
                    deliver=True,
                )
            )

        assert [fact.event_type for fact in facts] == [
            "agent.invocation.started",
            "agent.invocation.failed",
        ]
        assert facts[-1].payload["source_event_type"] == "run.failed"
        assert facts[-1].payload["resolved_state"] == "failed"

    def test_exhausted_empty_response_projects_failed_terminal(self) -> None:
        class _RepeatedRecoveredEmptyProvider(LLMProvider):
            name = "repeated-recovered-empty"

            async def generate(self, request: ProviderRequest) -> ProviderResponse:
                del request
                return ProviderResponse(
                    text="display fallback",
                    model="fake-model",
                    normalization={"empty_payload_recovered": True},
                )

        gateway, _sink = self._build_gateway(
            provider=_RepeatedRecoveredEmptyProvider(),
            logger_name="openminion.tests.gateway.terminal.empty-response",
            agent_logger_name="openminion.tests.gateway.terminal.empty-response.agent",
            auto_resume=False,
        )
        facts = self._capture(gateway)

        with self.assertRaises(ProviderError) as raised:
            asyncio.run(
                gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="fail structurally",
                    session_id="terminal-empty-response",
                    deliver=True,
                )
            )

        self.assertEqual(raised.exception.code, "EMPTY_PROVIDER_RESPONSE")
        assert [fact.event_type for fact in facts] == [
            "agent.invocation.started",
            "agent.invocation.failed",
        ]
        assert facts[-1].payload["source_event_type"] == "run.failed"

    def test_deferred_delivery_closes_only_on_replay(self) -> None:
        facts = self._capture(self.gateway)
        first = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="defer",
                session_id="terminal-deferred",
                deliver=False,
            )
        )
        assert [fact.event_type for fact in facts] == ["agent.invocation.started"]

        replay = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="replay",
                session_id="terminal-deferred",
                deliver=True,
            )
        )

        assert replay.metadata.get("replayed_response") == "true"
        assert facts[-1].event_type == "agent.invocation.completed"
        assert facts[-1].payload["source_event_type"] == "response.delivered"
        assert facts[-1].payload["run_id"] == first.metadata["run_id"]

    def test_later_ack_reuses_the_canonical_terminal_source(self) -> None:
        facts = self._capture(self.gateway)
        response = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="ack",
                session_id="terminal-ack",
                deliver=True,
            )
        )
        original = facts[-1]

        self.gateway._turn_runner._lifecycle_ops.emit_turn_event(
            session_id=response.metadata["session_id"],
            event_type="response.acked",
            conversation_id=response.metadata.get("conversation_id") or None,
            thread_id=response.metadata.get("thread_id") or None,
            attach_id=response.metadata.get("attach_id") or None,
            payload={
                "run_id": response.metadata["run_id"],
                "response_id": response.id,
            },
        )

        duplicate = facts[-1]
        assert duplicate.event_id == original.event_id
        assert duplicate.timestamp == original.timestamp
        assert (
            duplicate.payload["source_event_id"] == original.payload["source_event_id"]
        )
