from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.trailers import (
    EXPECTED_TRAILERS_METADATA_KEY,
    TRAILER_LANE_APD,
    TrailerPostprocessService,
)
from openminion.modules.context.schemas import SessionSlice
from openminion.modules.context.segment import _render_trailer_feedback


class _FakeSessionAPI:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        self.events.append(
            {
                "session_id": session_id,
                "event_type": event_type,
                "payload": payload,
                **kwargs,
            }
        )


class TrailerFeedbackConstructionTests(unittest.TestCase):
    def test_missing_apd_trailer_emits_feedback_pending(self) -> None:
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
            route="direct_respond",
            request_metadata={
                EXPECTED_TRAILERS_METADATA_KEY: [TRAILER_LANE_APD],
            },
        )

        self.assertTrue(result.feedback_pending)
        feedback_events = [
            event
            for event in session_api.events
            if event["event_type"] == "trailer.feedback_pending"
        ]
        self.assertEqual(len(feedback_events), 1)
        payload = feedback_events[0]["payload"]
        self.assertEqual(payload["kind"], "missing_trailer")
        self.assertEqual(payload["missing_lanes"], [TRAILER_LANE_APD])
        self.assertTrue(payload["hints"])
        self.assertIn("task_plan", payload["hints"][0])

    def test_expected_trailer_emitted_does_not_emit_feedback(self) -> None:
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
            request_metadata={
                EXPECTED_TRAILERS_METADATA_KEY: [TRAILER_LANE_APD],
            },
        )

        self.assertFalse(result.feedback_pending)
        feedback_events = [
            event
            for event in session_api.events
            if event["event_type"] == "trailer.feedback_pending"
        ]
        self.assertEqual(feedback_events, [])

    def test_no_expected_trailers_does_not_emit_feedback(self) -> None:
        session_api = _FakeSessionAPI()
        response = SimpleNamespace(task_plan=None)

        service = TrailerPostprocessService()
        result = service.process(
            response=response,
            session_api=session_api,
            session_id="s1",
            agent_id="agent-x",
            request_metadata=None,
        )

        self.assertFalse(result.feedback_pending)
        feedback_events = [
            event
            for event in session_api.events
            if event["event_type"] == "trailer.feedback_pending"
        ]
        self.assertEqual(feedback_events, [])

    def test_feedback_payload_contains_route(self) -> None:
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
        service.process(
            response=response,
            session_api=session_api,
            session_id="s1",
            agent_id="agent-x",
            route="adaptive_final",
            request_metadata={
                EXPECTED_TRAILERS_METADATA_KEY: [TRAILER_LANE_APD],
            },
        )

        feedback_events = [
            event
            for event in session_api.events
            if event["event_type"] == "trailer.feedback_pending"
        ]
        self.assertEqual(feedback_events[0]["payload"]["route"], "adaptive_final")


class TrailerFeedbackRenderingTests(unittest.TestCase):
    def test_render_missing_trailer_feedback(self) -> None:
        feedback = {
            "kind": "missing_trailer",
            "missing_lanes": ["apd"],
            "route": "direct_respond",
            "hints": [
                "The prior decide guidance expected a <task_plan> control trailer.",
            ],
        }
        text = _render_trailer_feedback(feedback)
        self.assertIn("kind: missing_trailer", text)
        self.assertIn("route: direct_respond", text)
        self.assertIn('["apd"]', text)
        self.assertIn("task_plan", text)

    def test_render_empty_feedback_returns_empty(self) -> None:
        self.assertEqual(_render_trailer_feedback({}), "")


class TrailerFeedbackSliceFieldTests(unittest.TestCase):
    def test_slice_pending_trailer_feedback_defaults_to_none(self) -> None:
        slice_obj = SessionSlice(
            session_id="s1",
            slice_version="v1",
            summary_short="x",
        )
        self.assertIsNone(slice_obj.pending_trailer_feedback)

    def test_slice_pending_trailer_feedback_accepts_dict(self) -> None:
        slice_obj = SessionSlice(
            session_id="s1",
            slice_version="v1",
            summary_short="x",
            pending_trailer_feedback={
                "kind": "missing_trailer",
                "missing_lanes": ["apd"],
                "hints": ["Emit <task_plan> on next response."],
            },
        )
        self.assertEqual(slice_obj.pending_trailer_feedback["kind"], "missing_trailer")
