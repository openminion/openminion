from openminion.modules.brain.adapters.llm import LocalLLMAdapter

from tests.brain.runner_test_support import (
    BudgetAdjust,
    BudgetCounters,
    LocalA2AAdapter,
    LocalContextAdapter,
    LocalMemoryAdapter,
    LocalPolicyAdapter,
    LocalSessionStore,
    LocalToolAdapter,
    MagicMock,
    MetaDirective,
    MetaMetrics,
    MetaResult,
    MetaState,
    Path,
    Plan,
    RunnerOptions,
    BrainRunner,
    ToolCommand,
    VerificationMode,
    WorkingState,
    _profile,
    tempfile,
    unittest,
)


class RunnerMetaTests(unittest.TestCase):
    def test_meta_events_are_emitted(self) -> None:
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
                options=RunnerOptions(reflection_enabled=False, metactl_enabled=True),
            )

            runner.step(
                session_id="s4", user_input='tool echo {"x":1}', trace_id="trace-meta"
            )
            events = session.list_events("s4")
            event_types = {event["type"] for event in events}
            self.assertIn("meta.metrics", event_types)
            self.assertIn("meta.directive", event_types)

    def test_kill_switch_stops_execution(self) -> None:
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
                options=RunnerOptions(reflection_enabled=False, metactl_enabled=True),
            )

            output = runner.step(
                session_id="s5",
                user_input="Please stop all actions now.",
                trace_id="trace-kill",
            )
            self.assertEqual(output.status, "stopped")
            events = session.list_events("s5")
            event_types = {event["type"] for event in events}
            self.assertIn("meta.directive", event_types)
            self.assertNotIn("tool.call", event_types)

    def test_meta_override_forces_waiting_before_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                llm_api=LocalLLMAdapter(),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(reflection_enabled=False, metactl_enabled=False),
            )

            directive = MetaDirective(
                override_next_state="WAITING",
                escalation_question="Need more info before planning.",
            )
            runner.set_meta_override(
                "before_plan",
                MetaResult(
                    meta_state=MetaState.CAUTIOUS,
                    directive=directive,
                    metrics=MetaMetrics(),
                    reasons=["manual"],
                ),
            )

            output = runner.step(
                session_id="s6",
                user_input='tool echo {"msg":"hi"}',
                trace_id="trace-meta-plan",
            )
            self.assertEqual(output.status, "waiting_user")
            self.assertIn("Need more info", (output.message or ""))
            events = session.list_events("s6")
            event_types = {event["type"] for event in events}
            self.assertIn("meta.metrics", event_types)
            self.assertIn("meta.directive", event_types)

    def test_meta_override_require_clarification_blocks_before_plan(self) -> None:
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
                options=RunnerOptions(reflection_enabled=False, metactl_enabled=False),
            )

            directive = MetaDirective(
                require_clarification=True,
                clarification_question="Which environment should I target?",
            )
            runner.set_meta_override(
                "before_plan",
                MetaResult(
                    meta_state=MetaState.CAUTIOUS,
                    directive=directive,
                    metrics=MetaMetrics(),
                    reasons=["manual"],
                ),
            )

            output = runner.step(
                session_id="s6-clarify",
                user_input='tool echo {"msg":"hi"}',
                trace_id="trace-meta-plan-clarify",
            )
            self.assertEqual(output.status, "waiting_user")
            self.assertIn("Which environment", (output.message or ""))

    def test_meta_override_tracks_application_details(self) -> None:
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
                options=RunnerOptions(reflection_enabled=False, metactl_enabled=False),
            )

            directive = MetaDirective(
                tier_override="T3_high_assurance",
                prompt_constraints=["state facts only"],
                budget_adjustments=BudgetAdjust(
                    lower_context_limits=True, lower_llm_calls_max=2
                ),
            )
            runner.set_meta_override(
                "before_plan",
                MetaResult(
                    meta_state=MetaState.HIGH_ASSURANCE,
                    directive=directive,
                    metrics=MetaMetrics(),
                    reasons=["manual"],
                ),
            )

            output = runner.step(
                session_id="s7",
                user_input='tool echo {"msg":"hi"}',
                trace_id="trace-meta-act",
            )
            self.assertEqual(output.status, "done")
            application = runner.get_last_meta_application()
            self.assertIsNotNone(application)
            assert application is not None
            self.assertEqual(application.tier_after, "T3_high_assurance")
            self.assertIn("state facts only", application.constraints_added)
            self.assertTrue(application.budgets_adjusted)
            self.assertLessEqual(output.working_state.llm_calls_max, 2)

    def test_meta_override_confirmation_still_pauses_turn(self) -> None:
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
                options=RunnerOptions(reflection_enabled=False, metactl_enabled=False),
            )

            directive = MetaDirective(require_confirmation=True)
            runner.set_meta_override(
                "before_act",
                MetaResult(
                    meta_state=MetaState.CAUTIOUS,
                    directive=directive,
                    metrics=MetaMetrics(),
                    reasons=["manual"],
                ),
            )

            command = ToolCommand(
                title="call tool",
                tool_name="echo",
                args={"msg": "x"},
                success_criteria={"status": "success"},
                idempotency_key="idem-conf-1",
            )
            state = WorkingState(
                session_id="s8",
                agent_id="router-agent",
                budgets_remaining=BudgetCounters(
                    ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
                ),
                plan=Plan(
                    objective="do it",
                    steps=[command],
                    stop_conditions=[],
                    assumptions=[],
                    risk_summary="",
                    success_criteria={},
                ),
                cursor=0,
                status="active",
                trace_id="trace-conf",
            )
            session.put_working_state("s8", state_inline=state.model_dump(mode="json"))

            output = runner.step(session_id="s8")
            self.assertEqual(output.status, "waiting_user")
            self.assertIn(
                "could not safely determine the next step",
                (output.message or "").lower(),
            )

    def test_meta_override_verification_request_keeps_direct_tool_path_stable(
        self,
    ) -> None:
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
                options=RunnerOptions(reflection_enabled=False, metactl_enabled=False),
            )

            directive = MetaDirective(require_verification=True)
            meta_res = MetaResult(
                meta_state=MetaState.HIGH_ASSURANCE,
                directive=directive,
                metrics=MetaMetrics(),
                reasons=["manual"],
            )
            runner.set_meta_override("before_act", meta_res)

            command = ToolCommand(
                title="call tool",
                tool_name="echo",
                args={"msg": "x"},
                success_criteria={"status": "success"},
                idempotency_key="idem-ver-1",
            )
            state = WorkingState(
                session_id="s9",
                agent_id="router-agent",
                budgets_remaining=BudgetCounters(
                    ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
                ),
                plan=Plan(
                    objective="do it",
                    steps=[command],
                    stop_conditions=[],
                    assumptions=[],
                    risk_summary="",
                    success_criteria={},
                ),
                cursor=0,
                status="active",
                trace_id="trace-ver",
            )
            session.put_working_state("s9", state_inline=state.model_dump(mode="json"))

            runner.step(session_id="s9")
            events = session.list_events("s9")
            event_types = {e["type"] for e in events}
            self.assertIn("tool.request", event_types)
            self.assertIn("tool.completed", event_types)
            self.assertIn("turn.outcome", event_types)
            self.assertNotIn("verify.completed", event_types)

    def test_meta_events_checkpoint_ordering_and_persistence(self) -> None:
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
                options=RunnerOptions(reflection_enabled=False, metactl_enabled=True),
            )

            runner.step(
                session_id="s10",
                user_input='tool echo {"msg":"hi"}',
                trace_id="trace-ordering",
            )

            # Assert meta_logs persistence on the state
            state_data = session.get_latest_working_state("s10")
            self.assertIsNotNone(state_data)
            assert state_data is not None
            state = WorkingState.model_validate(state_data["state_inline"])

            hooks = [log.hook for log in state.meta_logs]
            # Current lifecycle persists the entry/meta checkpoints around the
            # direct tool path without separate before_act/after_observe hooks.
            for expected_hook in [
                "after_interpret",
                "before_plan",
                "before_respond",
            ]:
                self.assertIn(expected_hook, hooks)

            # Assert event ordering
            events = session.list_events("s10")
            meta_hooks_ordered = []
            for e in events:
                if e["type"] == "meta.directive":
                    meta_hooks_ordered.append(e["payload"]["hook"])

            # Current direct tool execution path emits the persisted
            # after_interpret -> before_plan -> before_respond sequence.
            self.assertTrue(len(meta_hooks_ordered) >= 3)
            self.assertEqual(meta_hooks_ordered[0], "after_interpret")
            self.assertEqual(meta_hooks_ordered[1], "before_plan")
            self.assertEqual(meta_hooks_ordered[-1], "before_respond")

    def test_tier_0_blocks_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                llm_api=LocalLLMAdapter(),
                context_api=LocalContextAdapter(session_store=session),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(reflection_enabled=False, metactl_enabled=False),
            )

            state = WorkingState(
                session_id="s11",
                agent_id="router-agent",
                budgets_remaining=BudgetCounters(
                    ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
                ),
                tier="T0_direct",
                status="active",
                trace_id="trace-t0",
            )
            session.put_working_state("s11", state_inline=state.model_dump(mode="json"))

            output = runner.step(
                session_id="s11",
                user_input='tool echo {"msg":"hi"}',
                trace_id="trace-t0",
            )
            self.assertEqual(output.status, "done")
            self.assertIn(
                "restricted to direct responses", (output.message or "").lower()
            )

            events = session.list_events("s11")
            event_types = {e["type"] for e in events}
            self.assertIn("tier.blocked", event_types)

    def test_tier_3_direct_tool_path_completes_without_reflection_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                llm_api=LocalLLMAdapter(),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(reflection_enabled=False, metactl_enabled=False),
            )

            command = ToolCommand(
                title="call tool",
                tool_name="echo",
                args={"msg": "success"},
                success_criteria={"status": "success"},
                idempotency_key="idem-t3",
            )
            state = WorkingState(
                session_id="s12",
                agent_id="router-agent",
                budgets_remaining=BudgetCounters(
                    ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
                ),
                tier="T3_high_assurance",
                plan=Plan(
                    objective="do it",
                    steps=[command],
                    stop_conditions=[],
                    assumptions=[],
                    risk_summary="",
                    success_criteria={},
                ),
                cursor=0,
                status="active",
                trace_id="trace-t3",
            )
            session.put_working_state("s12", state_inline=state.model_dump(mode="json"))

            output = runner.step(session_id="s12")
            self.assertEqual(output.status, "done")

            events = session.list_events("s12")
            event_types = {e["type"] for e in events}
            self.assertIn("tool.completed", event_types)
            self.assertIn("turn.outcome", event_types)
            self.assertNotIn("reflect.completed", event_types)

    def test_verification_override_direct_tool_path_fail_closes_without_verify_phase(
        self,
    ) -> None:
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
                options=RunnerOptions(reflection_enabled=False, metactl_enabled=False),
            )

            directive = MetaDirective(
                require_verification=True, verification_mode=VerificationMode.rule_based
            )
            meta_res = MetaResult(
                meta_state=MetaState.HIGH_ASSURANCE,
                directive=directive,
                metrics=MetaMetrics(),
                reasons=["manual"],
            )
            # Both before_act and after_observe set require_verification
            runner.set_meta_override("before_act", meta_res)
            runner.set_meta_override("after_observe", meta_res)

            command = ToolCommand(
                title="call tool",
                tool_name="echo",
                args={"msg": "actual_output"},
                success_criteria={"msg": "expected_output"},
                idempotency_key="idem-ver-fail",
            )
            state = WorkingState(
                session_id="s13",
                agent_id="router-agent",
                budgets_remaining=BudgetCounters(
                    ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
                ),
                tier="T3_high_assurance",
                plan=Plan(
                    objective="do it",
                    steps=[command],
                    stop_conditions=[],
                    assumptions=[],
                    risk_summary="",
                    success_criteria={},
                ),
                cursor=0,
                status="active",
                trace_id="trace-ver-fail",
            )
            session.put_working_state("s13", state_inline=state.model_dump(mode="json"))

            runner.step(session_id="s13")

            # Since verify failed, replan is forced. The state plan cursor steps should show replanning.
            state_data = session.get_latest_working_state("s13")
            self.assertIsNotNone(state_data)
            assert state_data is not None
            new_state = WorkingState.model_validate(state_data["state_inline"])

            events = session.list_events("s13")
            event_types = {e["type"] for e in events}
            self.assertIn("tool.request", event_types)
            self.assertIn("tool.completed", event_types)
            self.assertIn("turn.outcome", event_types)
            self.assertNotIn("verify.completed", event_types)
            self.assertEqual(new_state.status, "waiting_user")
            self.assertEqual(new_state.replans_used, 0)

    def test_skill_events_emitted_when_skill_api_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")

            class _RunnerLLM:
                contract_version = "v1"

                def estimate_tokens(self, *, model, context):
                    del model, context
                    return 12

                def call_structured(self, **kwargs):
                    schema_name = kwargs["schema"].__name__
                    if schema_name == "Decision":
                        return {
                            "route": "respond",
                            "confidence": 0.9,
                            "reason_code": "mock_decide",
                            "answer": "ok",
                        }
                    raise AssertionError(f"Unexpected schema: {schema_name}")

            skill_api = MagicMock()
            skill_api.catalog_summaries.return_value = [
                {
                    "id": "docker_restart_safe",
                    "name": "Restart Docker Services Safely",
                    "one_liner": "Safely restart docker and verify daemon health.",
                    "version_hash": "a" * 64,
                }
            ]
            skill_api.render_snippet.return_value = (
                "Use the docker restart workflow.",
                "a" * 64,
            )

            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                llm_api=_RunnerLLM(),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                skill_api=skill_api,
                options=RunnerOptions(reflection_enabled=False, metactl_enabled=False),
            )

            runner.step(
                session_id="s-skill",
                user_input='tool echo {"msg":"test"}',
                trace_id="trace-skill",
            )

            events = session.list_events("s-skill")
            event_types = [e["type"] for e in events]

            self.assertIn("skill.shortlisted", event_types)
            self.assertIn("skill.prerouting", event_types)
            self.assertIn("skill.selected", event_types)
            self.assertNotIn("skill.expanded", event_types)

            shortlisted = next(e for e in events if e["type"] == "skill.shortlisted")
            self.assertIn("docker_restart_safe", shortlisted["payload"]["skill_ids"])
            self.assertEqual(shortlisted["payload"]["strategy"], "direct")

            selected = next(e for e in events if e["type"] == "skill.selected")
            self.assertIn("skill_ref", selected["payload"])
            self.assertEqual(
                selected["payload"]["skill_ref"]["id"], "docker_restart_safe"
            )
            self.assertAlmostEqual(selected["payload"]["confidence"], 1.0)
            self.assertEqual(selected["payload"]["selection_mode"], "direct")

    def test_prerouting_event_includes_context_budget_metadata_when_enabled(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")

            class _RunnerLLM:
                contract_version = "v1"

                def estimate_tokens(self, *, model, context):
                    del model, context
                    return 12

                def call_structured(self, **kwargs):
                    schema_name = kwargs["schema"].__name__
                    if schema_name == "Decision":
                        return {
                            "route": "respond",
                            "confidence": 0.9,
                            "reason_code": "mock_decide",
                            "answer": "ok",
                        }
                    raise AssertionError(f"Unexpected schema: {schema_name}")

            skill_api = MagicMock()
            skill_api.catalog_summaries.return_value = [
                {
                    "id": "docker_restart_safe",
                    "name": "Restart Docker Services Safely",
                    "one_liner": "Safely restart docker and verify daemon health.",
                    "version_hash": "a" * 64,
                }
            ]
            skill_api.render_snippet.return_value = (
                "Use the docker restart workflow.",
                "a" * 64,
            )

            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                llm_api=_RunnerLLM(),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                skill_api=skill_api,
                options=RunnerOptions(
                    reflection_enabled=False,
                    metactl_enabled=False,
                ),
            )

            runner.step(
                session_id="s-prerouting-budget",
                user_input="continue fixing the docker issue from earlier",
                trace_id="trace-prerouting-budget",
            )

            events = session.list_events("s-prerouting-budget")
            prerouting = next(e for e in events if e["type"] == "skill.prerouting")
            self.assertEqual(prerouting["payload"]["context_budget"], "full")
            self.assertEqual(prerouting["payload"]["strategy"], "direct")

    def test_canonical_logger_used_by_runner(self) -> None:
        from openminion.modules.brain.runner.tick import run_step
        import inspect

        # Verify the runner flow uses CanonicalEventLogger
        source = inspect.getsource(run_step)
        self.assertIn("CanonicalEventLogger", source)
