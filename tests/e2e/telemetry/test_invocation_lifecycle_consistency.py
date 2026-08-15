from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
import io
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.modules.llm.providers.base import ProviderRequest, ProviderResponse
from openminion.modules.storage.runtime.session_store.turn_leases import (
    RuntimeSessionTurnFenceError,
)
from openminion.modules.telemetry.cli import main as telemetryctl_main
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService
from openminion.services.agent.telemetry import generate_with_provider_call_telemetry

from tests.services.gateway._gateway_service_support import GatewayServiceTestCase


pytestmark = pytest.mark.e2e


class InvocationLifecycleConsistencyE2E(GatewayServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.telemetry_path = Path(self._tmp.name) / "telemetry.db"
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

        repaired = self.gateway.repair_invocation_lifecycle(
            session_id="lifecycle-e2e"
        )
        unchanged = self.gateway.repair_invocation_lifecycle(
            session_id="lifecycle-e2e"
        )

        assert repaired["status"] == "repaired"
        assert unchanged["status"] == "unchanged"
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


@pytest.mark.asyncio
async def test_provider_result_survives_telemetry_store_failure() -> None:
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
