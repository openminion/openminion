from __future__ import annotations

import asyncio
import io
import json
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from openminion.cli.commands.agent.runner import run_agent
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService
from openminion.services.gateway.constants import (
    CALLER_HANDLES_DELIVERY_METADATA_KEY,
)

from tests.services.gateway._gateway_service_support import GatewayServiceTestCase


class AgentIngressLifecycleTests(GatewayServiceTestCase):
    def test_agent_cli_matches_gateway_lifecycle_and_delivery_default(self) -> None:
        telemetry_service = TelemetryService(home_root=Path(self._tmp.name))
        self.gateway._agent._telemetryctl = TelemetryCtl(telemetry_service)
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
            session_id="agent-cli-session",
            deliver=False,
            json=True,
        )

        output = io.StringIO()
        with redirect_stdout(output):
            assert run_agent(args, app) == 0
        cli_payload = json.loads(output.getvalue())
        gateway_response = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="hello",
                session_id="gateway-session",
                deliver=False,
                inbound_metadata={CALLER_HANDLES_DELIVERY_METADATA_KEY: "true"},
            )
        )
        cli_events = asyncio.run(
            telemetry_service.get_invocation_events(
                cli_payload["metadata"]["invocation_id"]
            )
        )
        gateway_events = asyncio.run(
            telemetry_service.get_invocation_events(
                gateway_response.metadata["invocation_id"]
            )
        )
        telemetry_service.close_sync()

        assert self.channel.sent == []
        assert [event.event_type for event in cli_events] == [
            event.event_type for event in gateway_events
        ]
        assert [event.event_type for event in cli_events].count(
            "agent.invocation.started"
        ) == 1
        assert [event.event_type for event in cli_events].count(
            "agent.invocation.completed"
        ) == 1
