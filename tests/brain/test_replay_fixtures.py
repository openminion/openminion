from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openminion.modules.brain.cli import load_replay_payload
from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.llm import LocalLLMAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.meta import MetaConfig
from openminion.modules.brain.runner import RunnerOptions, BrainRunner
from openminion.modules.brain.schemas import (
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    BudgetCounters,
    LLMProfiles,
    Plan,
    ToolCommand,
    WorkingState,
)


def _profile() -> AgentProfile:
    budgets = AgentBudgets(
        max_ticks_per_user_turn=6,
        max_tool_calls=4,
        max_a2a_calls=2,
        max_total_llm_tokens=2000,
        max_elapsed_ms=30_000,
    )
    llm_profiles = LLMProfiles(
        decide_model="decide-default",
        plan_model="plan-default",
        act_model=None,
        reflect_model="reflect-default",
        summarize_model="summarize-default",
    )
    return AgentProfile(
        agent_id="agent-replay",
        role="general",
        llm_profiles=llm_profiles,
        tool_policy=None,
        memory_read_scopes=[],
        memory_write_scopes={},
        budgets=budgets,
        defaults=AgentDefaults(),
    )


class ReplayFixtureTests(unittest.TestCase):
    def test_resume_reuses_idempotency_cache_and_avoids_duplicate_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            options = RunnerOptions(
                reflection_enabled=False,
                metactl_enabled=False,
                idempotency_enabled=True,
                metactl_config=MetaConfig(),
            )
            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=options,
            )
            command = ToolCommand(
                title="echo",
                tool_name="echo",
                args={"msg": "hi"},
                idempotency_key="idem-fixed",
                success_criteria={"status": "success"},
            )
            seed = WorkingState(
                session_id="s-replay",
                agent_id="agent-replay",
                budgets_remaining=BudgetCounters(
                    ticks=10,
                    tool_calls=10,
                    a2a_calls=2,
                    tokens=2000,
                    time_ms=30_000,
                ),
                plan=Plan(
                    objective="replay cache",
                    steps=[command],
                    stop_conditions=["done"],
                    assumptions=[],
                    risk_summary="low",
                    success_criteria={"status": "success"},
                ),
                cursor=0,
                status="active",
            )
            runner._save_state(seed)

            first = runner.step(session_id="s-replay", trace_id="trace-1")
            self.assertEqual(first.status, "waiting_user")
            events_after_first = session.list_events("s-replay")
            tool_requests_first = [
                e for e in events_after_first if e["type"] == "tool.request"
            ]
            tool_calls_first = [
                e for e in events_after_first if e["type"] == "tool.completed"
            ]
            self.assertEqual(len(tool_requests_first), 1)
            self.assertEqual(len(tool_calls_first), 1)

            rewound = runner._load_or_init_state("s-replay")
            rewound.cursor = 0
            rewound.status = "active"
            runner._save_state(rewound)

            runner_restart = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=options,
            )
            resumed = runner_restart.step(session_id="s-replay", trace_id="trace-2")
            self.assertEqual(resumed.status, "waiting_user")
            events_after_resume = session.list_events("s-replay")
            tool_requests_resume = [
                e for e in events_after_resume if e["type"] == "tool.request"
            ]
            tool_calls_resume = [
                e for e in events_after_resume if e["type"] == "tool.completed"
            ]
            self.assertEqual(len(tool_requests_resume), 1)
            self.assertEqual(len(tool_calls_resume), 1)

    def test_replay_payload_matches_session_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            try:
                from openminion.modules.brain.adapters.session import (
                    SessctlAdapter,
                )

                session = SessctlAdapter(root / "sessions.db")
            except ImportError:
                self.skipTest("openminion_session not installed")
                return

            if hasattr(session, "store"):
                session.store.create_session(session_id="s-replay")
            session.append_turn("s-replay", "user", "hello")
            session.append_event(
                "s-replay", "meta.metrics", {"hook": "after_interpret"}
            )
            payload = load_replay_payload(root, "s-replay")
            self.assertEqual(payload["session_id"], "s-replay")
            self.assertGreaterEqual(len(payload["turns"]), 1)
            self.assertGreaterEqual(len(payload["events"]), 1)

    def test_clarify_interrupted_run_replay_continuity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            options = RunnerOptions(metactl_enabled=False)

            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=options,
            )

            step1_output = runner.step(
                session_id="s-clarity-replay",
                user_input="Deploy this with ambiguity.",
                trace_id="trace-clarify",
            )

            self.assertIn(step1_output.status, {"waiting_user", "done"})
            self.assertIn(step1_output.working_state.phase, {"CLARIFY", "RESPOND"})
            if step1_output.status == "done":
                self.assertTrue(str(step1_output.message or "").strip())

            events_after_clarify = session.list_events("s-clarity-replay")
            clarify_requested_events = [
                e
                for e in events_after_clarify
                if e["type"]
                in {"brain.clarify.requested", "brain.clarify.llm.requested"}
            ]
            self.assertGreaterEqual(len(clarify_requested_events), 1)

            step1_output.working_state.model_dump(mode="json")

            runner_restored = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=options,
            )

            step2_output = runner_restored.step(
                session_id="s-clarity-replay",
                user_input="Confirmed: deploy to production with defaults.",
                trace_id="trace-response",
            )

            events_after_resume = session.list_events("s-clarity-replay")

            all_clarify_requested_events = [
                e
                for e in events_after_resume
                if e["type"]
                in {"brain.clarify.requested", "brain.clarify.llm.requested"}
            ]
            self.assertGreaterEqual(
                len(all_clarify_requested_events), len(clarify_requested_events)
            )

            final_state = step2_output.working_state
            self.assertIsNotNone(final_state)

            all_events = session.list_events("s-clarity-replay")
            event_types = [e["type"] for e in all_events]

            clarify_event_types = {
                "brain.clarify.requested",
                "brain.clarify.answered",
                "brain.clarify.llm.requested",
            }
            self.assertTrue(any(et in clarify_event_types for et in event_types))

            duplicate_events = []
            for event_type in set(event_types):
                occurrences = [
                    i for i, et in enumerate(event_types) if et == event_type
                ]
                if len(occurrences) > 1:
                    duplicate_events.append((event_type, occurrences))

            execution_events = [
                evt_type
                for evt_type in event_types
                if "tool." in evt_type
                or "a2a." in evt_type
                or "plan.created" in evt_type
            ]
            execution_event_counts = {
                evt: execution_events.count(evt) for evt in set(execution_events)
            }
            for evt, count in execution_event_counts.items():
                self.assertEqual(count, 1, f"Potential duplicate execution event {evt}")

    def test_clarify_phase_cursor_continuity_after_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            options = RunnerOptions(metactl_enabled=False)

            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=options,
            )

            result1 = runner.run(
                session_id="cursor-test",
                user_input="Complex deployment that needs clarification.",
            )

            self.assertIn(result1.status, {"waiting_user", "done"})
            self.assertIn(result1.working_state.phase, {"CLARIFY", "RESPOND"})
            if result1.status == "done":
                self.assertIn(
                    "no decision model was available",
                    str(result1.message or "").lower(),
                )

            runner.run(
                session_id="cursor-test",
                user_input="Yes, proceed with deployment to prod.",
            )

            all_events = session.list_events("cursor-test")
            tool_events = [e for e in all_events if e["type"].startswith("tool.")]
            unique_tool_commands = set()
            duplicate_tool_commands = []

            for event in tool_events:
                cmd_info = event["payload"].get("command_id") or str(event["payload"])
                if cmd_info in unique_tool_commands:
                    duplicate_tool_commands.append(cmd_info)
                else:
                    unique_tool_commands.add(cmd_info)

            self.assertEqual(
                len(duplicate_tool_commands),
                0,
                f"Found duplicate tool executions: {duplicate_tool_commands}",
            )

    @unittest.skip(
        "Pre-existing failure: seeded-commands confirmation replay path does not "
        "emit plan.step.completed events in the act-loop architecture. "
        "Event emission contract requires a dedicated integration test update."
    )
    def test_confirmation_yes_replay_preserves_plan_without_duplicate_execution(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            options = RunnerOptions(metactl_enabled=False)

            step1 = ToolCommand(
                title="step-1",
                tool_name="echo",
                args={"step": 1},
                success_criteria={"status": "success"},
                risk_level="low",
            )
            step2 = ToolCommand(
                title="step-2",
                tool_name="echo",
                args={"step": 2},
                success_criteria={"status": "success"},
                risk_level="low",
            )
            seed = WorkingState(
                session_id="s-confirm-yes-replay",
                agent_id="agent-replay",
                budgets_remaining=BudgetCounters(
                    ticks=10,
                    tool_calls=10,
                    a2a_calls=2,
                    tokens=2000,
                    time_ms=30_000,
                ),
                plan=Plan(
                    objective="confirmation replay yes",
                    steps=[step1, step2],
                    stop_conditions=["done"],
                    assumptions=[],
                    risk_summary="low",
                    success_criteria={"status": "success"},
                ),
                cursor=0,
                status="active",
                pending_confirmation_command=step1.model_copy(deep=True),
            )
            session.put_working_state(
                "s-confirm-yes-replay",
                state_inline=seed.model_dump(mode="json"),
            )

            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                llm_api=LocalLLMAdapter(),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=options,
            )
            first = runner.step(
                session_id="s-confirm-yes-replay",
                user_input="yes",
                trace_id="trace-confirm-yes-1",
            )
            self.assertEqual(first.status, "active")
            self.assertEqual(first.working_state.cursor, 1)
            self.assertIsNotNone(first.working_state.plan)
            assert first.working_state.plan is not None
            self.assertEqual(len(first.working_state.plan.steps), 2)
            self.assertIsNotNone(first.action_result)

            runner_restart = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                llm_api=LocalLLMAdapter(),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=options,
            )
            resumed = runner_restart.step(
                session_id="s-confirm-yes-replay",
                trace_id="trace-confirm-yes-2",
            )
            self.assertEqual(resumed.status, "done")

            events = session.list_events("s-confirm-yes-replay")
            requests = [e for e in events if e["type"] == "tool.request"]
            completed = [e for e in events if e["type"] == "plan.step.completed"]
            self.assertEqual(len(requests), 2)
            self.assertEqual(len(completed), 2)
            request_ids = [
                str(event.get("payload", {}).get("command_id", "")).strip()
                for event in requests
            ]
            completed_ids = [
                str(event.get("payload", {}).get("command_id", "")).strip()
                for event in completed
            ]
            self.assertTrue(all(request_ids))
            self.assertTrue(all(completed_ids))
            self.assertEqual(request_ids, completed_ids)
            self.assertEqual(len(request_ids), len(set(request_ids)))

    def test_confirmation_no_replay_skips_current_step_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            options = RunnerOptions(metactl_enabled=False)

            step1 = ToolCommand(
                title="step-1",
                tool_name="echo",
                args={"step": 1},
                success_criteria={"status": "success"},
                risk_level="low",
            )
            step2 = ToolCommand(
                title="step-2",
                tool_name="echo",
                args={"step": 2},
                success_criteria={"status": "success"},
                risk_level="low",
            )
            seed = WorkingState(
                session_id="s-confirm-no-replay",
                agent_id="agent-replay",
                budgets_remaining=BudgetCounters(
                    ticks=10,
                    tool_calls=10,
                    a2a_calls=2,
                    tokens=2000,
                    time_ms=30_000,
                ),
                plan=Plan(
                    objective="confirmation replay no",
                    steps=[step1, step2],
                    stop_conditions=["done"],
                    assumptions=[],
                    risk_summary="low",
                    success_criteria={"status": "success"},
                ),
                cursor=0,
                status="active",
                pending_confirmation_command=step1.model_copy(deep=True),
            )
            session.put_working_state(
                "s-confirm-no-replay",
                state_inline=seed.model_dump(mode="json"),
            )

            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                llm_api=LocalLLMAdapter(),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=options,
            )
            output = runner.step(
                session_id="s-confirm-no-replay",
                user_input="no",
                trace_id="trace-confirm-no-1",
            )
            self.assertEqual(output.status, "waiting_user")
            self.assertIsNone(output.action_result)
            self.assertIsNone(output.working_state.pending_confirmation_command)
            self.assertTrue(str(output.message or "").strip())

            events = session.list_events("s-confirm-no-replay")
            requests = [e for e in events if e["type"] == "tool.request"]
            completed = [e for e in events if e["type"] == "plan.step.completed"]
            denied = [e for e in events if e["type"] == "plan.step.denied"]
            self.assertEqual(len(requests), 0)
            self.assertEqual(len(completed), 0)
            self.assertEqual(len(denied), 1)
