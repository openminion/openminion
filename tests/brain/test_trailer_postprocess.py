from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.trailers import (
    EXPECTED_TRAILERS_METADATA_KEY,
    TRAILER_LANE_APD,
    TRAILER_LANE_DELEGATION_CONTEXT,
    TRAILER_LANE_DELEGATION_RESULT,
    TRAILER_LANE_MACC,
    TRAILER_LANE_MRP,
    TRAILER_LANE_PTC,
    TRAILER_LANE_SWSC,
    TrailerPostprocessService,
)


class _FakeSessionAPI:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        actor_type: str = "",
        actor_id: str = "",
        trace: dict[str, Any] | None = None,
        importance: int = 1,
        redaction: str = "none",
        status: str = "ok",
    ) -> None:
        self.events.append(
            {
                "session_id": session_id,
                "event_type": event_type,
                "payload": payload,
                "actor_type": actor_type,
                "actor_id": actor_id,
                "trace": trace,
                "importance": importance,
                "status": status,
            }
        )


class TrailerPostprocessMeasurementTests(unittest.TestCase):
    def test_expected_and_emitted_events_both_fire_for_apd_trailer(self) -> None:
        session_api = _FakeSessionAPI()
        response = SimpleNamespace(
            task_plan={"plan_id": "p1", "objective": "x", "steps": []},
            task_plan_step_completed=None,
            task_plan_step_blocked=None,
            task_plan_revision=None,
            task_plan_abandoned=None,
            task_plan_completed=None,
            confident_complete=None,
            session_work_summary=None,
        )

        service = TrailerPostprocessService()
        result = service.process(
            response=response,
            session_api=session_api,
            session_id="s1",
            agent_id="agent-x",
            trace_id="trace-1",
            route="direct_respond",
            request_metadata={
                EXPECTED_TRAILERS_METADATA_KEY: [TRAILER_LANE_APD],
            },
        )

        self.assertEqual(result.expected_lanes, [TRAILER_LANE_APD])
        self.assertEqual(result.emitted_lanes, [TRAILER_LANE_APD])
        self.assertEqual(result.route, "direct_respond")

        event_types = [event["event_type"] for event in session_api.events]
        self.assertIn("trailer.expected", event_types)
        self.assertIn("trailer.emitted", event_types)

        expected_event = next(
            event
            for event in session_api.events
            if event["event_type"] == "trailer.expected"
        )
        self.assertEqual(expected_event["payload"]["lanes"], [TRAILER_LANE_APD])
        self.assertEqual(expected_event["payload"]["route"], "direct_respond")

        emitted_event = next(
            event
            for event in session_api.events
            if event["event_type"] == "trailer.emitted"
        )
        self.assertEqual(emitted_event["payload"]["lanes"], [TRAILER_LANE_APD])
        self.assertEqual(
            emitted_event["payload"]["sources"],
            {TRAILER_LANE_APD: ["unknown"]},
        )

    def test_expected_lane_with_no_trailer_emitted_records_empty_emitted(self) -> None:
        session_api = _FakeSessionAPI()
        response = SimpleNamespace(
            task_plan=None,
            task_plan_step_completed=None,
            task_plan_step_blocked=None,
            task_plan_revision=None,
            task_plan_abandoned=None,
            task_plan_completed=None,
            confident_complete=None,
            session_work_summary=None,
        )

        service = TrailerPostprocessService()
        result = service.process(
            response=response,
            session_api=session_api,
            session_id="s1",
            agent_id="agent-x",
            request_metadata={
                EXPECTED_TRAILERS_METADATA_KEY: [TRAILER_LANE_APD],
            },
        )

        self.assertEqual(result.expected_lanes, [TRAILER_LANE_APD])
        self.assertEqual(result.emitted_lanes, [])

        emitted_event = next(
            event
            for event in session_api.events
            if event["event_type"] == "trailer.emitted"
        )
        self.assertEqual(emitted_event["payload"]["lanes"], [])

    def test_no_expected_metadata_still_emits_trailer_emitted(self) -> None:
        session_api = _FakeSessionAPI()
        response = SimpleNamespace(
            task_plan={"plan_id": "p1", "objective": "x", "steps": []},
            task_plan_step_completed=None,
            task_plan_step_blocked=None,
            task_plan_revision=None,
            task_plan_abandoned=None,
            task_plan_completed=None,
            confident_complete=None,
            session_work_summary=None,
        )

        service = TrailerPostprocessService()
        result = service.process(
            response=response,
            session_api=session_api,
            session_id="s1",
            agent_id="agent-x",
            request_metadata=None,
        )

        self.assertEqual(result.expected_lanes, [])
        self.assertEqual(result.emitted_lanes, [TRAILER_LANE_APD])

        event_types = [event["event_type"] for event in session_api.events]
        self.assertNotIn("trailer.expected", event_types)
        self.assertIn("trailer.emitted", event_types)

    def test_emitted_payloads_path_detects_lanes_without_response_object(
        self,
    ) -> None:
        session_api = _FakeSessionAPI()
        payloads = {
            "task_plan": {"plan_id": "p1", "objective": "x", "steps": []},
            "session_work_summary": {"summary": "work"},
            "confident_complete": None,
        }

        service = TrailerPostprocessService()
        result = service.process(
            emitted_payloads=payloads,
            session_api=session_api,
            session_id="s1",
            agent_id="agent-x",
            route="adaptive_final",
        )

        self.assertIn(TRAILER_LANE_APD, result.emitted_lanes)
        self.assertIn(TRAILER_LANE_SWSC, result.emitted_lanes)
        self.assertNotIn(TRAILER_LANE_MACC, result.emitted_lanes)

    def test_group_a_structured_fields_are_measured_as_emitted_lanes(self) -> None:
        session_api = _FakeSessionAPI()
        response = SimpleNamespace(
            confident_complete={"complete": True, "reasoning": "done"},
            session_work_summary={"summary": "checkpoint"},
            pending_turn_context={"original_user_request": "continue"},
            meta_rule_preference={
                "rule": "retry_limit",
                "preferred_value": 2,
                "reasoning": "avoid loops",
            },
            delegation_context={"summary": "delegate this slice"},
            delegation_result_summary={"summary": "child completed"},
            telemetry={
                "typed_signal_sources": {
                    "confident_complete": "structured_field",
                    "session_work_summary": "structured_field",
                    "pending_turn_context": "structured_field",
                    "meta_rule_preference": "structured_field",
                    "delegation_context": "structured_field",
                    "delegation_result_summary": "structured_field",
                }
            },
        )

        service = TrailerPostprocessService()
        result = service.process(
            response=response,
            session_api=session_api,
            session_id="s1",
            agent_id="agent-x",
            route="direct_respond",
            request_metadata={
                EXPECTED_TRAILERS_METADATA_KEY: [
                    TRAILER_LANE_MACC,
                    TRAILER_LANE_SWSC,
                    TRAILER_LANE_PTC,
                    TRAILER_LANE_MRP,
                    TRAILER_LANE_DELEGATION_CONTEXT,
                    TRAILER_LANE_DELEGATION_RESULT,
                ],
            },
        )

        self.assertEqual(
            result.emitted_lanes,
            [
                TRAILER_LANE_MACC,
                TRAILER_LANE_SWSC,
                TRAILER_LANE_PTC,
                TRAILER_LANE_MRP,
                TRAILER_LANE_DELEGATION_CONTEXT,
                TRAILER_LANE_DELEGATION_RESULT,
            ],
        )
        self.assertFalse(result.feedback_pending)
        emitted_event = next(
            event
            for event in session_api.events
            if event["event_type"] == "trailer.emitted"
        )
        self.assertEqual(
            emitted_event["payload"]["sources"][TRAILER_LANE_MACC],
            ["structured_field"],
        )

    def test_route_label_recorded_on_both_events(self) -> None:
        session_api = _FakeSessionAPI()
        response = SimpleNamespace(
            task_plan=None,
            task_plan_step_completed=None,
            task_plan_step_blocked=None,
            task_plan_revision=None,
            task_plan_abandoned=None,
            task_plan_completed=None,
            confident_complete={"confident": True},
            session_work_summary=None,
        )

        service = TrailerPostprocessService()
        service.process(
            response=response,
            session_api=session_api,
            session_id="s1",
            agent_id="agent-x",
            route="adaptive_final",
            request_metadata={
                EXPECTED_TRAILERS_METADATA_KEY: [TRAILER_LANE_MACC],
            },
        )

        for event in session_api.events:
            self.assertEqual(event["payload"]["route"], "adaptive_final")

    def test_missing_session_api_returns_empty_result_without_crash(self) -> None:
        response = SimpleNamespace(task_plan=None)
        service = TrailerPostprocessService()
        result = service.process(
            response=response,
            session_api=None,
            session_id="s1",
            agent_id="agent-x",
        )
        self.assertEqual(result.expected_lanes, [])
        self.assertEqual(result.emitted_lanes, [])

    def test_expected_lanes_deduplicate_and_normalize(self) -> None:
        session_api = _FakeSessionAPI()
        response = SimpleNamespace(task_plan=None)

        service = TrailerPostprocessService()
        result = service.process(
            response=response,
            session_api=session_api,
            session_id="s1",
            agent_id="agent-x",
            request_metadata={
                EXPECTED_TRAILERS_METADATA_KEY: ["APD", " apd ", "macc", "apd"],
            },
        )
        self.assertEqual(result.expected_lanes, ["apd", "macc"])

    def test_group_a_missing_lane_feedback_uses_specific_hints(self) -> None:
        session_api = _FakeSessionAPI()
        response = SimpleNamespace(
            pending_turn_context=None,
            meta_rule_preference=None,
            delegation_context=None,
            delegation_result_summary=None,
        )

        service = TrailerPostprocessService()
        result = service.process(
            response=response,
            session_api=session_api,
            session_id="s1",
            agent_id="agent-x",
            request_metadata={
                EXPECTED_TRAILERS_METADATA_KEY: [
                    TRAILER_LANE_PTC,
                    TRAILER_LANE_MRP,
                    TRAILER_LANE_DELEGATION_CONTEXT,
                    TRAILER_LANE_DELEGATION_RESULT,
                ],
            },
        )

        self.assertTrue(result.feedback_pending)
        feedback_event = next(
            event
            for event in session_api.events
            if event["event_type"] == "trailer.feedback_pending"
        )
        hints = "\n".join(feedback_event["payload"]["hints"])
        self.assertIn("pending_turn_context", hints)
        self.assertIn("meta_rule_preference", hints)
        self.assertIn("delegation_context", hints)
        self.assertIn("delegation_result_summary", hints)


class TrailerPostprocessEventEmissionTests(unittest.TestCase):
    def test_emitted_event_uses_system_actor_type(self) -> None:
        session_api = _FakeSessionAPI()
        response = SimpleNamespace(task_plan=None)

        service = TrailerPostprocessService()
        service.process(
            response=response,
            session_api=session_api,
            session_id="s1",
            agent_id="agent-x",
        )

        self.assertTrue(session_api.events)
        emitted_event = session_api.events[0]
        self.assertEqual(emitted_event["actor_type"], "system")
        self.assertEqual(emitted_event["actor_id"], "agent-x")
