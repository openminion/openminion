from __future__ import annotations

from openminion.services.gateway.turn_intent import BenchmarkHarnessTurnIntent
from openminion.services.runtime.run_status import RUN_CHECKPOINT_EVENT_TYPE
from openminion.services.runtime.verifier_binding import (
    TERMINAL_STATE_PROVENANCE_FIELD,
    TERMINAL_STATE_PROVENANCE_TYPED,
)
from tests.services.gateway._gateway_service_support import (
    GatewayServiceTestCase,
    asyncio,
)


def _benchmark_harness_turn_intent() -> BenchmarkHarnessTurnIntent:
    return BenchmarkHarnessTurnIntent(
        kind="benchmark_harness",
        goal_id="goal-ameb-coding-01",
        corpus_task_id="ameb-coding-01",
        description="Update a bounded function and satisfy one focused test file.",
        mission_type="coding",
        success_criteria=(
            {
                "criterion_id": "ameb-coding-01-sc-tests-pass",
                "description": "Focused pytest target passes after the edit.",
                "structural_check": "success_criteria.tests_passed=true",
            },
        ),
        deliverables=(
            {
                "deliverable_id": "ameb-coding-01-d-patch-artifact",
                "description": "Patch artifact describing edited file set.",
                "verification_hint": "artifact_presence",
            },
        ),
        failure_conditions=(
            {
                "condition_id": "ameb-coding-01-fc-tests-failed",
                "kind": "success_criterion_unmet",
                "description": "Target test file did not pass after the edit.",
            },
        ),
    )


class GatewayTypedGoalSourceIntegrationTests(GatewayServiceTestCase):
    def test_benchmark_harness_intent_routes_through_typed_terminal_binding(
        self,
    ) -> None:
        response = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="ameb benchmark turn",
                session_id="gtgs-benchmark",
                typed_turn_intent=_benchmark_harness_turn_intent(),
                inbound_metadata={"attach_id": "att-gtgs"},
            )
        )

        session_id = response.metadata["session_id"]
        events = self.sessions.list_events(
            session_id=session_id,
            limit=100,
            newest_first=False,
        )
        event_types = [event.event_type for event in events]
        assert RUN_CHECKPOINT_EVENT_TYPE in event_types
        terminal_events = [
            event
            for event in events
            if event.event_type in {"run.completed", "run.failed", "run.blocked"}
        ]
        assert terminal_events, "expected a terminal run event"
        terminal = terminal_events[-1]
        assert (
            terminal.payload[TERMINAL_STATE_PROVENANCE_FIELD]
            == TERMINAL_STATE_PROVENANCE_TYPED
        )
        assert terminal.payload["checkpoint_id"].endswith(":terminal")

    def test_freeform_chat_without_typed_intent_preserves_legacy_path(self) -> None:
        response = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="normal chat turn",
                session_id="gtgs-freeform",
            )
        )

        session_id = response.metadata["session_id"]
        event_types = [
            event.event_type
            for event in self.sessions.list_events(
                session_id=session_id,
                limit=100,
                newest_first=False,
            )
        ]
        assert RUN_CHECKPOINT_EVENT_TYPE not in event_types
