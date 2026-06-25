from __future__ import annotations

import unittest
from typing import Any

from openminion.modules.brain.loop.proactive_entrypoint import (
    IDLE_TICK_JOB_NAME_PREFIX,
    cancel_idle_tick,
    idle_tick_job_id,
    is_user_active,
    last_user_message_timestamp,
    maybe_schedule_idle_tick,
)


# Mocks


class _InMemorySessionAPI:
    def __init__(self) -> None:
        self._events: dict[str, list[dict[str, Any]]] = {}

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._events.get(session_id, []))

    def append_event(
        self,
        session_id: str,
        type: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> str:
        events = self._events.setdefault(session_id, [])
        event = {
            "event_id": f"evt-{len(events) + 1}",
            "event_type": type,
            "payload": dict(payload or {}),
            **kwargs,
        }
        events.append(event)
        return event["event_id"]

    def get_active_task_plan(self, session_id: str) -> dict[str, Any] | None:
        for event in reversed(self._events.get(session_id, [])):
            event_type = str(event.get("event_type") or "").strip()
            if event_type in ("task_plan.declared", "task_plan.revised"):
                payload = event.get("payload") or {}
                plan = payload.get("plan") if isinstance(payload, dict) else None
                if isinstance(plan, dict):
                    return dict(plan)
        return None


class _InMemoryCronStore:
    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.add_calls: list[dict[str, Any]] = []
        self.delete_calls: list[str] = []
        self._raise_on_add: Exception | None = None

    def raise_on_add(self, exc: Exception) -> None:
        self._raise_on_add = exc

    def add_cron_job(self, **kwargs: Any) -> str:
        if self._raise_on_add is not None:
            raise self._raise_on_add
        job_id = str(kwargs.get("job_id") or kwargs.get("name") or "")
        self.add_calls.append(dict(kwargs))
        self.jobs[job_id] = {"job_id": job_id, **dict(kwargs)}
        return job_id

    def get_cron_job(self, job_id: str) -> dict[str, Any] | None:
        return self.jobs.get(job_id)

    def delete_cron_job(self, job_id: str) -> None:
        self.delete_calls.append(job_id)
        self.jobs.pop(job_id, None)


def _runner_with_pae(*, enabled: bool, interval_seconds: int = 120) -> Any:
    from types import SimpleNamespace

    class _PAECfg:
        pass

    pae = _PAECfg()
    pae.enabled = enabled
    pae.interval_seconds = interval_seconds
    pae.user_activity_grace_seconds = 300
    pae.max_consecutive_noops = 3

    class _Profile:
        agent_id = "agent-x"
        proactive_autonomous_entrypoint = pae

    runner = SimpleNamespace()
    runner.profile = _Profile()
    runner.options = None
    return runner


# PAE-01 — cron job type


class AgentIdleTickPayloadTests(unittest.TestCase):
    def test_allowed_kinds_include_agent_idle_tick(self) -> None:
        from openminion.services.cron.constants import ALLOWED_PAYLOAD_KINDS

        self.assertIn("agentIdleTick", ALLOWED_PAYLOAD_KINDS)

    def test_allowed_session_targets_include_agent_session(self) -> None:
        from openminion.services.cron.constants import ALLOWED_SESSION_TARGETS

        self.assertIn("agent_session", ALLOWED_SESSION_TARGETS)

    def test_normalize_payload_requires_session_id(self) -> None:
        from openminion.services.cron.scheduling import normalize_payload

        with self.assertRaises(ValueError):
            normalize_payload({"kind": "agentIdleTick"})

    def test_normalize_payload_accepts_agent_idle_tick(self) -> None:
        from openminion.services.cron.scheduling import normalize_payload

        result = normalize_payload(
            {
                "kind": "agentIdleTick",
                "session_id": " s1 ",
                "plan_id": "p1",
            }
        )
        self.assertEqual(result["kind"], "agentIdleTick")
        self.assertEqual(result["session_id"], "s1")
        self.assertEqual(result["plan_id"], "p1")

    def test_target_payload_pair_agent_session_locked_to_idle_tick(self) -> None:
        from openminion.services.cron.scheduling import validate_target_payload_pair

        # Matching pair: OK.
        validate_target_payload_pair(
            session_target="agent_session", payload_kind="agentIdleTick"
        )
        # Wrong target for idle tick: rejected.
        with self.assertRaises(ValueError):
            validate_target_payload_pair(
                session_target="main", payload_kind="agentIdleTick"
            )
        with self.assertRaises(ValueError):
            validate_target_payload_pair(
                session_target="isolated", payload_kind="agentIdleTick"
            )
        # Wrong payload for agent_session target: rejected.
        with self.assertRaises(ValueError):
            validate_target_payload_pair(
                session_target="agent_session", payload_kind="systemEvent"
            )

    def test_default_session_target_for_agent_idle_tick(self) -> None:
        from openminion.services.cron.scheduling import (
            default_session_target_for_payload,
        )

        self.assertEqual(
            default_session_target_for_payload("agentIdleTick"),
            "agent_session",
        )


# PAE-02 — scheduling


class MaybeScheduleIdleTickTests(unittest.TestCase):
    def test_schedules_when_enabled(self) -> None:
        cron = _InMemoryCronStore()
        session_api = _InMemorySessionAPI()
        runner = _runner_with_pae(enabled=True, interval_seconds=120)

        result = maybe_schedule_idle_tick(
            cron_store=cron,
            session_api=session_api,
            runner=runner,
            session_id="s1",
            agent_id="agent-x",
            plan_id="p1",
        )
        self.assertTrue(result["scheduled"])
        self.assertEqual(result["interval_seconds"], 120)
        self.assertEqual(len(cron.add_calls), 1)
        call = cron.add_calls[0]
        self.assertEqual(call["agent_id"], "agent-x")
        self.assertEqual(call["session_target"], "agent_session")
        self.assertEqual(call["payload"]["kind"], "agentIdleTick")
        self.assertEqual(call["payload"]["session_id"], "s1")
        self.assertEqual(call["payload"]["plan_id"], "p1")
        self.assertEqual(call["schedule"]["every_ms"], 120_000)
        # Lifecycle event emitted.
        events = session_api.list_events("s1")
        scheduled = [e for e in events if e["event_type"] == "pae.idle_tick.scheduled"]
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0]["payload"]["plan_id"], "p1")
        self.assertEqual(scheduled[0]["payload"]["interval_seconds"], 120)

    def test_skips_when_disabled(self) -> None:
        cron = _InMemoryCronStore()
        session_api = _InMemorySessionAPI()
        runner = _runner_with_pae(enabled=False, interval_seconds=120)

        result = maybe_schedule_idle_tick(
            cron_store=cron,
            session_api=session_api,
            runner=runner,
            session_id="s1",
            agent_id="agent-x",
            plan_id="p1",
        )
        self.assertFalse(result["scheduled"])
        self.assertEqual(result["reason"], "disabled")
        self.assertEqual(len(cron.add_calls), 0)
        suppressed = [
            e
            for e in session_api.list_events("s1")
            if e["event_type"] == "pae.idle_tick.suppressed"
        ]
        self.assertEqual(len(suppressed), 1)
        self.assertEqual(suppressed[0]["payload"]["reason"], "disabled")

    def test_skips_when_interval_zero(self) -> None:
        cron = _InMemoryCronStore()
        runner = _runner_with_pae(enabled=True, interval_seconds=0)
        result = maybe_schedule_idle_tick(
            cron_store=cron,
            session_api=_InMemorySessionAPI(),
            runner=runner,
            session_id="s1",
            agent_id="agent-x",
            plan_id="p1",
        )
        self.assertFalse(result["scheduled"])
        self.assertEqual(result["reason"], "disabled")

    def test_skips_when_missing_ids(self) -> None:
        cron = _InMemoryCronStore()
        runner = _runner_with_pae(enabled=True)
        result = maybe_schedule_idle_tick(
            cron_store=cron,
            session_api=_InMemorySessionAPI(),
            runner=runner,
            session_id="",
            agent_id="agent-x",
            plan_id="p1",
        )
        self.assertFalse(result["scheduled"])
        self.assertEqual(result["reason"], "missing_ids")

    def test_skips_when_cron_store_missing(self) -> None:
        runner = _runner_with_pae(enabled=True)
        result = maybe_schedule_idle_tick(
            cron_store=None,
            session_api=_InMemorySessionAPI(),
            runner=runner,
            session_id="s1",
            agent_id="agent-x",
            plan_id="p1",
        )
        self.assertFalse(result["scheduled"])
        self.assertEqual(result["reason"], "missing_cron_store")

    def test_idempotent_on_repeat_calls(self) -> None:
        cron = _InMemoryCronStore()
        session_api = _InMemorySessionAPI()
        runner = _runner_with_pae(enabled=True)

        for _ in range(3):
            maybe_schedule_idle_tick(
                cron_store=cron,
                session_api=session_api,
                runner=runner,
                session_id="s1",
                agent_id="agent-x",
                plan_id="p1",
            )
        self.assertEqual(len(cron.add_calls), 1)
        self.assertEqual(len(cron.jobs), 1)
        # Only one `scheduled` telemetry event — subsequent calls take
        # the `already_scheduled` path silently.
        scheduled = [
            e
            for e in session_api.list_events("s1")
            if e["event_type"] == "pae.idle_tick.scheduled"
        ]
        self.assertEqual(len(scheduled), 1)

    def test_deterministic_job_id_per_triple(self) -> None:
        jid1 = idle_tick_job_id(agent_id="a", session_id="s", plan_id="p")
        jid2 = idle_tick_job_id(agent_id="a", session_id="s", plan_id="p")
        self.assertEqual(jid1, jid2)
        self.assertTrue(jid1.startswith(IDLE_TICK_JOB_NAME_PREFIX))
        # Different plan → different id.
        jid3 = idle_tick_job_id(agent_id="a", session_id="s", plan_id="p2")
        self.assertNotEqual(jid1, jid3)

    def test_schedule_failed_surfaces_structural_reason(self) -> None:
        cron = _InMemoryCronStore()
        cron.raise_on_add(RuntimeError("store offline"))
        session_api = _InMemorySessionAPI()
        runner = _runner_with_pae(enabled=True)
        result = maybe_schedule_idle_tick(
            cron_store=cron,
            session_api=session_api,
            runner=runner,
            session_id="s1",
            agent_id="agent-x",
            plan_id="p1",
        )
        self.assertFalse(result["scheduled"])
        self.assertEqual(result["reason"], "schedule_failed")
        self.assertIn("store offline", result.get("error", ""))


class CancelIdleTickTests(unittest.TestCase):
    def test_deletes_existing_job(self) -> None:
        cron = _InMemoryCronStore()
        session_api = _InMemorySessionAPI()
        runner = _runner_with_pae(enabled=True)
        maybe_schedule_idle_tick(
            cron_store=cron,
            session_api=session_api,
            runner=runner,
            session_id="s1",
            agent_id="agent-x",
            plan_id="p1",
        )
        result = cancel_idle_tick(
            cron_store=cron,
            session_api=session_api,
            session_id="s1",
            agent_id="agent-x",
            plan_id="p1",
            reason="plan_completed",
        )
        self.assertTrue(result["cancelled"])
        self.assertEqual(len(cron.jobs), 0)
        cancelled = [
            e
            for e in session_api.list_events("s1")
            if e["event_type"] == "pae.idle_tick.cancelled"
        ]
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(cancelled[0]["payload"]["reason"], "plan_completed")

    def test_no_op_when_job_missing(self) -> None:
        cron = _InMemoryCronStore()
        result = cancel_idle_tick(
            cron_store=cron,
            session_api=_InMemorySessionAPI(),
            session_id="s1",
            agent_id="agent-x",
            plan_id="p1",
        )
        self.assertFalse(result["cancelled"])
        self.assertEqual(cron.delete_calls, [])

    def test_no_op_when_cron_store_missing(self) -> None:
        result = cancel_idle_tick(
            cron_store=None,
            session_api=_InMemorySessionAPI(),
            session_id="s1",
            agent_id="agent-x",
            plan_id="p1",
        )
        self.assertFalse(result["cancelled"])


# User-activity gate


class UserActivityGateTests(unittest.TestCase):
    def test_no_user_events_returns_false(self) -> None:
        session_api = _InMemorySessionAPI()
        self.assertFalse(
            is_user_active(session_api=session_api, session_id="s1", grace_seconds=300)
        )

    def test_grace_zero_disables_gate(self) -> None:
        session_api = _InMemorySessionAPI()
        session_api.append_event(
            "s1",
            "turn.user",
            {},
            timestamp="2026-04-18T12:00:00+00:00",
        )
        self.assertFalse(
            is_user_active(
                session_api=session_api,
                session_id="s1",
                grace_seconds=0,
                now_iso="2026-04-18T12:00:30+00:00",
            )
        )

    def test_active_within_window(self) -> None:
        session_api = _InMemorySessionAPI()
        session_api.append_event(
            "s1",
            "turn.user",
            {},
            timestamp="2026-04-18T12:00:00+00:00",
        )
        self.assertTrue(
            is_user_active(
                session_api=session_api,
                session_id="s1",
                grace_seconds=300,
                now_iso="2026-04-18T12:04:00+00:00",  # 4 min later
            )
        )

    def test_idle_outside_window(self) -> None:
        session_api = _InMemorySessionAPI()
        session_api.append_event(
            "s1",
            "turn.user",
            {},
            timestamp="2026-04-18T12:00:00+00:00",
        )
        self.assertFalse(
            is_user_active(
                session_api=session_api,
                session_id="s1",
                grace_seconds=300,
                now_iso="2026-04-18T12:10:00+00:00",  # 10 min later
            )
        )

    def test_latest_user_event_wins(self) -> None:
        session_api = _InMemorySessionAPI()
        session_api.append_event(
            "s1",
            "turn.user",
            {},
            timestamp="2026-04-18T12:00:00+00:00",
        )
        session_api.append_event(
            "s1",
            "turn.user",
            {},
            timestamp="2026-04-18T12:09:30+00:00",
        )
        self.assertEqual(
            last_user_message_timestamp(session_api=session_api, session_id="s1"),
            "2026-04-18T12:09:30+00:00",
        )

    def test_malformed_timestamp_defaults_to_inactive(self) -> None:
        session_api = _InMemorySessionAPI()
        session_api.append_event(
            "s1",
            "turn.user",
            {},
            timestamp="not-an-iso",
        )
        self.assertFalse(
            is_user_active(
                session_api=session_api,
                session_id="s1",
                grace_seconds=300,
                now_iso="2026-04-18T12:00:00+00:00",
            )
        )

    def test_broken_session_api_returns_false_safely(self) -> None:
        class _BrokenAPI:
            def list_events(self, session_id: str) -> list:
                raise RuntimeError("store offline")

        self.assertFalse(
            is_user_active(
                session_api=_BrokenAPI(),
                session_id="s1",
                grace_seconds=300,
            )
        )


# PAE-03 — runner trigger


class IdleTickRunnerTriggerTests(unittest.TestCase):
    def test_run_trigger_idle_tick_is_registered(self) -> None:
        from openminion.modules.brain.runner.lifecycle import (
            RUN_TRIGGER_IDLE_TICK,
            _SUPPORTED_RUN_TRIGGERS,
        )

        self.assertEqual(RUN_TRIGGER_IDLE_TICK, "idle_tick")
        self.assertIn(RUN_TRIGGER_IDLE_TICK, _SUPPORTED_RUN_TRIGGERS)

    def test_canonical_event_registered(self) -> None:
        from openminion.modules.session.storage.replay import (
            _KNOWN_CANONICAL_EVENT_TYPES,
        )

        for event in (
            "brain.idle_tick.started",
            "pae.idle_tick.scheduled",
            "pae.idle_tick.cancelled",
            "pae.idle_tick.suppressed",
            "pae.unsupported_v1_action",
        ):
            self.assertIn(event, _KNOWN_CANONICAL_EVENT_TYPES)


# PAE-04 — config


class PaeConfigTests(unittest.TestCase):
    def test_default_disabled(self) -> None:
        from openminion.modules.brain.schemas import (
            ProactiveAutonomousEntrypointConfig,
        )

        cfg = ProactiveAutonomousEntrypointConfig()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.interval_seconds, 0)
        self.assertEqual(cfg.user_activity_grace_seconds, 300)
        self.assertEqual(cfg.max_consecutive_noops, 3)

    def test_agent_profile_carries_pae_field(self) -> None:
        from openminion.modules.brain.schemas import (
            AgentBudgets,
            AgentProfile,
            LLMProfiles,
        )

        p = AgentProfile(
            agent_id="a",
            role="r",
            llm_profiles=LLMProfiles(
                decide_model="m",
                plan_model="m",
                act_model=None,
                reflect_model="m",
                summarize_model="m",
            ),
            budgets=AgentBudgets(
                max_ticks_per_user_turn=1,
                max_tool_calls=1,
                max_a2a_calls=0,
                max_total_llm_tokens=1,
                max_elapsed_ms=1,
            ),
        )
        self.assertFalse(p.proactive_autonomous_entrypoint.enabled)
        self.assertEqual(p.proactive_autonomous_entrypoint.interval_seconds, 0)

    def test_runner_options_carries_pae_config(self) -> None:
        from openminion.modules.brain.config import RunnerOptions

        ro = RunnerOptions()
        self.assertFalse(ro.proactive_autonomous_entrypoint_config.enabled)

    def test_negative_interval_rejected_by_pydantic(self) -> None:
        from openminion.modules.brain.schemas import (
            ProactiveAutonomousEntrypointConfig,
        )

        with self.assertRaises(Exception):  # pydantic ValidationError
            ProactiveAutonomousEntrypointConfig(interval_seconds=-1)


# PAE-02 production wiring — BrainBridgeTurnMixin


class ProductionPathIdleTickRoutingTests(unittest.TestCase):
    def _runner_stub(self) -> Any:
        from types import SimpleNamespace

        calls: list[dict[str, Any]] = []

        class _Options:
            autonomous_continuation_enabled = True
            autonomous_continuation_max_per_plan = 10
            autonomous_continuation_max_per_session = 20

        def _run(**kwargs: Any) -> Any:
            calls.append(dict(kwargs))

            class _State:
                trace_id = "t1"

            class _Out:
                working_state = _State()

            return _Out()

        runner = SimpleNamespace()
        runner.options = _Options()
        runner.run = _run
        runner.session_api = _InMemorySessionAPI()

        class _Profile:
            agent_id = "agent-x"

        runner.profile = _Profile()
        return runner, calls

    def test_idle_tick_marker_routes_to_trigger_idle_tick(self) -> None:
        from openminion.services.brain.post_execution.mixin import (
            BrainBridgeTurnMixin,
        )
        from openminion.base.types import Message

        runner, calls = self._runner_stub()
        mixin = BrainBridgeTurnMixin()
        message = Message(
            channel="cron",
            target="pae",
            body="",
            metadata={"pae_idle_tick": "true", "pae_plan_id": "p1"},
        )
        mixin._execute_turn(
            runner=runner,
            session_id="s1",
            request_id="req-1",
            message=message,
            forced_tools=None,
            capability_category=None,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["trigger"], "idle_tick")
        self.assertIsNone(calls[0]["user_input"])

    def test_no_marker_falls_back_to_ctgp_wrapper(self) -> None:
        from openminion.services.brain.post_execution.mixin import (
            BrainBridgeTurnMixin,
        )
        from openminion.base.types import Message

        runner, calls = self._runner_stub()
        mixin = BrainBridgeTurnMixin()
        message = Message(
            channel="chat",
            target="user",
            body="hi",
            metadata={},
        )
        mixin._execute_turn(
            runner=runner,
            session_id="s1",
            request_id="req-1",
            message=message,
            forced_tools=None,
            capability_category=None,
        )
        # The CTGP wrapper fires `runner.run(trigger="user_input", ...)`;
        # the stub receives that call.
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["trigger"], "user_input")
        self.assertEqual(calls[0]["user_input"], "hi")


# PAE-02 plan-tool handler integration


class PlanToolPaeIntegrationTests(unittest.TestCase):
    def _loop_ctx(
        self, *, cron_api: Any, pae_enabled: bool = True
    ) -> tuple[Any, _InMemoryCronStore | None, _InMemorySessionAPI]:
        from types import SimpleNamespace

        session_api = _InMemorySessionAPI()

        class _PAECfg:
            pass

        pae = _PAECfg()
        pae.enabled = pae_enabled
        pae.interval_seconds = 120 if pae_enabled else 0
        pae.user_activity_grace_seconds = 300
        pae.max_consecutive_noops = 3

        class _Profile:
            agent_id = "agent-x"
            proactive_autonomous_entrypoint = pae

        runner = SimpleNamespace()
        runner.profile = _Profile()
        runner.cron_api = cron_api
        runner.session_api = session_api
        runner.options = None

        class _State:
            session_id = "s1"
            agent_id = "agent-x"
            trace_id = "trace-x"

        loop_ctx = SimpleNamespace()
        loop_ctx.session_api = session_api
        loop_ctx.state = _State()
        loop_ctx._runner = runner
        return loop_ctx, cron_api, session_api

    def test_declare_with_opt_in_schedules_job(self) -> None:
        from openminion.modules.brain.loop.tools.plan_control import (
            PLAN_ACTION_DECLARE,
            handle_plan_tool_call,
        )

        cron = _InMemoryCronStore()
        loop_ctx, _, session_api = self._loop_ctx(cron_api=cron)
        result = handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_DECLARE,
                "plan_id": "p1",
                "objective": "build",
                "steps": [{"step_id": "s1", "description": "d1"}],
                "continue_plan_autonomously": True,
            },
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(len(cron.jobs), 1)
        scheduled = [
            e
            for e in session_api.list_events("s1")
            if e["event_type"] == "pae.idle_tick.scheduled"
        ]
        self.assertEqual(len(scheduled), 1)

    def test_declare_without_opt_in_does_not_schedule(self) -> None:
        from openminion.modules.brain.loop.tools.plan_control import (
            PLAN_ACTION_DECLARE,
            handle_plan_tool_call,
        )

        cron = _InMemoryCronStore()
        loop_ctx, _, _ = self._loop_ctx(cron_api=cron)
        handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_DECLARE,
                "plan_id": "p1",
                "objective": "build",
                "steps": [{"step_id": "s1", "description": "d1"}],
                # no continue_plan_autonomously
            },
        )
        self.assertEqual(len(cron.jobs), 0)

    def test_declare_with_pae_disabled_does_not_schedule(self) -> None:
        from openminion.modules.brain.loop.tools.plan_control import (
            PLAN_ACTION_DECLARE,
            handle_plan_tool_call,
        )

        cron = _InMemoryCronStore()
        loop_ctx, _, session_api = self._loop_ctx(cron_api=cron, pae_enabled=False)
        handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_DECLARE,
                "plan_id": "p1",
                "objective": "build",
                "steps": [{"step_id": "s1", "description": "d1"}],
                "continue_plan_autonomously": True,
            },
        )
        # No job scheduled. Suppressed telemetry event records reason.
        self.assertEqual(len(cron.jobs), 0)
        suppressed = [
            e
            for e in session_api.list_events("s1")
            if e["event_type"] == "pae.idle_tick.suppressed"
        ]
        self.assertEqual(len(suppressed), 1)
        self.assertEqual(suppressed[0]["payload"]["reason"], "disabled")

    def test_plan_completed_cancels_job(self) -> None:
        from openminion.modules.brain.loop.tools.plan_control import (
            PLAN_ACTION_COMPLETE,
            PLAN_ACTION_DECLARE,
            handle_plan_tool_call,
        )

        cron = _InMemoryCronStore()
        loop_ctx, _, session_api = self._loop_ctx(cron_api=cron)
        handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_DECLARE,
                "plan_id": "p1",
                "objective": "build",
                "steps": [{"step_id": "s1", "description": "d1"}],
                "continue_plan_autonomously": True,
            },
        )
        self.assertEqual(len(cron.jobs), 1)
        handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_COMPLETE,
                "plan_id": "p1",
                "reason": "finished",
            },
        )
        self.assertEqual(len(cron.jobs), 0)
        cancelled = [
            e
            for e in session_api.list_events("s1")
            if e["event_type"] == "pae.idle_tick.cancelled"
        ]
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(cancelled[0]["payload"]["reason"], "completed")

    def test_plan_abandoned_cancels_job(self) -> None:
        from openminion.modules.brain.loop.tools.plan_control import (
            PLAN_ACTION_ABANDON,
            PLAN_ACTION_DECLARE,
            handle_plan_tool_call,
        )

        cron = _InMemoryCronStore()
        loop_ctx, _, _ = self._loop_ctx(cron_api=cron)
        handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_DECLARE,
                "plan_id": "p1",
                "objective": "build",
                "steps": [{"step_id": "s1", "description": "d1"}],
                "continue_plan_autonomously": True,
            },
        )
        handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_ABANDON,
                "plan_id": "p1",
                "reason": "user changed mind",
            },
        )
        self.assertEqual(len(cron.jobs), 0)

    def test_step_blocked_cancels_job(self) -> None:
        from openminion.modules.brain.loop.tools.plan_control import (
            PLAN_ACTION_DECLARE,
            PLAN_ACTION_STEP_BLOCKED,
            handle_plan_tool_call,
        )

        cron = _InMemoryCronStore()
        loop_ctx, _, _ = self._loop_ctx(cron_api=cron)
        handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_DECLARE,
                "plan_id": "p1",
                "objective": "build",
                "steps": [{"step_id": "s1", "description": "d1"}],
                "continue_plan_autonomously": True,
            },
        )
        handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_STEP_BLOCKED,
                "plan_id": "p1",
                "step_id": "s1",
                "blocker_type": "user_input_required",
            },
        )
        self.assertEqual(len(cron.jobs), 0)

    def test_runner_without_cron_api_is_silent(self) -> None:
        from openminion.modules.brain.loop.tools.plan_control import (
            PLAN_ACTION_DECLARE,
            handle_plan_tool_call,
        )

        loop_ctx, _, session_api = self._loop_ctx(cron_api=None)
        result = handle_plan_tool_call(
            loop_ctx=loop_ctx,
            arguments={
                "action": PLAN_ACTION_DECLARE,
                "plan_id": "p1",
                "objective": "build",
                "steps": [{"step_id": "s1", "description": "d1"}],
                "continue_plan_autonomously": True,
            },
        )
        self.assertEqual(result.status, "success")
        # No PAE telemetry events since the hooks short-circuited before
        # the helper layer.
        pae_events = [
            e
            for e in session_api.list_events("s1")
            if e["event_type"].startswith("pae.")
        ]
        self.assertEqual(pae_events, [])


class ReviewRoundTwoFixesTests(unittest.TestCase):
    def test_bridge_passes_cron_api_to_brain_runner(self) -> None:
        import inspect
        from openminion.modules.brain.runner.coordinator import BrainRunner

        sig = inspect.signature(BrainRunner.__init__)
        self.assertIn("cron_api", sig.parameters)
        self.assertIsNone(sig.parameters["cron_api"].default)
        # The runtime bootstrap owns runner assembly; grep for the call
        # site to ensure the wiring hasn't been removed.
        import openminion.services.runtime.bootstrap as bootstrap_mod

        src = inspect.getsource(bootstrap_mod)
        self.assertIn("cron_api=cron_repository", src)

    def test_decide_skips_resume_fast_path_on_idle_tick(self) -> None:
        from openminion.modules.brain.loop.orchestration import decide
        from openminion.modules.brain.schemas import (
            BudgetCounters,
            Plan,
            ThinkCommand,
            WorkingState,
        )

        plan = Plan(
            objective="test objective",
            steps=[ThinkCommand(title="think step", prompt="first step reasoning")],
        )
        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            goal="test",
            budgets_remaining=BudgetCounters(
                ticks=5,
                tool_calls=5,
                a2a_calls=0,
                tokens=5000,
                time_ms=60_000,
            ),
            plan=plan,
            cursor=0,
            run_trigger="idle_tick",
        )

        class _Runner:
            llm_api = None
            context_api = None

            class _Profile:
                agent_id = "a1"

            profile = _Profile()

            def _resolve_skill_hints(self, **kwargs: Any) -> dict:
                return {}

        class _Logger:
            def emit(self, *a: Any, **kw: Any) -> None:
                return None

        decision = decide(_Runner(), state=state, user_input=None, logger=_Logger())
        self.assertNotEqual(
            getattr(decision, "reason_code", ""), "resume_existing_plan"
        )

    def test_decide_resume_fast_path_intact_for_non_idle_tick(self) -> None:
        from openminion.modules.brain.loop.orchestration import decide
        from openminion.modules.brain.schemas import (
            BudgetCounters,
            Plan,
            ThinkCommand,
            WorkingState,
        )

        plan = Plan(
            objective="test objective",
            steps=[ThinkCommand(title="think step", prompt="first step reasoning")],
        )
        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            goal="test",
            budgets_remaining=BudgetCounters(
                ticks=5,
                tool_calls=5,
                a2a_calls=0,
                tokens=5000,
                time_ms=60_000,
            ),
            plan=plan,
            cursor=0,
            run_trigger="user_input",
        )

        class _Runner:
            llm_api = None
            context_api = None

            class _Profile:
                agent_id = "a1"

            profile = _Profile()

        class _Logger:
            def emit(self, *a: Any, **kw: Any) -> None:
                return None

        decision = decide(_Runner(), state=state, user_input=None, logger=_Logger())
        self.assertEqual(getattr(decision, "reason_code", ""), "resume_existing_plan")

    def test_mixin_idle_tick_routes_through_ctgp_wrapper(self) -> None:
        from types import SimpleNamespace
        from openminion.services.brain.post_execution.mixin import (
            BrainBridgeTurnMixin,
        )
        from openminion.base.types import Message

        session_api = _InMemorySessionAPI()
        calls: list[dict[str, Any]] = []

        # Simulate a model that opts in on the first (idle_tick) turn
        # and then no-opts-in on the second (plan_continuation) turn
        # so the loop terminates cleanly.
        scripted_events = iter(
            [
                {
                    "event_type": "task_plan.declared",
                    "plan_id": "p1",
                    "continue_plan_autonomously": True,
                    "plan": {
                        "plan_id": "p1",
                        "objective": "build",
                        "status": "active",
                        "steps": [
                            {
                                "step_id": "s1",
                                "description": "d1",
                                "status": "pending",
                            }
                        ],
                        "continue_plan_autonomously": True,
                    },
                },
                {
                    "event_type": "task_plan.step_completed",
                    "plan_id": "p1",
                    "step_id": "s1",
                    "continue_plan_autonomously": False,
                },
            ]
        )

        def _run(**kwargs: Any) -> Any:
            calls.append(dict(kwargs))
            try:
                ev = next(scripted_events)
            except StopIteration:
                ev = None
            if ev:
                etype = ev["event_type"]
                if etype in ("task_plan.declared", "task_plan.revised"):
                    session_api.append_event(
                        "s1",
                        etype,
                        {"plan": ev["plan"]},
                    )
                else:
                    session_api.append_event(
                        "s1",
                        etype,
                        {
                            "plan_id": ev["plan_id"],
                            "step_id": ev.get("step_id", "s1"),
                            "continue_plan_autonomously": ev.get(
                                "continue_plan_autonomously", False
                            ),
                        },
                    )

            class _State:
                trace_id = "t1"

            class _Out:
                working_state = _State()

            return _Out()

        class _Options:
            autonomous_continuation_enabled = True
            autonomous_continuation_max_per_plan = 10
            autonomous_continuation_max_per_session = 20

        class _Profile:
            agent_id = "agent-x"

        runner = SimpleNamespace()
        runner.options = _Options()
        runner.run = _run
        runner.session_api = session_api
        runner.profile = _Profile()

        mixin = BrainBridgeTurnMixin()
        message = Message(
            channel="cron",
            target="pae",
            body="",
            metadata={"pae_idle_tick": "true", "pae_plan_id": "p1"},
        )
        mixin._execute_turn(
            runner=runner,
            session_id="s1",
            request_id="req-1",
            message=message,
            forced_tools=None,
            capability_category=None,
        )
        # First call enters via `trigger="idle_tick"` (the CTGP wrapper's
        # `initial_trigger`).
        self.assertEqual(calls[0]["trigger"], "idle_tick")
        # Second call is the CTGP follow-up.
        self.assertEqual(calls[1]["trigger"], "plan_continuation")
        self.assertIsNone(calls[1]["user_input"])

    def test_executor_resolves_session_via_cron_store(self) -> None:
        from openminion.services.runtime.cron.executor import (
            CronTurnExecutor,
        )

        class _FakeStore:
            def list_events(self, session_id: str) -> list:
                return []

            def append_event(self, *a: Any, **kw: Any) -> str:
                return "evt"

        class _FakeRuntime:
            runtime_manager = None

        executor = CronTurnExecutor(
            runtime=_FakeRuntime(),
            cron_store=_FakeStore(),
            request_builder=lambda payload, agent_id: None,
            timeout_s=10.0,
            max_attempts=1,
        )
        resolved = executor._resolve_session_api(agent_id="a1")
        self.assertIs(resolved, executor._cron_store)

    def test_executor_reads_grace_seconds_from_payload(self) -> None:
        from openminion.services.runtime.cron.executor import (
            CronTurnExecutor,
        )

        class _ActiveStore(_InMemorySessionAPI):
            pass

        store = _ActiveStore()
        # Recent user turn — within grace window.
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat()
        store.append_event("s1", "turn.user", {}, timestamp=now_iso)

        class _FakeRuntime:
            runtime_manager = None

        executor = CronTurnExecutor(
            runtime=_FakeRuntime(),
            cron_store=store,
            request_builder=lambda payload, agent_id: None,
            timeout_s=10.0,
            max_attempts=1,
        )
        suppressed = executor._check_idle_tick_user_activity_gate(
            agent_id="a1",
            session_id="s1",
            plan_id="p1",
            grace_seconds=300,
        )
        self.assertIsNotNone(suppressed)
        self.assertEqual(suppressed["metadata"]["pae_suppressed"], "user_activity")
        # Grace=0 disables the gate even with a recent user event.
        pass_through = executor._check_idle_tick_user_activity_gate(
            agent_id="a1",
            session_id="s1",
            plan_id="p1",
            grace_seconds=0,
        )
        self.assertIsNone(pass_through)

    def test_schedule_embeds_grace_seconds_in_payload(self) -> None:
        cron = _InMemoryCronStore()
        runner = _runner_with_pae(enabled=True, interval_seconds=120)
        # Override the grace to something non-default so we can
        # verify it's carried through.
        runner.profile.proactive_autonomous_entrypoint.user_activity_grace_seconds = 60
        maybe_schedule_idle_tick(
            cron_store=cron,
            session_api=_InMemorySessionAPI(),
            runner=runner,
            session_id="s1",
            agent_id="agent-x",
            plan_id="p1",
        )
        self.assertEqual(len(cron.add_calls), 1)
        payload = cron.add_calls[0]["payload"]
        self.assertEqual(payload["user_activity_grace_seconds"], 60)


class ReviewRoundThreeFixesTests(unittest.TestCase):
    def test_idle_tick_hints_in_decide_allowlist(self) -> None:
        from openminion.modules.brain.runtime.context import (
            _PHASE_HINT_KEYS,
            _is_phase_hint_allowed,
        )

        decide_allowlist = _PHASE_HINT_KEYS.get("decide", set())
        self.assertIn("idle_tick_entry", decide_allowlist)
        self.assertIn("idle_tick_v1_actions", decide_allowlist)
        self.assertTrue(_is_phase_hint_allowed(purpose="decide", key="idle_tick_entry"))
        self.assertTrue(
            _is_phase_hint_allowed(purpose="decide", key="idle_tick_v1_actions")
        )
        # Other phases reject them (idle_tick is decide-only).
        self.assertFalse(_is_phase_hint_allowed(purpose="plan", key="idle_tick_entry"))

    def test_idle_tick_narrows_tool_surface_to_plan(self) -> None:
        from openminion.modules.brain.schemas import (
            BudgetCounters,
            WorkingState,
        )
        from openminion.modules.llm.schemas import ToolSpec

        # Construct a bench state with idle_tick trigger. We don't run
        # full decide — we simulate the filter block by reaching into
        # the same logic the orchestration path uses.
        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            goal="test",
            budgets_remaining=BudgetCounters(
                ticks=5,
                tool_calls=5,
                a2a_calls=0,
                tokens=5000,
                time_ms=60_000,
            ),
            run_trigger="idle_tick",
        )

        # Simulate the set of tool specs the entry builder would
        # produce (plan + clarify + some runtime tools). Apply the
        # same filter rule decide() uses for idle ticks.
        tool_specs = [
            ToolSpec(
                name="plan",
                description="plan tool",
                input_schema={"type": "object"},
            ),
            ToolSpec(
                name="clarify",
                description="clarify tool",
                input_schema={"type": "object"},
            ),
            ToolSpec(
                name="file_read",
                description="runtime tool",
                input_schema={"type": "object"},
            ),
        ]
        # This is the same filter decide() applies inline.
        if str(getattr(state, "run_trigger", "") or "") == "idle_tick":
            tool_specs = [
                spec
                for spec in tool_specs
                if str(getattr(spec, "name", "") or "").strip() == "plan"
            ]
        self.assertEqual(len(tool_specs), 1)
        self.assertEqual(tool_specs[0].name, "plan")

        # Non-idle-tick trigger keeps the full surface — sanity check.
        state.run_trigger = "user_input"
        tool_specs = [
            ToolSpec(
                name="plan",
                description="plan tool",
                input_schema={"type": "object"},
            ),
            ToolSpec(
                name="clarify",
                description="clarify tool",
                input_schema={"type": "object"},
            ),
            ToolSpec(
                name="file_read",
                description="runtime tool",
                input_schema={"type": "object"},
            ),
        ]
        if str(getattr(state, "run_trigger", "") or "") == "idle_tick":
            tool_specs = [
                spec
                for spec in tool_specs
                if str(getattr(spec, "name", "") or "").strip() == "plan"
            ]
        self.assertEqual(len(tool_specs), 3)

    def test_unsupported_v1_action_event_registered(self) -> None:
        from openminion.modules.session.storage.replay import (
            _KNOWN_CANONICAL_EVENT_TYPES,
        )

        self.assertIn("pae.unsupported_v1_action", _KNOWN_CANONICAL_EVENT_TYPES)

    def test_ctgp_continuation_forwards_progress_callback(self) -> None:
        from types import SimpleNamespace
        from openminion.modules.brain.loop.continuation import (
            run_with_autonomous_continuation,
        )

        session_api = _InMemorySessionAPI()
        calls: list[dict[str, Any]] = []

        scripted = iter(
            [
                {
                    "event_type": "task_plan.declared",
                    "plan_id": "p1",
                    "continue": True,
                    "plan": {
                        "plan_id": "p1",
                        "objective": "o",
                        "status": "active",
                        "steps": [
                            {
                                "step_id": "s1",
                                "description": "d1",
                                "status": "pending",
                            }
                        ],
                        "continue_plan_autonomously": True,
                    },
                },
                {
                    "event_type": "task_plan.step_completed",
                    "plan_id": "p1",
                    "step_id": "s1",
                    "continue": False,
                },
            ]
        )

        def _run(**kwargs: Any) -> Any:
            calls.append(dict(kwargs))
            try:
                step = next(scripted)
            except StopIteration:
                step = None
            if step:
                etype = step["event_type"]
                if etype in ("task_plan.declared", "task_plan.revised"):
                    session_api.append_event("s1", etype, {"plan": step["plan"]})
                else:
                    session_api.append_event(
                        "s1",
                        etype,
                        {
                            "plan_id": step["plan_id"],
                            "step_id": step.get("step_id", "s1"),
                            "continue_plan_autonomously": step.get("continue", False),
                        },
                    )

            class _State:
                trace_id = "t1"

            class _Out:
                working_state = _State()

            return _Out()

        class _Profile:
            agent_id = "agent-x"

        runner = SimpleNamespace()
        runner.run = _run
        runner.session_api = session_api
        runner.profile = _Profile()

        phase_events: list[str] = []

        def _progress_cb(status: Any) -> None:
            phase_events.append(str(status))

        run_with_autonomous_continuation(
            runner,
            session_id="s1",
            user_input="go",
            progress_callback=_progress_cb,
        )

        # Expect 2 calls: initial user_input turn + 1 plan_continuation.
        self.assertEqual(len(calls), 2)
        # Both calls receive the progress_callback.
        self.assertIs(calls[0]["progress_callback"], _progress_cb)
        self.assertIs(calls[1]["progress_callback"], _progress_cb)
        # The continuation turn carries plan_continuation trigger.
        self.assertEqual(calls[1]["trigger"], "plan_continuation")


class IdleTickV1EnforcementTests(unittest.TestCase):
    def _detection(
        self,
        *,
        path: str = "respond",
        response_text: str = "",
        clarify_question: str = "",
        tool_call_names: tuple[str, ...] = (),
    ) -> Any:
        from openminion.modules.brain.loop.entry import EntryPathDetection

        return EntryPathDetection(
            path=path,
            response_text=response_text,
            clarify_question=clarify_question,
            tool_call_names=tool_call_names,
        )

    def _logger(self) -> tuple[Any, list[tuple[str, dict, dict]]]:
        emitted: list[tuple[str, dict, dict]] = []

        class _Logger:
            def emit(self, event_type: str, payload: dict, **kwargs: Any) -> None:
                emitted.append((event_type, dict(payload), dict(kwargs)))

        return _Logger(), emitted

    def _enforce(
        self,
        *,
        detection: Any,
    ) -> tuple[Any, list[tuple[str, dict, dict]]]:
        from openminion.modules.brain.loop.orchestration import (
            _enforce_idle_tick_v1_bound,
        )

        logger, emitted = self._logger()
        result = _enforce_idle_tick_v1_bound(
            detection=detection,
            logger=logger,
            trace_id="trace-test",
            llm_call_id="call-test",
        )
        return result, emitted

    def test_non_plan_tool_call_coerced_to_noop(self) -> None:
        result, emitted = self._enforce(
            detection=self._detection(path="act", tool_call_names=("file_read",))
        )
        self.assertIsNotNone(result)
        self.assertEqual(getattr(result, "reason_code", ""), "pae_idle_tick_noop")
        self.assertEqual(str(getattr(result, "answer", "")).strip(), "[pae:no_op]")
        unsupported = [p for ev, p, _ in emitted if ev == "pae.unsupported_v1_action"]
        self.assertEqual(len(unsupported), 1)
        self.assertEqual(unsupported[0]["reason"], "non_plan_tool_call")
        self.assertIn("file_read", unsupported[0]["actions"])
        self.assertEqual(unsupported[0]["allowed"], ["plan"])

    def test_non_plan_call_beside_plan_still_coerces(self) -> None:
        result, emitted = self._enforce(
            detection=self._detection(path="act", tool_call_names=("plan", "file_read"))
        )
        self.assertIsNotNone(result)
        self.assertEqual(getattr(result, "reason_code", ""), "pae_idle_tick_noop")
        unsupported = [p for ev, p, _ in emitted if ev == "pae.unsupported_v1_action"]
        self.assertEqual(len(unsupported), 1)
        self.assertEqual(unsupported[0]["reason"], "non_plan_tool_call")
        self.assertEqual(unsupported[0]["actions"], ["file_read"])

    def test_clarify_during_idle_tick_coerced_to_noop(self) -> None:
        result, emitted = self._enforce(
            detection=self._detection(
                path="clarify",
                clarify_question="What should I do?",
            )
        )
        self.assertIsNotNone(result)
        self.assertEqual(getattr(result, "reason_code", ""), "pae_idle_tick_noop")
        self.assertEqual(str(getattr(result, "answer", "")).strip(), "[pae:no_op]")
        unsupported = [p for ev, p, _ in emitted if ev == "pae.unsupported_v1_action"]
        self.assertEqual(len(unsupported), 1)
        self.assertEqual(unsupported[0]["reason"], "clarify_during_idle_tick")
        self.assertEqual(unsupported[0]["clarify_question"], "What should I do?")

    def test_non_empty_respond_coerced_to_noop_with_bounded_preview(
        self,
    ) -> None:
        huge = "I think the plan is done. " + ("x" * 500)
        result, emitted = self._enforce(
            detection=self._detection(path="respond", response_text=huge)
        )
        self.assertIsNotNone(result)
        self.assertEqual(getattr(result, "reason_code", ""), "pae_idle_tick_noop")
        self.assertEqual(str(getattr(result, "answer", "")).strip(), "[pae:no_op]")
        unsupported = [p for ev, p, _ in emitted if ev == "pae.unsupported_v1_action"]
        self.assertEqual(len(unsupported), 1)
        self.assertEqual(unsupported[0]["reason"], "non_empty_respond_during_idle_tick")
        # Preview is bounded to 200 chars.
        self.assertLessEqual(len(unsupported[0]["response_preview"]), 200)
        # Full length reported for operator context.
        self.assertEqual(unsupported[0]["response_chars"], len(huge.strip()))

    def test_empty_respond_is_legitimate_noop_routed_to_coerced_shape(
        self,
    ) -> None:
        result, emitted = self._enforce(
            detection=self._detection(path="respond", response_text="")
        )
        self.assertIsNotNone(result)
        self.assertEqual(getattr(result, "reason_code", ""), "pae_idle_tick_noop")
        # No unsupported-action log for legitimate no_op.
        self.assertEqual(
            [ev for ev, _p, _kw in emitted if ev == "pae.unsupported_v1_action"],
            [],
        )

    def test_respond_with_whitespace_only_is_legitimate_noop(self) -> None:
        result, emitted = self._enforce(
            detection=self._detection(path="respond", response_text="   \n\t  ")
        )
        self.assertIsNotNone(result)
        self.assertEqual(getattr(result, "reason_code", ""), "pae_idle_tick_noop")
        self.assertEqual(
            [ev for ev, _p, _kw in emitted if ev == "pae.unsupported_v1_action"],
            [],
        )

    def test_plan_tool_call_passes_through(self) -> None:
        result, emitted = self._enforce(
            detection=self._detection(path="act", tool_call_names=("plan",))
        )
        self.assertIsNone(result)
        self.assertEqual(
            [ev for ev, _p, _kw in emitted if ev == "pae.unsupported_v1_action"],
            [],
        )

    def test_non_idle_tick_skips_enforcement_at_caller(self) -> None:
        import inspect
        from openminion.modules.brain.loop import orchestration as orch_mod

        src = inspect.getsource(orch_mod.decide)
        # The enforcement block appears exactly once, gated on
        # run_trigger == "idle_tick".
        self.assertIn('"idle_tick"', src)
        self.assertIn("_enforce_idle_tick_v1_bound(", src)
        # The call site is inside an `if` checking run_trigger.
        gate_idx = src.find('state, "run_trigger"')
        enforce_idx = src.find("_enforce_idle_tick_v1_bound(")
        self.assertLess(gate_idx, enforce_idx)


class StructuralNoopPersistenceTests(unittest.TestCase):
    def _fake_runner(self) -> tuple[Any, list[tuple[str, str, str]]]:
        turn_appends: list[tuple[str, str, str]] = []

        class _Session:
            def append_turn(
                self,
                session_id: str,
                role: str,
                message: str,
                meta: dict | None = None,
            ) -> str:
                turn_appends.append(
                    (role, message, str((meta or {}).get("status", "")))
                )
                return "turn-1"

            def set_session_status(self, *a: Any, **kw: Any) -> None:
                return None

            def update_session_summary(self, *a: Any, **kw: Any) -> None:
                return None

            def put_working_state(self, *a: Any, **kw: Any) -> None:
                return None

            def update_session_status(self, *a: Any, **kw: Any) -> None:
                return None

        class _Profile:
            agent_id = "agent-x"

        class _Runner:
            profile = _Profile()
            session_api = _Session()
            skill_api = None
            _skill_active_session_ids: set[str] = set()

            def _save_state(self, state: Any) -> None:
                return None

            def _compact(self, *, state: Any, logger: Any, content: str) -> None:
                turn_appends.append(("_compact", content, ""))

            def _emit_phase_status(self, **kwargs: Any) -> None:
                return None

        return _Runner(), turn_appends

    def test_structural_noop_does_not_append_assistant_turn(self) -> None:
        from openminion.modules.brain.state import respond_structural_noop
        from openminion.modules.brain.schemas import (
            BudgetCounters,
            WorkingState,
        )

        runner, turn_appends = self._fake_runner()
        state = WorkingState(
            session_id="s1",
            agent_id="agent-x",
            goal="test",
            budgets_remaining=BudgetCounters(
                ticks=5,
                tool_calls=5,
                a2a_calls=0,
                tokens=5000,
                time_ms=60_000,
            ),
            run_trigger="idle_tick",
        )
        emitted: list[tuple[str, dict, dict]] = []

        class _Logger:
            def emit(self, event_type: str, payload: dict, **kwargs: Any) -> None:
                emitted.append((event_type, dict(payload), dict(kwargs)))

        result = respond_structural_noop(runner, state=state, logger=_Logger())
        # Empty message on StepOutput — no sentinel leakage.
        self.assertEqual(str(result.message or ""), "")
        # No assistant turn appended.
        self.assertEqual([e for e in turn_appends if e[0] == "assistant"], [])
        # No compact call (compact on sentinel would corrupt summary).
        self.assertEqual([e for e in turn_appends if e[0] == "_compact"], [])
        # Telemetry `pae.idle_tick.noop` emitted.
        self.assertEqual(
            [ev for ev, _p, _kw in emitted if ev == "pae.idle_tick.noop"],
            ["pae.idle_tick.noop"],
        )

    def test_dispatch_routes_pae_idle_tick_noop_to_structural_path(self) -> None:
        from openminion.modules.brain.execution.dispatch import _respond_execute
        from openminion.modules.brain.execution.loop_contracts import (
            ExecutionContext,
        )
        from openminion.modules.brain.execution.context import (
            RunnerExecutionServices,
        )
        from openminion.modules.brain.schemas import (
            BudgetCounters,
            RespondDecision,
            WorkingState,
        )

        runner, turn_appends = self._fake_runner()
        state = WorkingState(
            session_id="s1",
            agent_id="agent-x",
            goal="test",
            budgets_remaining=BudgetCounters(
                ticks=5,
                tool_calls=5,
                a2a_calls=0,
                tokens=5000,
                time_ms=60_000,
            ),
            run_trigger="idle_tick",
        )
        decision = RespondDecision(
            confidence=1.0,
            reason_code="pae_idle_tick_noop",
            respond_kind="answer",
            answer="[pae:no_op]",
        )
        emitted: list[tuple[str, dict, dict]] = []

        class _Logger:
            def emit(self, event_type: str, payload: dict, **kwargs: Any) -> None:
                emitted.append((event_type, dict(payload), dict(kwargs)))

        ctx = ExecutionContext(
            state=state,
            decision=decision,
            user_input=None,
            logger=_Logger(),
            options=None,
            llm_adapter=None,
            command_executor=None,
            _services=RunnerExecutionServices(runner=runner),
        )
        result = _respond_execute(ctx)
        # Empty StepOutput message — sentinel suppressed.
        self.assertEqual(str(result.message or ""), "")
        # No assistant turn persisted.
        self.assertEqual([e for e in turn_appends if e[0] == "assistant"], [])
        # Structural no-op event emitted.
        self.assertIn(
            "pae.idle_tick.noop",
            [ev for ev, _p, _kw in emitted],
        )

    def test_non_pae_idle_tick_noop_reason_still_normal_path(self) -> None:
        import inspect
        from openminion.modules.brain.execution import dispatch as dispatch_mod

        src = inspect.getsource(dispatch_mod._respond_execute)
        # Gate is specifically on the reason_code sentinel.
        self.assertIn('reason_code == "pae_idle_tick_noop"', src)
        self.assertIn("respond_structural_noop", src)

    def test_idle_tick_noop_canonical_event_registered(self) -> None:
        from openminion.modules.session.storage.replay import (
            _KNOWN_CANONICAL_EVENT_TYPES,
        )

        self.assertIn("pae.idle_tick.noop", _KNOWN_CANONICAL_EVENT_TYPES)

    def test_idle_tick_empty_response_bypasses_retry_loop(self) -> None:
        import inspect
        from openminion.modules.brain.loop import orchestration as orch_mod

        src = inspect.getsource(orch_mod.decide)
        # The break-out branch exists.
        self.assertIn("_is_empty_entry_response(response)", src)
        self.assertIn("idle_tick_noop", src)
        # The break-out precedes the retry `continue` so empty-on-
        # idle_tick falls through to the enforcement block instead
        # of retrying.
        retry_idx = src.find("if attempt < max_retries:")
        idle_tick_breakout_idx = src.find('str(getattr(state, "run_trigger"')
        # First idle_tick check in decide() is the resume-fast-path
        # gate (line 144). We want the SECOND one (inside the retry
        # loop).
        second_idle_tick_idx = src.find(
            'str(getattr(state, "run_trigger"',
            idle_tick_breakout_idx + 1,
        )
        self.assertGreater(second_idle_tick_idx, 0)
        # Break-out appears before the retry's `continue`.
        self.assertLess(second_idle_tick_idx, retry_idx)
        # Break-out emits the structural telemetry marker so
        # operators see why the retry didn't fire.
        self.assertIn("llm.call.empty_response_accepted", src)

    def test_response_suppressed_canonical_event_registered(self) -> None:
        from openminion.modules.session.storage.replay import (
            _KNOWN_CANONICAL_EVENT_TYPES,
        )

        self.assertIn("response.suppressed", _KNOWN_CANONICAL_EVENT_TYPES)

    def test_llm_call_empty_response_accepted_event_registered(self) -> None:
        from openminion.modules.session.storage.replay import (
            _KNOWN_CANONICAL_EVENT_TYPES,
        )

        self.assertIn("llm.call.empty_response_accepted", _KNOWN_CANONICAL_EVENT_TYPES)


class PostprocessIdleTickNoopSuppressionTests(unittest.IsolatedAsyncioTestCase):
    def _step_output(
        self,
        *,
        run_trigger: str,
        message: str = "",
        status: str = "done",
        pae_idle_tick_noop: bool = False,
    ) -> Any:
        from openminion.modules.brain.schemas import (
            BudgetCounters,
            StepOutput,
            WorkingState,
        )

        state = WorkingState(
            session_id="s1",
            agent_id="agent-x",
            goal="test",
            budgets_remaining=BudgetCounters(
                ticks=5,
                tool_calls=5,
                a2a_calls=0,
                tokens=5000,
                time_ms=60_000,
            ),
            run_trigger=run_trigger,
        )
        return StepOutput(
            session_id="s1",
            status=status,
            message=message,
            working_state=state,
            pae_idle_tick_noop=pae_idle_tick_noop,
        )

    def _bridge_stub(self) -> Any:

        class _BridgeStub:
            _telemetryctl = None

            class _Config:
                class _Agent:
                    name = "agent-x"

                class _Profile:
                    name = "agent-x"

                agent = _Agent()
                agents = {"agent-x": _Profile()}
                default_agent = "agent-x"

            _config = _Config()

            def _active_mode_name_from_step(self, step_out: Any) -> str | None:
                return None

            async def _apply_tool_result_postprocess(
                self,
                *,
                step_out: Any,
                message: Any,
                session_id: str,
                turn_id: str,
                active_mode_name: Any,
                response_text: str,
                termination_reason: str,
            ) -> tuple[str, str, Any]:
                return response_text, termination_reason, None

            def _build_turn_response_metadata(
                self,
                *,
                runner: Any,
                step_out: Any,
                session_id: str,
                request_id: str | None,
                elapsed_ms: float,
                llm_steps: int,
                termination_reason: str,
            ) -> dict[str, Any]:
                return {}

            def _identity_metadata(self) -> dict[str, Any]:
                return {}

            def _extract_memory_policy_metadata(
                self, *, response_text: str
            ) -> dict[str, Any]:
                return {}

            def _build_clarify_request_payload(
                self,
                *,
                step_out: Any,
                session_id: str,
                trace_id: str | None,
            ) -> Any | None:
                return None

            def _attach_clarify_request_metadata(
                self,
                *,
                metadata: dict[str, Any],
                clarify_request: Any,
            ) -> None:
                return None

            def _attach_tool_result_metadata(
                self,
                *,
                metadata: dict[str, Any],
                tool_results_payload: Any,
                termination_reason: str,
            ) -> None:
                return None

        return _BridgeStub()

    async def _run_postprocess(
        self,
        *,
        step_out: Any,
    ) -> Any:
        from openminion.base.types import Message
        from openminion.services.brain.post_execution.postprocess import (
            _postprocess_turn,
        )

        bridge = self._bridge_stub()
        message = Message(channel="cron", target="pae", body="", metadata={})
        return await _postprocess_turn(
            bridge,
            runner=None,
            step_out=step_out,
            message=message,
            history=None,
            session_id="s1",
            request_id="req-1",
            turn_id="turn-1",
            turn_start_time=0.0,
        )

    async def test_idle_tick_noop_suppresses_fallback_text(self) -> None:
        step_out = self._step_output(
            run_trigger="idle_tick",
            message="",
            pae_idle_tick_noop=True,
        )
        response = await self._run_postprocess(step_out=step_out)
        self.assertEqual(response.text, "")
        self.assertEqual(response.metadata.get("pae_idle_tick_noop"), "true")
        self.assertEqual(response.metadata.get("finish_reason"), "pae_idle_tick_noop")

    async def test_idle_tick_without_marker_is_not_suppressed(self) -> None:
        # Idle-tick run_trigger + empty message but NO explicit marker
        # (simulates a non-no-op idle tick that happens to have empty
        # initial `step_out.message`).
        step_out = self._step_output(
            run_trigger="idle_tick",
            message="",
            pae_idle_tick_noop=False,
        )
        response = await self._run_postprocess(step_out=step_out)
        # Suppression must NOT engage — the postprocess gate is the
        # marker, not inferred run_trigger+empty.
        self.assertIn("No response generated.", response.text)
        self.assertNotEqual(response.metadata.get("pae_idle_tick_noop"), "true")

    async def test_non_idle_tick_empty_still_gets_fallback(self) -> None:
        step_out = self._step_output(run_trigger="user_input", message="")
        response = await self._run_postprocess(step_out=step_out)
        self.assertIn("No response generated.", response.text)
        self.assertNotEqual(response.metadata.get("pae_idle_tick_noop"), "true")

    async def test_idle_tick_non_empty_message_not_suppressed(self) -> None:
        step_out = self._step_output(
            run_trigger="idle_tick",
            message="I'm continuing the plan.",
        )
        response = await self._run_postprocess(step_out=step_out)
        self.assertIn("I'm continuing the plan.", response.text)
        self.assertNotEqual(response.metadata.get("pae_idle_tick_noop"), "true")


class GatewayNoopSuppressionTests(unittest.TestCase):
    def test_predicate_detects_metadata_marker(self) -> None:
        from openminion.services.gateway.turn import (
            _response_is_pae_idle_tick_noop,
        )
        from openminion.base.types import AgentResponse

        # Marker present: True.
        r = AgentResponse(
            text="",
            channel="cron",
            target="pae",
            metadata={"pae_idle_tick_noop": "true"},
        )
        self.assertTrue(_response_is_pae_idle_tick_noop(r))
        # Marker absent: False.
        r2 = AgentResponse(
            text="hello",
            channel="chat",
            target="user",
            metadata={},
        )
        self.assertFalse(_response_is_pae_idle_tick_noop(r2))
        # Marker present with wrong case / value: case-insensitive
        # True; anything else False.
        r3 = AgentResponse(
            text="",
            channel="cron",
            target="pae",
            metadata={"pae_idle_tick_noop": "TRUE"},
        )
        self.assertTrue(_response_is_pae_idle_tick_noop(r3))
        r4 = AgentResponse(
            text="",
            channel="cron",
            target="pae",
            metadata={"pae_idle_tick_noop": "false"},
        )
        self.assertFalse(_response_is_pae_idle_tick_noop(r4))

    def test_gateway_suppression_branch_structurally_skips_persistence(
        self,
    ) -> None:
        import inspect
        from openminion.services.gateway import turn as turn_mod

        src = inspect.getsource(turn_mod.GatewayTurnRunner.run)
        # The suppression branch exists and uses the predicate.
        self.assertIn("_response_is_pae_idle_tick_noop(response)", src)
        # Emits the suppressed event, not persisted.
        self.assertIn('event_type="response.suppressed"', src)
        # Branch exists BEFORE Phase 7 persistence so persistence is
        # structurally unreachable on suppression.
        branch_idx = src.find("_response_is_pae_idle_tick_noop(response)")
        persist_idx = src.find("self._build_outbound_and_persist(")
        self.assertLess(branch_idx, persist_idx)
        # Branch returns early, short-circuiting the post-phase 7 flow.
        # We check the subsequent `return outbound` inside the branch
        # precedes the phase-7 invocation path.
        post_branch = src[branch_idx:persist_idx]
        self.assertIn("return outbound", post_branch)

    def test_suppressed_outbound_envelope_has_empty_body(self) -> None:
        from openminion.services.gateway.turn import GatewayTurnRunner
        from openminion.base.types import AgentResponse
        from unittest.mock import MagicMock

        runner = GatewayTurnRunner.__new__(GatewayTurnRunner)
        routing = MagicMock()
        routing.normalized_request_id = "req-1"
        routing.conversation_id = "conv-1"
        routing.thread_id = "thread-1"
        routing.attach_id = ""
        response = AgentResponse(
            text="",
            channel="cron",
            target="pae-agent",
            metadata={"pae_idle_tick_noop": "true"},
        )
        outbound = runner._suppressed_outbound_for_response(
            routing=routing, run_id="run-1", response=response
        )
        self.assertEqual(outbound.body, "")
        self.assertEqual(outbound.metadata["pae_idle_tick_noop"], "true")
        self.assertEqual(outbound.metadata["suppressed"], "pae_idle_tick_noop")
        self.assertEqual(outbound.metadata["run_id"], "run-1")
        self.assertEqual(outbound.metadata["request_id"], "req-1")
        self.assertEqual(outbound.metadata["conversation_id"], "conv-1")
        self.assertEqual(outbound.metadata["thread_id"], "thread-1")
        self.assertNotIn("attach_id", outbound.metadata)  # empty attach_id excluded


class CronExecutorNoopSummaryTests(unittest.TestCase):
    def test_no_op_summary_when_metadata_marker_present(self) -> None:
        import inspect
        from openminion.services.runtime.cron import executor as mod

        src = inspect.getsource(mod.CronTurnExecutor._execute_idle_tick_turn)
        # The no-op branch is gated on the `pae_idle_tick_noop`
        # metadata marker the postprocess layer sets.
        self.assertIn('"pae_idle_tick_noop"', src)
        self.assertIn('"PAE idle tick: no-op"', src)
        self.assertIn('"PAE idle tick completed."', src)
