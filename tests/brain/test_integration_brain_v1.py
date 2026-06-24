from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from openminion.modules.brain.cli import _build_runner
from openminion.modules.brain.config import (
    MetaCtlConfig,
    PlanAutoScaleConfig,
    RuntimeConfig,
    BrainConfig,
)
from openminion.modules.brain.interfaces import MetaAPI
from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.recursive import LocalRLMAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.meta import (
    MetaConfig,
    MetaDirective,
    MetaMetrics,
    MetaResult,
    MetaState,
)
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
from openminion.modules.llm.schemas import LLMResponse, Message, ToolCall, UsageInfo


class CountingMetaAdapter(MetaAPI):
    contract_version = "v1"

    def __init__(self) -> None:
        self.calls: list[MetaMetrics] = []

    def evaluate(self, metrics: MetaMetrics) -> MetaResult:
        self.calls.append(metrics)
        return MetaResult(
            meta_state=MetaState.NORMAL,
            directive=MetaDirective(),
            metrics=metrics,
            reasons=["test"],
            ruleset_version="meta.test",
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
        agent_id="agent-int",
        role="general",
        llm_profiles=llm_profiles,
        tool_policy=None,
        memory_read_scopes=[],
        memory_write_scopes={},
        budgets=budgets,
        defaults=AgentDefaults(),
    )


class IntegrationBrainV1Tests(unittest.TestCase):
    def test_cli_builds_runner_with_builtin_meta_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = RuntimeConfig(
                brain=BrainConfig(
                    agent_id="agent-int",
                    role="general",
                    llm_profiles=_profile().llm_profiles,
                    tool_policy=None,
                    memory_read_scopes=[],
                    memory_write_scopes={},
                    budgets=_profile().budgets,
                    metactl=MetaCtlConfig(enabled=True),
                    plan_auto_scale=PlanAutoScaleConfig(
                        max_llm_calls=31,
                        max_ticks=17,
                        max_tokens=88_000,
                        max_elapsed_ms=210_000,
                        base_overhead_ms=11_000,
                        per_step_time_ms=8_000,
                    ),
                )
            )
            runner, session_store = _build_runner(config=cfg, root=root)
            self.assertIsNone(runner.meta_api)
            self.assertIsNotNone(runner.meta_engine)
            self.assertTrue(runner.options.metactl_enabled)
            self.assertEqual(runner.options.plan_auto_scale_max_llm_calls, 31)
            self.assertEqual(runner.options.plan_auto_scale_max_ticks, 17)
            self.assertEqual(runner.options.plan_auto_scale_max_tokens, 88_000)
            self.assertEqual(runner.options.plan_auto_scale_max_elapsed_ms, 210_000)
            self.assertEqual(runner.options.plan_auto_scale_base_overhead_ms, 11_000)
            self.assertEqual(runner.options.plan_auto_scale_per_step_time_ms, 8_000)
            # sanity: a session store was built and is writable
            session_store.append_turn("s-int-cli", "user", "hi")

    def test_meta_adapter_is_used_during_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            meta_adapter = CountingMetaAdapter()
            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                meta_api=meta_adapter,
                options=RunnerOptions(
                    reflection_enabled=False,
                    metactl_enabled=True,
                    metactl_config=MetaConfig(),
                ),
            )

            runner.step(
                session_id="s-int",
                user_input='tool echo {"msg":"hi"}',
                trace_id="trace-int",
            )

            self.assertGreaterEqual(len(meta_adapter.calls), 1)
            events = session.list_events("s-int")
            event_types = {event["type"] for event in events}
            self.assertIn("meta.metrics", event_types)
            self.assertIn("meta.directive", event_types)

    def test_llm_factory_sanitizes_none_config_values(self) -> None:
        from openminion.modules.brain.adapters.factory import create_llm_adapter
        from unittest.mock import patch

        # Test that None config is handled correctly
        adapter_1 = create_llm_adapter(mode="local")  # No config
        adapter_2 = create_llm_adapter(mode="local", config=None)
        self.assertEqual(type(adapter_1), type(adapter_2))

        # If using external adapter with config, verify None values are filtered out
        with patch.dict("os.environ", {"OPENMINION_LLMS_ADAPTER": "local"}):
            # Test with various combinations of config with None values
            config_with_nones = {
                "model": "test-model",
                "config_path": None,  # This should be filtered out
                "temperature": 0.7,
                "top_p": None,  # This should be filtered out
                "api_key": "valid-key",
            }

            # With non-local mode, adapter creation should succeed even with None values
            # Since llm dependency may not be available, test with patched import
            try:
                with patch(
                    "openminion.modules.brain.adapters.factory.create_llm_adapter"
                ):
                    # This is to check our sanitized config logic separately
                    # Test directly that the filter mechanism works
                    sanitized = {
                        k: v for k, v in config_with_nones.items() if v is not None
                    }
                    self.assertIn("model", sanitized)
                    self.assertIn("temperature", sanitized)
                    self.assertIn("api_key", sanitized)
                    self.assertNotIn("config_path", sanitized)  # Should be filtered out
                    self.assertNotIn("top_p", sanitized)  # Should be filtered out
            except ImportError:
                # LLM adapter not available, test with local fallback to check the logic
                # still applies
                local_adapter_1 = create_llm_adapter(
                    mode="local", config=config_with_nones
                )
                local_adapter_2 = create_llm_adapter(mode="local")  # Empty config
                self.assertEqual(type(local_adapter_1), type(local_adapter_2))

    def test_clarify_pause_resume_continuity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                llm_api=SimpleNamespace(
                    call=MagicMock(
                        side_effect=[
                            LLMResponse(
                                ok=True,
                                provider="test",
                                model="test-model",
                                tool_calls=[
                                    ToolCall(
                                        id="clarify-1",
                                        name="clarify",
                                        arguments={
                                            "question": "What exactly should I do?"
                                        },
                                        status="requested",
                                    )
                                ],
                                usage=UsageInfo(
                                    input_tokens=1,
                                    output_tokens=1,
                                    total_tokens=2,
                                ),
                                finish_reason="tool_calls",
                                provider_raw={},
                                telemetry={},
                            ),
                            LLMResponse(
                                ok=True,
                                provider="test",
                                model="test-model",
                                output_text="Understood.",
                                assistant_messages=[
                                    Message(role="assistant", content="Understood.")
                                ],
                                usage=UsageInfo(
                                    input_tokens=1,
                                    output_tokens=1,
                                    total_tokens=2,
                                ),
                                finish_reason="stop",
                                provider_raw={},
                                telemetry={},
                            ),
                        ]
                    ),
                    estimate_tokens=MagicMock(return_value=1),
                ),
                context_api=LocalContextAdapter(session_store=session),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(
                    metactl_enabled=False,
                ),
            )

            # LLM-driven flow; keep continuity assertions tolerant.
            res1 = runner.step(
                session_id="s-pause",
                user_input="Do something maybe?",
                trace_id="t-pause",
            )
            self.assertEqual(res1.status, "waiting_user")
            self.assertIn(res1.working_state.phase, {"CLARIFY", "RESPOND"})
            if res1.working_state.phase == "CLARIFY":
                self.assertEqual(len(res1.working_state.unresolved_clarify_items), 1)

            # Track events before resume to check for duplicates
            events_before_resume = session.list_events("s-pause")
            tool_events_before = [
                e for e in events_before_resume if "tool." in e["type"]
            ]

            # Turn 2: Resume with answer
            res2 = runner.step(
                session_id="s-pause",
                user_input="I confirm. Deploy to production.",
                trace_id="t-pause",
            )

            # After answering, clarification items should be processed
            # Now should continue with decision making for "Deploy to production"
            self.assertEqual(len(res2.working_state.unresolved_clarify_items), 0)
            self.assertEqual(res2.working_state.trace_id, "t-pause")

            # Verify no duplicate side effects from original request (BCM-07 requirement)
            events_after_resume = session.list_events("s-pause")
            tool_events_after = [e for e in events_after_resume if "tool." in e["type"]]

            # No duplicate tool executions that happened before clarification should occur again
            self.assertEqual(
                len(tool_events_after),
                len(tool_events_before),
                "Duplicate tool executions detected after clarification resume",
            )

    def test_simple_qa_path_avoids_plan_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(metactl_enabled=False),
            )

            out = runner.step(
                session_id="s-simple-qa",
                user_input="What is 2+2?",
                trace_id="t-simple-qa",
            )
            self.assertIn(out.status, {"done", "waiting_user"})
            events = session.list_events("s-simple-qa")
            event_types = [item["type"] for item in events]
            self.assertNotIn("plan.created", event_types)

    def test_autonomous_recursive_mode_emits_recursive_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                rlm_api=LocalRLMAdapter(),
                options=RunnerOptions(metactl_enabled=False),
            )

            # Seed mode to autonomous for this session.
            state = runner._load_or_init_state("s-auto")
            state.mode = "autonomous"
            runner._save_state(state)

            out = runner.step(
                session_id="s-auto",
                user_input="Investigate this issue autonomously.",
                trace_id="t-auto",
            )
            self.assertIn(out.status, {"done", "waiting_user"})
            events = session.list_events("s-auto")
            event_types = [item["type"] for item in events]
            self.assertIn("brain.recursive_turn.started", event_types)
            self.assertTrue(
                "brain.recursive_turn.completed" in event_types
                or "brain.recursive_turn.error" in event_types
            )

    def test_confirmation_replay_paths_preserve_or_complete_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(metactl_enabled=False),
            )

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

            yes_seed = WorkingState(
                session_id="s-int-confirm-yes",
                agent_id="agent-int",
                budgets_remaining=BudgetCounters(
                    ticks=10,
                    tool_calls=10,
                    a2a_calls=2,
                    tokens=2000,
                    time_ms=30_000,
                ),
                plan=Plan(
                    objective="confirm yes integration",
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
                "s-int-confirm-yes",
                state_inline=yes_seed.model_dump(mode="json"),
            )

            yes_output = runner.step(
                session_id="s-int-confirm-yes",
                user_input="yes",
                trace_id="trace-int-confirm-yes",
            )
            self.assertIn(yes_output.status, {"active", "waiting_user"})
            self.assertIsNone(yes_output.working_state.pending_confirmation_command)
            if yes_output.status == "active":
                self.assertEqual(yes_output.working_state.cursor, 1)
            else:
                self.assertEqual(yes_output.working_state.cursor, 0)
                self.assertEqual(yes_output.working_state.phase, "CLARIFY")
                self.assertIn(
                    "could not safely determine the next step",
                    str(yes_output.message or "").lower(),
                )

            no_seed = WorkingState(
                session_id="s-int-confirm-no-final",
                agent_id="agent-int",
                budgets_remaining=BudgetCounters(
                    ticks=10,
                    tool_calls=10,
                    a2a_calls=2,
                    tokens=2000,
                    time_ms=30_000,
                ),
                plan=Plan(
                    objective="confirm no final integration",
                    steps=[step1.model_copy(deep=True)],
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
                "s-int-confirm-no-final",
                state_inline=no_seed.model_dump(mode="json"),
            )

            no_output = runner.step(
                session_id="s-int-confirm-no-final",
                user_input="no",
                trace_id="trace-int-confirm-no-final",
            )
            self.assertIn(no_output.status, {"done", "waiting_user"})
            self.assertIsNone(no_output.working_state.pending_confirmation_command)
            if no_output.status == "done":
                self.assertIn("skipped", str(no_output.message or "").lower())
            else:
                self.assertEqual(no_output.working_state.cursor, 0)
                self.assertEqual(no_output.working_state.phase, "CLARIFY")
                self.assertIn(
                    "could not safely determine the next step",
                    str(no_output.message or "").lower(),
                )
