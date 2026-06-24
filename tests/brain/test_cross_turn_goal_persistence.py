from __future__ import annotations

import unittest
from typing import Any

from openminion.modules.brain.loop.continuation import (
    AUTONOMOUS_TURN_FIRED_EVENT,
    DEFAULT_MAX_AUTONOMOUS_TURNS_PER_PLAN,
    DEFAULT_MAX_AUTONOMOUS_TURNS_PER_SESSION,
    check_autonomous_continuation_caps,
    count_autonomous_turns,
    peek_latest_continuation_signal,
    record_autonomous_turn,
    run_with_autonomous_continuation,
    should_schedule_continuation,
)
from openminion.modules.brain.loop.tools.plan_control import (
    PLAN_ACTION_DECLARE,
    PLAN_ACTION_REVISE,
    PLAN_ACTION_STEP_BLOCKED,
    PLAN_ACTION_STEP_COMPLETED,
    PLAN_CONTINUE_AUTONOMOUSLY_OUTPUT_KEY,
    build_plan_tool_spec,
    handle_plan_tool_call,
)


class _InMemorySessionAPI:
    def __init__(self) -> None:
        self._events: dict[str, list[dict[str, Any]]] = {}

    def _bucket(self, session_id: str) -> list[dict[str, Any]]:
        return self._events.setdefault(session_id, [])

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._bucket(session_id))

    def append_event(
        self,
        session_id: str,
        type: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> str:
        event = {
            "event_id": f"evt-{len(self._bucket(session_id)) + 1}",
            "event_type": type,
            "payload": dict(payload or {}),
            **kwargs,
        }
        self._bucket(session_id).append(event)
        return event["event_id"]

    def get_active_task_plan(self, session_id: str) -> dict[str, Any] | None:
        for event in reversed(self._bucket(session_id)):
            event_type = str(event.get("event_type") or "").strip()
            if event_type in ("task_plan.declared", "task_plan.revised"):
                payload = event.get("payload") or {}
                plan = payload.get("plan") if isinstance(payload, dict) else None
                if isinstance(plan, dict):
                    return dict(plan)
        return None

    def seed_plan_event(
        self,
        session_id: str,
        *,
        event_type: str,
        plan_id: str,
        continue_plan_autonomously: bool = False,
        step_id: str | None = None,
    ) -> None:
        if event_type in ("task_plan.declared", "task_plan.revised"):
            payload = {
                "plan": {
                    "plan_id": plan_id,
                    "objective": "test",
                    "status": "active",
                    "steps": [
                        {"step_id": "s1", "description": "d1", "status": "pending"}
                    ],
                    "continue_plan_autonomously": continue_plan_autonomously,
                }
            }
        elif event_type == "task_plan.step_completed":
            payload = {
                "plan_id": plan_id,
                "step_id": step_id or "s1",
                "outcome": "ok",
                "output_summary": "done",
                "continue_plan_autonomously": continue_plan_autonomously,
            }
        else:
            payload = {"plan_id": plan_id}
        self.append_event(session_id, event_type, payload)


class PlanToolContinuationParamTests(unittest.TestCase):
    def test_plan_tool_schema_exposes_continue_plan_autonomously(self) -> None:
        spec = build_plan_tool_spec()
        props = spec.input_schema["properties"]
        self.assertIn("continue_plan_autonomously", props)
        self.assertEqual(props["continue_plan_autonomously"]["type"], "boolean")

    def _loop_ctx_with_active_plan(self, plan_id: str) -> Any:
        session_api = _InMemorySessionAPI()
        session_api.seed_plan_event(
            "s1", event_type="task_plan.declared", plan_id=plan_id
        )

        class _State:
            session_id = "s1"
            agent_id = "agent-x"
            trace_id = "trace-x"

        class _LoopCtx:
            def __init__(self) -> None:
                self.session_api = session_api
                self.state = _State()

        return _LoopCtx(), session_api

    def test_step_completed_outputs_signal_when_set(self) -> None:
        loop_ctx, session_api = self._loop_ctx_with_active_plan("p-abc")
        result = handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_STEP_COMPLETED,
                "plan_id": "p-abc",
                "step_id": "s1",
                "outcome": "ok",
                "continue_plan_autonomously": True,
            },
        )
        self.assertEqual(result.status, "success")
        self.assertTrue(result.outputs.get(PLAN_CONTINUE_AUTONOMOUSLY_OUTPUT_KEY))
        events = session_api.list_events("s1")
        latest = events[-1]
        self.assertEqual(latest["event_type"], "task_plan.step_completed")
        self.assertTrue(latest["payload"]["continue_plan_autonomously"])

    def test_step_completed_absent_signal_defaults_false(self) -> None:
        loop_ctx, session_api = self._loop_ctx_with_active_plan("p-xyz")
        result = handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_STEP_COMPLETED,
                "plan_id": "p-xyz",
                "step_id": "s1",
            },
        )
        self.assertEqual(result.status, "success")
        self.assertNotIn(PLAN_CONTINUE_AUTONOMOUSLY_OUTPUT_KEY, result.outputs)
        latest = session_api.list_events("s1")[-1]
        self.assertFalse(latest["payload"]["continue_plan_autonomously"])

    def test_step_completed_inherits_plan_continuation_until_steps_finish(
        self,
    ) -> None:
        session_api = _InMemorySessionAPI()

        class _State:
            session_id = "s1"
            agent_id = "agent-x"
            trace_id = "trace-x"

        class _LoopCtx:
            pass

        loop_ctx = _LoopCtx()
        loop_ctx.session_api = session_api
        loop_ctx.state = _State()

        declare = handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_DECLARE,
                "plan_id": "p-auto",
                "objective": "build",
                "steps": [
                    {"step_id": "s1", "description": "d1"},
                    {"step_id": "s2", "description": "d2"},
                ],
                "continue_plan_autonomously": True,
            },
        )
        self.assertEqual(declare.status, "success")

        first = handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_STEP_COMPLETED,
                "plan_id": "p-auto",
                "step_id": "s1",
                "outcome": "ok",
            },
        )
        self.assertEqual(first.status, "success")
        self.assertTrue(first.outputs[PLAN_CONTINUE_AUTONOMOUSLY_OUTPUT_KEY])
        latest = session_api.list_events("s1")[-1]
        self.assertTrue(latest["payload"]["continue_plan_autonomously"])

        last = handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_STEP_COMPLETED,
                "plan_id": "p-auto",
                "step_id": "s2",
                "outcome": "ok",
            },
        )
        self.assertEqual(last.status, "success")
        self.assertNotIn(PLAN_CONTINUE_AUTONOMOUSLY_OUTPUT_KEY, last.outputs)
        latest = session_api.list_events("s1")[-1]
        self.assertFalse(latest["payload"]["continue_plan_autonomously"])

    def test_redeclaring_same_autonomous_plan_preserves_completed_steps(
        self,
    ) -> None:
        session_api = _InMemorySessionAPI()

        class _State:
            session_id = "s1"
            agent_id = "agent-x"
            trace_id = "trace-x"

        class _LoopCtx:
            pass

        loop_ctx = _LoopCtx()
        loop_ctx.session_api = session_api
        loop_ctx.state = _State()

        handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_DECLARE,
                "plan_id": "p-repeat",
                "objective": "build",
                "steps": [
                    {"step_id": "s1", "description": "d1"},
                    {"step_id": "s2", "description": "d2"},
                ],
                "continue_plan_autonomously": True,
            },
        )
        handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_STEP_COMPLETED,
                "plan_id": "p-repeat",
                "step_id": "s1",
                "outcome": "ok",
                "output_summary": "s1 done",
            },
        )

        redeclare = handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_DECLARE,
                "plan_id": "p-repeat",
                "objective": "build",
                "steps": [
                    {"step_id": "s1", "description": "d1"},
                    {"step_id": "s2", "description": "d2"},
                ],
            },
        )
        self.assertEqual(redeclare.status, "success")
        latest_plan = session_api.list_events("s1")[-1]["payload"]["plan"]
        steps = {step["step_id"]: step for step in latest_plan["steps"]}
        self.assertEqual(steps["s1"]["status"], "completed")
        self.assertEqual(steps["s1"]["output_summary"], "s1 done")
        self.assertTrue(latest_plan["continue_plan_autonomously"])

    def test_step_blocked_ignores_continue_plan_autonomously(self) -> None:
        loop_ctx, session_api = self._loop_ctx_with_active_plan("p-blk")
        result = handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_STEP_BLOCKED,
                "plan_id": "p-blk",
                "step_id": "s1",
                "blocker_type": "user_input_required",
                "continue_plan_autonomously": True,
            },
        )
        self.assertEqual(result.status, "success")
        self.assertNotIn(PLAN_CONTINUE_AUTONOMOUSLY_OUTPUT_KEY, result.outputs)

    def test_declare_and_revise_output_signal_when_set(self) -> None:
        session_api = _InMemorySessionAPI()

        class _State:
            session_id = "s1"
            agent_id = "agent-x"
            trace_id = "trace-x"

        class _LoopCtx:
            pass

        loop_ctx = _LoopCtx()
        loop_ctx.session_api = session_api
        loop_ctx.state = _State()

        declare = handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_DECLARE,
                "plan_id": "p-new",
                "objective": "build",
                "steps": [
                    {"step_id": "s1", "description": "d1"},
                    {"step_id": "s2", "description": "d2"},
                ],
                "continue_plan_autonomously": True,
            },
        )
        self.assertEqual(declare.status, "success")
        self.assertTrue(declare.outputs.get(PLAN_CONTINUE_AUTONOMOUSLY_OUTPUT_KEY))

        revise = handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_REVISE,
                "plan_id": "p-new",
                "reason": "new info",
                "revised_steps": [
                    {"step_id": "s1", "description": "d1'"},
                    {"step_id": "s3", "description": "d3"},
                ],
                "continue_plan_autonomously": True,
            },
        )
        self.assertEqual(revise.status, "success")
        self.assertTrue(revise.outputs.get(PLAN_CONTINUE_AUTONOMOUSLY_OUTPUT_KEY))

        events = session_api.list_events("s1")
        declared_event = next(
            e for e in events if e["event_type"] == "task_plan.declared"
        )
        revised_event = next(
            e for e in events if e["event_type"] == "task_plan.revised"
        )
        self.assertTrue(declared_event["payload"]["plan"]["continue_plan_autonomously"])
        self.assertTrue(revised_event["payload"]["plan"]["continue_plan_autonomously"])


class AutonomousTurnCapsTests(unittest.TestCase):
    def test_count_autonomous_turns_empty(self) -> None:
        api = _InMemorySessionAPI()
        self.assertEqual(count_autonomous_turns(session_api=api, session_id="s1"), 0)

    def test_record_and_count_per_session_and_per_plan(self) -> None:
        api = _InMemorySessionAPI()
        record_autonomous_turn(
            session_api=api,
            session_id="s1",
            agent_id="a1",
            plan_id="p1",
            trace_id="t1",
        )
        record_autonomous_turn(
            session_api=api,
            session_id="s1",
            agent_id="a1",
            plan_id="p1",
            trace_id="t2",
        )
        record_autonomous_turn(
            session_api=api,
            session_id="s1",
            agent_id="a1",
            plan_id="p2",
            trace_id="t3",
        )
        self.assertEqual(count_autonomous_turns(session_api=api, session_id="s1"), 3)
        self.assertEqual(
            count_autonomous_turns(session_api=api, session_id="s1", plan_id="p1"),
            2,
        )
        self.assertEqual(
            count_autonomous_turns(session_api=api, session_id="s1", plan_id="p2"),
            1,
        )
        events = [
            e
            for e in api.list_events("s1")
            if e["event_type"] == AUTONOMOUS_TURN_FIRED_EVENT
        ]
        self.assertEqual([e["payload"]["turn_index"] for e in events], [1, 2, 3])

    def test_caps_allow_within_limits(self) -> None:
        api = _InMemorySessionAPI()
        for _ in range(2):
            record_autonomous_turn(
                session_api=api,
                session_id="s1",
                agent_id="a1",
                plan_id="p1",
                trace_id=None,
            )
        decision = check_autonomous_continuation_caps(
            session_api=api, session_id="s1", plan_id="p1"
        )
        self.assertTrue(decision["allowed"])
        self.assertIsNone(decision["reason"])
        self.assertEqual(decision["plan_turns"], 2)
        self.assertEqual(decision["session_turns"], 2)

    def test_per_plan_cap_triggers_termination(self) -> None:
        api = _InMemorySessionAPI()
        for _ in range(3):
            record_autonomous_turn(
                session_api=api,
                session_id="s1",
                agent_id="a1",
                plan_id="p1",
                trace_id=None,
            )
        decision = check_autonomous_continuation_caps(
            session_api=api,
            session_id="s1",
            plan_id="p1",
            max_per_plan=3,
            max_per_session=100,
        )
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "per_plan_cap_reached")

    def test_per_session_cap_triggers_termination(self) -> None:
        api = _InMemorySessionAPI()
        for plan in ("p1", "p2"):
            for _ in range(2):
                record_autonomous_turn(
                    session_api=api,
                    session_id="s1",
                    agent_id="a1",
                    plan_id=plan,
                    trace_id=None,
                )
        decision = check_autonomous_continuation_caps(
            session_api=api,
            session_id="s1",
            plan_id="p1",
            max_per_plan=10,
            max_per_session=4,
        )
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "per_session_cap_reached")

    def test_caps_survive_list_like_api_failure(self) -> None:
        class _BrokenAPI:
            def list_events(self, session_id: str) -> list[dict[str, Any]]:
                raise RuntimeError("store down")

        self.assertEqual(
            count_autonomous_turns(session_api=_BrokenAPI(), session_id="s1"),
            0,
        )

    def test_caps_default_constants_match_spec(self) -> None:
        self.assertEqual(DEFAULT_MAX_AUTONOMOUS_TURNS_PER_PLAN, 10)
        self.assertEqual(DEFAULT_MAX_AUTONOMOUS_TURNS_PER_SESSION, 20)


# Event-log peek + signal resolution


class PeekContinuationSignalTests(unittest.TestCase):
    def test_returns_none_for_empty_session(self) -> None:
        api = _InMemorySessionAPI()
        self.assertIsNone(
            peek_latest_continuation_signal(session_api=api, session_id="s1")
        )

    def test_picks_up_latest_step_completed_signal(self) -> None:
        api = _InMemorySessionAPI()
        api.seed_plan_event(
            "s1",
            event_type="task_plan.declared",
            plan_id="p1",
            continue_plan_autonomously=False,
        )
        api.seed_plan_event(
            "s1",
            event_type="task_plan.step_completed",
            plan_id="p1",
            continue_plan_autonomously=True,
        )
        signal = peek_latest_continuation_signal(session_api=api, session_id="s1")
        self.assertIsNotNone(signal)
        self.assertEqual(signal["plan_id"], "p1")
        self.assertTrue(signal["continue_plan_autonomously"])

    def test_terminal_event_cancels_dangling_signal(self) -> None:
        api = _InMemorySessionAPI()
        api.seed_plan_event(
            "s1",
            event_type="task_plan.step_completed",
            plan_id="p1",
            continue_plan_autonomously=True,
        )
        # Then model calls plan(action="step_blocked") -> terminal.
        api.append_event(
            "s1",
            "task_plan.step_blocked",
            {"plan_id": "p1", "step_id": "s1", "blocker_type": "user_required"},
        )
        self.assertIsNone(
            peek_latest_continuation_signal(session_api=api, session_id="s1")
        )

    def test_abandoned_event_cancels_signal(self) -> None:
        api = _InMemorySessionAPI()
        api.seed_plan_event(
            "s1",
            event_type="task_plan.declared",
            plan_id="p1",
            continue_plan_autonomously=True,
        )
        api.append_event("s1", "task_plan.abandoned", {"plan_id": "p1"})
        self.assertIsNone(
            peek_latest_continuation_signal(session_api=api, session_id="s1")
        )


# CTGP-03 - end-to-end scheduler wrapper


class _StubRunner:
    def __init__(
        self,
        *,
        session_api: _InMemorySessionAPI,
        script: list[dict[str, Any]],
    ) -> None:
        self.session_api = session_api
        self._script = list(script)
        self._idx = 0
        self.call_log: list[dict[str, Any]] = []

        class _Profile:
            agent_id = "agent-x"

        self.profile = _Profile()

    def run(
        self,
        *,
        session_id: str,
        user_input: str | None = None,
        trace_id: str | None = None,
        forced_tools: Any | None = None,
        capability_category: Any | None = None,
        trigger: str = "user_input",
        progress_callback: Any | None = None,
        approval_callback: Any | None = None,
    ) -> Any:
        self.call_log.append(
            {
                "user_input": user_input,
                "trigger": trigger,
                "trace_id": trace_id,
            }
        )
        if self._idx < len(self._script):
            step = self._script[self._idx]
            self._idx += 1
            event_type = step.get("event_type", "task_plan.step_completed")
            if event_type == "task_plan.step_blocked":
                self.session_api.append_event(
                    session_id,
                    event_type,
                    {
                        "plan_id": step["plan_id"],
                        "step_id": step.get("step_id", "s1"),
                        "blocker_type": step.get("blocker_type", "user_input_required"),
                    },
                )
            else:
                self.session_api.seed_plan_event(
                    session_id,
                    event_type=event_type,
                    plan_id=step["plan_id"],
                    continue_plan_autonomously=bool(
                        step.get("continue_plan_autonomously", False)
                    ),
                    step_id=step.get("step_id"),
                )

        # Mock StepOutput — only `.working_state.trace_id` is read.
        class _State:
            def __init__(self, tid: str | None) -> None:
                self.trace_id = tid

        class _StepOutput:
            def __init__(self, tid: str | None) -> None:
                self.working_state = _State(tid)

        return _StepOutput(trace_id or "trace-autonomous")


class RunWithAutonomousContinuationTests(unittest.TestCase):
    def test_three_step_plan_runs_three_autonomous_turns(self) -> None:
        session_api = _InMemorySessionAPI()
        runner = _StubRunner(
            session_api=session_api,
            script=[
                # user turn → declare plan + opt in
                {
                    "event_type": "task_plan.declared",
                    "plan_id": "p1",
                    "continue_plan_autonomously": True,
                },
                # autonomous turn 1 → step_completed s1 + opt in
                {
                    "event_type": "task_plan.step_completed",
                    "plan_id": "p1",
                    "step_id": "s1",
                    "continue_plan_autonomously": True,
                },
                # autonomous turn 2 → step_completed s2 + opt in
                {
                    "event_type": "task_plan.step_completed",
                    "plan_id": "p1",
                    "step_id": "s2",
                    "continue_plan_autonomously": True,
                },
                # autonomous turn 3 → step_completed s3 + STOP (no opt-in)
                {
                    "event_type": "task_plan.step_completed",
                    "plan_id": "p1",
                    "step_id": "s3",
                    "continue_plan_autonomously": False,
                },
            ],
        )
        run_with_autonomous_continuation(
            runner,
            session_id="s1",
            user_input="start building",
        )
        # 1 user turn + 3 autonomous turns = 4 run() calls.
        self.assertEqual(len(runner.call_log), 4)
        self.assertEqual(runner.call_log[0]["trigger"], "user_input")
        self.assertEqual(runner.call_log[0]["user_input"], "start building")
        for call in runner.call_log[1:]:
            self.assertEqual(call["trigger"], "plan_continuation")
            self.assertIsNone(call["user_input"])
        # 3 autonomous_turn.fired events durably recorded.
        self.assertEqual(
            count_autonomous_turns(session_api=session_api, session_id="s1"),
            3,
        )

    def test_caps_terminate_autonomous_cycle(self) -> None:
        session_api = _InMemorySessionAPI()
        # Script: user turn + model keeps opting in forever.
        script = [
            {
                "event_type": "task_plan.declared",
                "plan_id": "p1",
                "continue_plan_autonomously": True,
            }
        ] + [
            {
                "event_type": "task_plan.step_completed",
                "plan_id": "p1",
                "step_id": f"s{i}",
                "continue_plan_autonomously": True,
            }
            for i in range(20)
        ]
        runner = _StubRunner(session_api=session_api, script=script)
        run_with_autonomous_continuation(
            runner,
            session_id="s1",
            user_input="go",
            max_per_plan=3,
            max_per_session=100,
        )
        # 1 user turn + 3 autonomous turns (capped at 3) = 4 runs.
        self.assertEqual(len(runner.call_log), 4)
        # 3 autonomous_turn.fired events durably recorded.
        self.assertEqual(
            count_autonomous_turns(session_api=session_api, session_id="s1"),
            3,
        )
        # Cap-stopped telemetry event appended.
        stopped = [
            e
            for e in session_api.list_events("s1")
            if e["event_type"] == "brain.autonomous_continuation.stopped"
        ]
        self.assertEqual(len(stopped), 1)
        self.assertEqual(stopped[0]["payload"]["reason"], "per_plan_cap_reached")

    def test_step_blocked_stops_autonomous_cycle(self) -> None:
        session_api = _InMemorySessionAPI()
        runner = _StubRunner(
            session_api=session_api,
            script=[
                {
                    "event_type": "task_plan.declared",
                    "plan_id": "p1",
                    "continue_plan_autonomously": True,
                },
                # Autonomous turn fires step_blocked — runtime MUST stop.
                {
                    "event_type": "task_plan.step_blocked",
                    "plan_id": "p1",
                    "step_id": "s1",
                },
            ],
        )
        run_with_autonomous_continuation(runner, session_id="s1", user_input="go")
        # 1 user turn + 1 autonomous turn that produced step_blocked = 2.
        # No further continuation after step_blocked.
        self.assertEqual(len(runner.call_log), 2)

    def test_signal_absent_means_single_turn(self) -> None:
        session_api = _InMemorySessionAPI()
        runner = _StubRunner(
            session_api=session_api,
            script=[
                {
                    "event_type": "task_plan.declared",
                    "plan_id": "p1",
                    "continue_plan_autonomously": False,
                }
            ],
        )
        run_with_autonomous_continuation(runner, session_id="s1", user_input="go")
        self.assertEqual(len(runner.call_log), 1)
        self.assertEqual(runner.call_log[0]["trigger"], "user_input")
        self.assertEqual(
            count_autonomous_turns(session_api=session_api, session_id="s1"),
            0,
        )

    def test_user_initiated_turn_does_not_emit_autonomous_turn_fired(
        self,
    ) -> None:
        session_api = _InMemorySessionAPI()
        runner = _StubRunner(
            session_api=session_api,
            script=[
                {
                    "event_type": "task_plan.declared",
                    "plan_id": "p1",
                    "continue_plan_autonomously": False,
                }
            ],
        )
        run_with_autonomous_continuation(runner, session_id="s1", user_input="start")
        fired = [
            e
            for e in session_api.list_events("s1")
            if e["event_type"] == AUTONOMOUS_TURN_FIRED_EVENT
        ]
        self.assertEqual(fired, [])


class ShouldScheduleContinuationTests(unittest.TestCase):
    def test_refuses_when_signal_not_set(self) -> None:
        api = _InMemorySessionAPI()

        class _StubRunner2:
            session_api = api

        decision = should_schedule_continuation(
            runner=_StubRunner2(),
            session_id="s1",
            plan_id="p1",
            signal_set=False,
        )
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "signal_not_set")

    def test_refuses_when_no_active_plan(self) -> None:
        api = _InMemorySessionAPI()

        class _StubRunner2:
            session_api = api

        decision = should_schedule_continuation(
            runner=_StubRunner2(),
            session_id="s1",
            plan_id="",
            signal_set=True,
        )
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "no_active_plan")


class FailClosedCounterReadTests(unittest.TestCase):
    def test_check_caps_returns_counter_unavailable_when_list_events_raises(
        self,
    ) -> None:
        class _BrokenReadAPI:
            def list_events(self, session_id: str) -> list:
                raise RuntimeError("store offline")

        decision = check_autonomous_continuation_caps(
            session_api=_BrokenReadAPI(),
            session_id="s1",
            plan_id="p1",
        )
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "counter_unavailable")
        self.assertEqual(decision["plan_turns"], -1)
        self.assertEqual(decision["session_turns"], -1)
        self.assertIn("store offline", decision.get("cause", ""))

    def test_check_caps_returns_counter_unavailable_when_api_missing_list_events(
        self,
    ) -> None:
        class _BareAPI:
            def append_event(self, *a, **kw) -> str:
                return "evt"

        decision = check_autonomous_continuation_caps(
            session_api=_BareAPI(), session_id="s1", plan_id="p1"
        )
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "counter_unavailable")

    def test_check_caps_returns_counter_unavailable_when_list_events_returns_garbage(
        self,
    ) -> None:
        class _GarbageAPI:
            def list_events(self, session_id: str) -> Any:
                return "not a list"

        decision = check_autonomous_continuation_caps(
            session_api=_GarbageAPI(), session_id="s1", plan_id="p1"
        )
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "counter_unavailable")

    def test_wrapper_stops_when_counter_read_fails_mid_cycle(self) -> None:

        class _FailingMidCycleAPI(_InMemorySessionAPI):
            def __init__(self) -> None:
                super().__init__()
                self._list_fail_after = None  # None = never fail
                self._list_calls = 0

            def list_events(self, session_id: str) -> list:
                self._list_calls += 1
                if (
                    self._list_fail_after is not None
                    and self._list_calls > self._list_fail_after
                ):
                    raise RuntimeError("event log offline")
                return super().list_events(session_id)

        session_api = _FailingMidCycleAPI()
        runner = _StubRunner(
            session_api=session_api,
            script=[
                {
                    "event_type": "task_plan.declared",
                    "plan_id": "p1",
                    "continue_plan_autonomously": True,
                },
                # Scripted but should NOT be reached — the cap read
                # fails after the first turn, so the wrapper must stop.
                {
                    "event_type": "task_plan.step_completed",
                    "plan_id": "p1",
                    "step_id": "s1",
                    "continue_plan_autonomously": True,
                },
            ],
        )

        session_api._list_fail_after = 2

        run_with_autonomous_continuation(runner, session_id="s1", user_input="go")

        # Only the user turn ran. The first autonomous attempt aborted
        # because the cap-counter read failed.
        self.assertEqual(len(runner.call_log), 1)
        # Inspect the raw bucket directly — `list_events` raises now,
        # and peeking at the telemetry emitter's bucket is the
        # structural way to verify what the runtime recorded.
        bucket_events = session_api._events.get("s1", [])
        fired_direct = [
            e for e in bucket_events if e["event_type"] == AUTONOMOUS_TURN_FIRED_EVENT
        ]
        self.assertEqual(fired_direct, [])
        stopped = [
            e
            for e in bucket_events
            if e["event_type"] == "brain.autonomous_continuation.stopped"
        ]
        self.assertEqual(len(stopped), 1)
        self.assertEqual(stopped[0]["payload"]["reason"], "counter_unavailable")


class StoppedTelemetryCauseTests(unittest.TestCase):
    def test_read_failure_stopped_event_includes_counter_error(self) -> None:
        class _FailingCapCheckAPI(_InMemorySessionAPI):
            def __init__(self) -> None:
                super().__init__()
                self._list_calls = 0

            def list_events(self, session_id: str) -> list:
                self._list_calls += 1
                # Allow the first peek (1) to see the plan. Fail on
                # subsequent reads (cap check / record).
                if self._list_calls <= 1:
                    return super().list_events(session_id)
                raise RuntimeError("store offline: permission denied")

        session_api2 = _FailingCapCheckAPI()
        runner2 = _StubRunner(
            session_api=session_api2,
            script=[
                {
                    "event_type": "task_plan.declared",
                    "plan_id": "p1",
                    "continue_plan_autonomously": True,
                }
            ],
        )
        run_with_autonomous_continuation(runner2, session_id="s1", user_input="go")
        bucket2 = session_api2._events.get("s1", [])
        stopped2 = [
            e
            for e in bucket2
            if e["event_type"] == "brain.autonomous_continuation.stopped"
        ]
        self.assertEqual(len(stopped2), 1)
        payload = stopped2[0]["payload"]
        self.assertEqual(payload["reason"], "counter_unavailable")
        # The underlying adapter error must appear in counter_error so
        # operators can diagnose without re-fetching the exception.
        self.assertIn("counter_error", payload)
        self.assertIn("store offline", payload["counter_error"])

    def test_write_failure_stopped_event_includes_counter_error(self) -> None:
        class _WriteFailAPI(_InMemorySessionAPI):
            def append_event(
                self,
                session_id: str,
                type: str,
                payload: dict[str, Any],
                **kwargs: Any,
            ) -> str:
                if type == AUTONOMOUS_TURN_FIRED_EVENT:
                    raise RuntimeError("disk full: no space on device")
                return super().append_event(session_id, type, payload, **kwargs)

        session_api = _WriteFailAPI()
        runner = _StubRunner(
            session_api=session_api,
            script=[
                {
                    "event_type": "task_plan.declared",
                    "plan_id": "p1",
                    "continue_plan_autonomously": True,
                }
            ],
        )
        run_with_autonomous_continuation(runner, session_id="s1", user_input="go")
        stopped = [
            e
            for e in session_api.list_events("s1")
            if e["event_type"] == "brain.autonomous_continuation.stopped"
        ]
        self.assertEqual(len(stopped), 1)
        payload = stopped[0]["payload"]
        self.assertEqual(payload["reason"], "counter_append_failed")
        self.assertIn("counter_error", payload)
        self.assertIn("disk full", payload["counter_error"])
        # The attempted_turn_index is surfaced on write-failure stops
        # (the wrapper tried to record turn 1 but couldn't).
        self.assertEqual(payload.get("attempted_turn_index"), 1)

    def test_counter_error_is_bounded(self) -> None:
        huge = "x" * 5000

        class _HugeErrorAPI(_InMemorySessionAPI):
            def __init__(self) -> None:
                super().__init__()
                self._list_calls = 0

            def list_events(self, session_id: str) -> list:
                self._list_calls += 1
                if self._list_calls <= 1:
                    return super().list_events(session_id)
                raise RuntimeError(huge)

        session_api = _HugeErrorAPI()
        runner = _StubRunner(
            session_api=session_api,
            script=[
                {
                    "event_type": "task_plan.declared",
                    "plan_id": "p1",
                    "continue_plan_autonomously": True,
                }
            ],
        )
        run_with_autonomous_continuation(runner, session_id="s1", user_input="go")
        stopped = [
            e
            for e in session_api._events.get("s1", [])
            if e["event_type"] == "brain.autonomous_continuation.stopped"
        ]
        self.assertEqual(len(stopped), 1)
        counter_error = stopped[0]["payload"].get("counter_error", "")
        # Bounded; the truncation ellipsis is present.
        self.assertLessEqual(len(counter_error), 600)
        self.assertTrue(counter_error.endswith("…"))

    def test_read_failure_stopped_event_omits_attempted_turn_index(self) -> None:

        class _FailingCapCheckAPI(_InMemorySessionAPI):
            def __init__(self) -> None:
                super().__init__()
                self._list_calls = 0

            def list_events(self, session_id: str) -> list:
                self._list_calls += 1
                if self._list_calls <= 1:
                    return super().list_events(session_id)
                raise RuntimeError("read error")

        session_api = _FailingCapCheckAPI()
        runner = _StubRunner(
            session_api=session_api,
            script=[
                {
                    "event_type": "task_plan.declared",
                    "plan_id": "p1",
                    "continue_plan_autonomously": True,
                }
            ],
        )
        run_with_autonomous_continuation(runner, session_id="s1", user_input="go")
        stopped = [
            e
            for e in session_api._events.get("s1", [])
            if e["event_type"] == "brain.autonomous_continuation.stopped"
        ]
        self.assertEqual(len(stopped), 1)
        payload = stopped[0]["payload"]
        self.assertEqual(payload["reason"], "counter_unavailable")
        self.assertIn("counter_error", payload)
        # Read failed before the index could be derived — the field
        # must be absent rather than a misleading default.
        self.assertNotIn("attempted_turn_index", payload)

    def test_cap_hit_stopped_event_omits_counter_error(self) -> None:
        session_api = _InMemorySessionAPI()
        script = [
            {
                "event_type": "task_plan.declared",
                "plan_id": "p1",
                "continue_plan_autonomously": True,
            }
        ] + [
            {
                "event_type": "task_plan.step_completed",
                "plan_id": "p1",
                "step_id": f"s{i}",
                "continue_plan_autonomously": True,
            }
            for i in range(5)
        ]
        runner = _StubRunner(session_api=session_api, script=script)
        run_with_autonomous_continuation(
            runner,
            session_id="s1",
            user_input="go",
            max_per_plan=2,
            max_per_session=100,
        )
        stopped = [
            e
            for e in session_api.list_events("s1")
            if e["event_type"] == "brain.autonomous_continuation.stopped"
        ]
        self.assertEqual(len(stopped), 1)
        payload = stopped[0]["payload"]
        self.assertEqual(payload["reason"], "per_plan_cap_reached")
        self.assertNotIn("counter_error", payload)


class FailClosedCapCounterTests(unittest.TestCase):
    def test_record_autonomous_turn_raises_when_session_api_missing(self) -> None:
        from openminion.modules.brain.loop.continuation import (
            AutonomousContinuationCapsExceeded,
            record_autonomous_turn,
        )

        with self.assertRaises(AutonomousContinuationCapsExceeded) as cm:
            record_autonomous_turn(
                session_api=None,
                session_id="s1",
                agent_id="a1",
                plan_id="p1",
                trace_id=None,
            )
        self.assertEqual(cm.exception.reason, "counter_unavailable")

    def test_record_autonomous_turn_raises_when_api_lacks_append_event(self) -> None:
        from openminion.modules.brain.loop.continuation import (
            AutonomousContinuationCapsExceeded,
            record_autonomous_turn,
        )

        class _BareAPI:
            def list_events(self, session_id: str) -> list:
                return []

        with self.assertRaises(AutonomousContinuationCapsExceeded) as cm:
            record_autonomous_turn(
                session_api=_BareAPI(),
                session_id="s1",
                agent_id="a1",
                plan_id="p1",
                trace_id=None,
            )
        self.assertEqual(cm.exception.reason, "counter_unavailable")

    def test_record_autonomous_turn_raises_when_append_throws(self) -> None:
        from openminion.modules.brain.loop.continuation import (
            AutonomousContinuationCapsExceeded,
            record_autonomous_turn,
        )

        class _BrokenAPI:
            def list_events(self, session_id: str) -> list:
                return []

            def append_event(self, *args, **kwargs):
                raise RuntimeError("disk full")

        with self.assertRaises(AutonomousContinuationCapsExceeded) as cm:
            record_autonomous_turn(
                session_api=_BrokenAPI(),
                session_id="s1",
                agent_id="a1",
                plan_id="p1",
                trace_id=None,
            )
        self.assertEqual(cm.exception.reason, "counter_append_failed")
        self.assertIn("disk full", cm.exception.details.get("cause", ""))

    def test_wrapper_stops_when_counter_cannot_be_written(self) -> None:

        class _PartialAPI(_InMemorySessionAPI):
            def append_event(
                self,
                session_id: str,
                type: str,
                payload: dict[str, Any],
                **kwargs: Any,
            ) -> str:
                if type == AUTONOMOUS_TURN_FIRED_EVENT:
                    raise RuntimeError("store unavailable")
                return super().append_event(session_id, type, payload, **kwargs)

        session_api = _PartialAPI()
        runner = _StubRunner(
            session_api=session_api,
            script=[
                {
                    "event_type": "task_plan.declared",
                    "plan_id": "p1",
                    "continue_plan_autonomously": True,
                },
                # Would run if fail-open — should NOT be reached.
                {
                    "event_type": "task_plan.step_completed",
                    "plan_id": "p1",
                    "step_id": "s1",
                    "continue_plan_autonomously": True,
                },
            ],
        )
        run_with_autonomous_continuation(runner, session_id="s1", user_input="go")
        # Exactly 1 call — the user turn. The first (and only) attempt
        # at autonomous continuation aborts when the cap counter can't
        # be durably written.
        self.assertEqual(len(runner.call_log), 1)
        # Zero autonomous_turn.fired events landed (partial API rejects).
        self.assertEqual(
            count_autonomous_turns(session_api=session_api, session_id="s1"),
            0,
        )
        # Stopped event records the structural reason.
        stopped = [
            e
            for e in session_api.list_events("s1")
            if e["event_type"] == "brain.autonomous_continuation.stopped"
        ]
        self.assertEqual(len(stopped), 1)
        self.assertEqual(stopped[0]["payload"]["reason"], "counter_append_failed")


# P1-1: production wiring — `BrainBridgeTurnMixin._execute_turn`


class ProductionPathCtgpWiringTests(unittest.TestCase):
    def _build_mixin_with_stub(
        self, *, ctgp_enabled: bool, session_api: _InMemorySessionAPI
    ) -> tuple[Any, "_StubRunner"]:
        from openminion.services.brain.post_execution.mixin import (
            BrainBridgeTurnMixin,
        )

        class _OptionsStub:
            autonomous_continuation_enabled = ctgp_enabled
            autonomous_continuation_max_per_plan = 10
            autonomous_continuation_max_per_session = 20

        runner = _StubRunner(
            session_api=session_api,
            script=[
                {
                    "event_type": "task_plan.declared",
                    "plan_id": "p1",
                    "continue_plan_autonomously": True,
                },
                {
                    "event_type": "task_plan.step_completed",
                    "plan_id": "p1",
                    "step_id": "s1",
                    "continue_plan_autonomously": True,
                },
                {
                    "event_type": "task_plan.step_completed",
                    "plan_id": "p1",
                    "step_id": "s2",
                    "continue_plan_autonomously": False,
                },
            ],
        )
        # Attach options to the runner so the mixin's gate can read them.
        runner.options = _OptionsStub()

        # Minimal Message-like object the mixin reads.
        class _Msg:
            body = "go build"

        mixin = BrainBridgeTurnMixin()
        return (mixin, runner, _Msg())

    def test_execute_turn_routes_through_wrapper_when_ctgp_enabled(
        self,
    ) -> None:
        session_api = _InMemorySessionAPI()
        mixin, runner, msg = self._build_mixin_with_stub(
            ctgp_enabled=True, session_api=session_api
        )
        mixin._execute_turn(
            runner=runner,
            session_id="s1",
            request_id="req-1",
            message=msg,
            forced_tools=None,
            capability_category=None,
        )
        # Wrapper-style behavior: user turn + 2 autonomous turns.
        self.assertEqual(len(runner.call_log), 3)
        self.assertEqual(runner.call_log[0]["trigger"], "user_input")
        for call in runner.call_log[1:]:
            self.assertEqual(call["trigger"], "plan_continuation")
        # Durable cap events recorded for the 2 autonomous turns.
        self.assertEqual(
            count_autonomous_turns(session_api=session_api, session_id="s1"),
            2,
        )

    def test_execute_turn_falls_back_to_run_when_ctgp_disabled(self) -> None:
        session_api = _InMemorySessionAPI()
        mixin, runner, msg = self._build_mixin_with_stub(
            ctgp_enabled=False, session_api=session_api
        )
        mixin._execute_turn(
            runner=runner,
            session_id="s1",
            request_id="req-2",
            message=msg,
            forced_tools=None,
            capability_category=None,
        )
        self.assertEqual(len(runner.call_log), 1)
        self.assertEqual(runner.call_log[0]["trigger"], "user_input")
        # No autonomous_turn.fired events (wrapper never ran).
        self.assertEqual(
            count_autonomous_turns(session_api=session_api, session_id="s1"),
            0,
        )


class RunnerOptionsCtgpConfigTests(unittest.TestCase):
    def test_defaults_match_spec(self) -> None:
        from openminion.modules.brain.config import RunnerOptions

        opts = RunnerOptions()
        self.assertTrue(opts.autonomous_continuation_enabled)
        self.assertEqual(opts.autonomous_continuation_max_per_plan, 10)
        self.assertEqual(opts.autonomous_continuation_max_per_session, 20)

    def test_non_positive_caps_coerced_to_one(self) -> None:
        from openminion.modules.brain.config import RunnerOptions

        opts = RunnerOptions(
            autonomous_continuation_max_per_plan=0,
            autonomous_continuation_max_per_session=-5,
        )
        self.assertEqual(opts.autonomous_continuation_max_per_plan, 1)
        self.assertEqual(opts.autonomous_continuation_max_per_session, 1)

    def test_disabled_flag_propagates(self) -> None:
        from openminion.modules.brain.config import RunnerOptions

        opts = RunnerOptions(autonomous_continuation_enabled=False)
        self.assertFalse(opts.autonomous_continuation_enabled)
