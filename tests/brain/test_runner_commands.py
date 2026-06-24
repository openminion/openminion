from tests.brain.runner_test_support import (
    BudgetCounters,
    SimpleNamespace,
    BrainRunner,
    ToolCommand,
    WorkingState,
    _profile,
    fake_session_api,
    fake_tool_api,
    unittest,
)


def _runner(*, tool_names: tuple[str, ...] = ()) -> BrainRunner:
    return BrainRunner(
        profile=_profile(),
        session_api=fake_session_api(),
        tool_api=fake_tool_api(tool_names),
    )


class RunnerCommandTests(unittest.TestCase):
    def test_nl_tool_parsing_surface_removed(self) -> None:
        runner = _runner()
        self.assertFalse(hasattr(runner, "_parse_natural_language_tool"))

    def test_heuristic_decision_no_longer_routes_nl_text_to_tools(self) -> None:
        runner = _runner()
        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            trace_id="t1",
            budgets_remaining=BudgetCounters(
                ticks=5, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=60000
            ),
        )
        decision = runner._heuristic_decision(
            state=state, user_input="what's weather in tokyo?"
        )
        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.respond_kind, "answer")
        self.assertEqual(decision.reason_code, "llm_unavailable")

    def test_command_has_side_effects_false_for_read_only_run_command(self) -> None:
        runner = _runner()
        cmd = ToolCommand(
            title="Tool call: exec.run",
            tool_name="exec.run",
            args={"command": "ls ./"},
            success_criteria={"status": "success"},
            idempotency_key="read-only-ls",
            risk_level="med",
        )
        self.assertFalse(runner._command_has_side_effects(command=cmd))

    def test_command_has_side_effects_fails_closed_for_non_posix_shell_family(
        self,
    ) -> None:
        from openminion.tools.exec.process import ShellFamily
        from unittest.mock import patch

        runner = _runner()
        cmd = ToolCommand(
            title="Tool call: exec.run",
            tool_name="exec.run",
            args={"command": "ls ./"},
            success_criteria={"status": "success"},
            idempotency_key="read-only-ls-non-posix",
            risk_level="med",
        )
        with patch(
            "openminion.modules.brain.runner.turn.resolve_shell_family",
            return_value=ShellFamily.POWERSHELL,
        ):
            self.assertTrue(runner._command_has_side_effects(command=cmd))

    def test_command_has_side_effects_false_for_read_only_command_with_quoted_semicolon(
        self,
    ) -> None:
        runner = _runner()
        cmd = ToolCommand(
            title="Tool call: exec.run",
            tool_name="exec.run",
            args={"command": 'grep "alpha;beta" README.md'},
            success_criteria={"status": "success"},
            idempotency_key="read-only-grep-quoted-semicolon",
            risk_level="med",
        )
        self.assertFalse(runner._command_has_side_effects(command=cmd))

    def test_command_has_side_effects_true_for_destructive_exec_run_command(
        self,
    ) -> None:
        runner = _runner()
        cmd = ToolCommand(
            title="Tool call: exec.run",
            tool_name="exec.run",
            args={"command": "rm -rf /tmp/demo"},
            success_criteria={"status": "success"},
            idempotency_key="destructive-rm",
            risk_level="high",
        )
        self.assertTrue(runner._command_has_side_effects(command=cmd))

    def test_command_has_side_effects_true_for_chained_exec_run_command(self) -> None:
        from openminion.modules.brain.runner import BrainRunner

        runner = BrainRunner(
            profile=_profile(), session_api=fake_session_api(), tool_api=fake_tool_api()
        )
        cmd = ToolCommand(
            title="Tool call: exec.run",
            tool_name="exec.run",
            args={"command": "ls && pwd"},
            success_criteria={"status": "success"},
            idempotency_key="chained-ls-pwd",
            risk_level="med",
        )
        self.assertTrue(runner._command_has_side_effects(command=cmd))

    def test_command_has_side_effects_true_for_unsupported_ampersand_exec_run_command(
        self,
    ) -> None:
        from openminion.modules.brain.runner import BrainRunner

        runner = BrainRunner(
            profile=_profile(), session_api=fake_session_api(), tool_api=fake_tool_api()
        )
        cmd = ToolCommand(
            title="Tool call: exec.run",
            tool_name="exec.run",
            args={"command": "ls &"},
            success_criteria={"status": "success"},
            idempotency_key="unsupported-ampersand",
            risk_level="med",
        )
        self.assertTrue(runner._command_has_side_effects(command=cmd))

    def test_build_forced_file_list_command_fails_closed_without_explicit_args(
        self,
    ) -> None:
        from openminion.modules.brain.runner import BrainRunner
        from openminion.modules.brain.schemas import WorkingState, BudgetCounters

        runner = BrainRunner(
            profile=_profile(), session_api=fake_session_api(), tool_api=fake_tool_api()
        )
        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            trace_id="t1",
            budgets_remaining=BudgetCounters(
                ticks=5, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=60000
            ),
        )
        command = runner._build_forced_tool_command(
            state=state,
            user_input="show me files under this folder",
            tool_name="file.list_dir",
        )
        self.assertIsNone(command)

    def test_build_forced_weather_command_handles_how_about_follow_up(self) -> None:
        from openminion.modules.brain.runner import BrainRunner
        from openminion.modules.brain.schemas import WorkingState, BudgetCounters

        runner = BrainRunner(
            profile=_profile(), session_api=fake_session_api(), tool_api=fake_tool_api()
        )
        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            trace_id="t1",
            budgets_remaining=BudgetCounters(
                ticks=5, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=60000
            ),
        )
        command = runner._build_forced_tool_command(
            state=state,
            user_input="how about china?",
            tool_name="weather",
        )
        self.assertIsNone(command)

    def test_build_forced_weather_command_accepts_bare_location_answer(self) -> None:
        from openminion.modules.brain.runner import BrainRunner
        from openminion.modules.brain.schemas import WorkingState, BudgetCounters

        runner = BrainRunner(
            profile=_profile(), session_api=fake_session_api(), tool_api=fake_tool_api()
        )
        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            trace_id="t1",
            budgets_remaining=BudgetCounters(
                ticks=5, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=60000
            ),
        )
        command = runner._build_forced_tool_command(
            state=state,
            user_input="san francisco",
            tool_name="weather",
        )
        self.assertIsNone(command)

    def test_build_forced_weather_command_stays_fail_closed_without_typed_args(
        self,
    ) -> None:
        from openminion.modules.brain.runner import BrainRunner
        from openminion.modules.brain.schemas import WorkingState, BudgetCounters

        runner = BrainRunner(
            profile=_profile(), session_api=fake_session_api(), tool_api=fake_tool_api()
        )
        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            trace_id="t1",
            budgets_remaining=BudgetCounters(
                ticks=5, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=60000
            ),
        )
        command = runner._build_forced_tool_command(
            state=state,
            user_input="what is the weather in san francisco right now?",
            tool_name="weather",
        )
        self.assertIsNone(command)

    def test_build_forced_search_command_stays_fail_closed_without_explicit_args(
        self,
    ) -> None:
        from openminion.modules.brain.runner import BrainRunner
        from openminion.modules.brain.schemas import WorkingState, BudgetCounters

        runner = BrainRunner(
            profile=_profile(), session_api=fake_session_api(), tool_api=fake_tool_api()
        )
        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            trace_id="t1",
            budgets_remaining=BudgetCounters(
                ticks=5, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=60000
            ),
        )
        command = runner._build_forced_tool_command(
            state=state,
            user_input="check latest news on iran and summarize briefly",
            tool_name="web.search",
        )
        self.assertIsNone(command)

    def test_build_forced_fetch_providers_command_uses_empty_args(self) -> None:
        from openminion.modules.brain.runner import BrainRunner
        from openminion.modules.brain.schemas import WorkingState, BudgetCounters

        runner = BrainRunner(
            profile=_profile(), session_api=fake_session_api(), tool_api=fake_tool_api()
        )
        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            trace_id="t1",
            budgets_remaining=BudgetCounters(
                ticks=5, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=60000
            ),
        )
        command = runner._build_forced_tool_command(
            state=state,
            user_input="list available fetch providers",
            tool_name="fetch.providers",
        )
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.tool_name, "fetch.providers")
        self.assertEqual(command.args, {})

    def test_build_forced_time_now_command_uses_empty_args(self) -> None:
        from openminion.modules.brain.runner import BrainRunner
        from openminion.modules.brain.schemas import BudgetCounters, WorkingState

        runner = BrainRunner(
            profile=_profile(), session_api=fake_session_api(), tool_api=fake_tool_api()
        )
        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            trace_id="t1",
            budgets_remaining=BudgetCounters(
                ticks=5, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=60000
            ),
        )
        command = runner._build_forced_tool_command(
            state=state,
            user_input="what time is it right now",
            tool_name="time",
        )
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.tool_name, "time")
        self.assertEqual(command.args, {})

    def test_build_forced_location_command_uses_empty_args(self) -> None:
        from openminion.modules.brain.runner import BrainRunner
        from openminion.modules.brain.schemas import BudgetCounters, WorkingState

        runner = BrainRunner(
            profile=_profile(), session_api=fake_session_api(), tool_api=fake_tool_api()
        )
        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            trace_id="t1",
            budgets_remaining=BudgetCounters(
                ticks=5, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=60000
            ),
        )
        command = runner._build_forced_tool_command(
            state=state,
            user_input="what city am i near",
            tool_name="location",
        )
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.tool_name, "location")
        self.assertEqual(command.args, {})

    def test_build_forced_exec_run_command_extracts_inline_command(self) -> None:
        from openminion.modules.brain.runner import BrainRunner
        from openminion.modules.brain.schemas import BudgetCounters, WorkingState

        runner = BrainRunner(
            profile=_profile(), session_api=fake_session_api(), tool_api=fake_tool_api()
        )
        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            trace_id="t1",
            budgets_remaining=BudgetCounters(
                ticks=5, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=60000
            ),
        )
        command = runner._build_forced_tool_command(
            state=state,
            user_input="run command pwd and show output",
            tool_name="exec.run",
        )
        self.assertIsNone(command)

    def test_build_forced_fetch_requires_canonical_model_tool_name(self) -> None:
        from openminion.modules.brain.runner import BrainRunner
        from openminion.modules.brain.schemas import WorkingState, BudgetCounters

        runner = BrainRunner(
            profile=_profile(), session_api=fake_session_api(), tool_api=fake_tool_api()
        )
        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            trace_id="t1",
            budgets_remaining=BudgetCounters(
                ticks=5, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=60000
            ),
        )
        command = runner._build_forced_tool_command(
            state=state,
            user_input="fetch www.example.com using scrapling dynamic mode",
            tool_name="fetch.get",
        )
        self.assertIsNone(command)

    def test_validate_tool_args_requires_search_query_without_runtime_repair(
        self,
    ) -> None:
        from openminion.modules.brain.runner import BrainRunner
        from openminion.modules.brain.schemas import (
            ToolCommand,
            WorkingState,
            BudgetCounters,
        )

        runner = BrainRunner(
            profile=_profile(), session_api=fake_session_api(), tool_api=fake_tool_api()
        )
        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            goal="check latest news on iran and summarize briefly",
            budgets_remaining=BudgetCounters(
                ticks=5, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=60000
            ),
        )
        command = ToolCommand(
            title="Tool call: web.search",
            tool_name="web.search",
            args={},  # Missing 'location'
            success_criteria={"status": "success"},
            idempotency_key="test-key",
            risk_level="low",
        )

        result = runner._validate_tool_args(command=command, state=state)
        self.assertIsInstance(result, dict)
        assert isinstance(result, dict)
        self.assertEqual(result.get("reason_code"), "search_query_required")
        self.assertEqual(command.args, {})

    def test_validate_tool_args_requires_weather_location_when_unrecoverable(
        self,
    ) -> None:
        from openminion.modules.brain.runner import BrainRunner
        from openminion.modules.brain.schemas import ToolCommand

        runner = BrainRunner(
            profile=_profile(), session_api=fake_session_api(), tool_api=fake_tool_api()
        )
        command = ToolCommand(
            title="Tool call: weather",
            tool_name="weather",
            args={},
            success_criteria={"status": "success"},
            idempotency_key="test-key",
            risk_level="low",
        )

        result = runner._validate_tool_args(command=command)
        self.assertIsInstance(result, dict)
        assert isinstance(result, dict)
        self.assertEqual(result.get("reason_code"), "weather_location_required")
        self.assertIn("location", str(result.get("message", "")).lower())

    def test_validate_tool_args_accepts_valid_args(self) -> None:
        from openminion.modules.brain.runner import BrainRunner
        from openminion.modules.brain.schemas import ToolCommand

        runner = BrainRunner(
            profile=_profile(), session_api=fake_session_api(), tool_api=fake_tool_api()
        )

        command = ToolCommand(
            title="Tool call: weather",
            tool_name="weather",
            args={"location": "Tokyo"},
            success_criteria={"status": "success"},
            idempotency_key="test-key",
            risk_level="low",
        )

        result = runner._validate_tool_args(command=command)
        self.assertIsNone(result)

    def test_browser_like_nl_prompt_without_explicit_command_is_not_auto_executed(
        self,
    ) -> None:
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            tool_api=fake_tool_api(),
        )
        state = WorkingState(
            session_id="test-session",
            agent_id="test-agent",
            llm_calls_max=5,
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=3, tokens=1000, time_ms=60000
            ),
        )
        decision = runner._heuristic_decision(
            state=state, user_input="open browser and go to https://example.com"
        )
        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.respond_kind, "answer")
        self.assertEqual(decision.reason_code, "llm_unavailable")

    def test_time_like_nl_prompt_without_explicit_command_stays_non_executable(
        self,
    ) -> None:
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            tool_api=fake_tool_api(),
        )
        state = WorkingState(
            session_id="test-session",
            agent_id="test-agent",
            llm_calls_max=5,
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=3, tokens=1000, time_ms=60000
            ),
        )
        decision = runner._heuristic_decision(
            state=state, user_input="what time is now?"
        )
        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.respond_kind, "answer")
        self.assertEqual(decision.reason_code, "llm_unavailable")

    def test_resolve_browser_tool_prefers_pinchtab(self) -> None:
        from openminion.modules.brain.runner import BrainRunner

        class _Profile:
            agent_id = "test-agent"
            llm_profiles = SimpleNamespace(decide_model="test-model")
            defaults = SimpleNamespace(
                auto_save_lessons=False, auto_stage_policy_candidates=False
            )
            budgets = SimpleNamespace(
                max_ticks_per_user_turn=10,
                max_tool_calls=5,
                max_a2a_calls=3,
                max_total_llm_tokens=1000,
                max_elapsed_ms=60000,
            )

        class _MockRegistry:
            def __init__(self):
                self._tools = {
                    "browser.playwright.navigate": SimpleNamespace(
                        name="browser.playwright.navigate"
                    ),
                    "browser.pinchtab.navigate": SimpleNamespace(
                        name="browser.pinchtab.navigate"
                    ),
                }

        runner = BrainRunner(
            profile=_Profile(),
            session_api=fake_session_api(),
            tool_api=SimpleNamespace(registry=_MockRegistry()),
        )

        state = WorkingState(
            session_id="test-session",
            agent_id="test-agent",
            llm_calls_max=5,
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=3, tokens=1000, time_ms=60000
            ),
        )

        result = runner._resolve_browser_tool(state=state)
        self.assertEqual(result, "browser.pinchtab")

    def test_resolve_browser_tool_returns_none_when_no_browser(self) -> None:
        from openminion.modules.brain.runner import BrainRunner

        class _Profile:
            agent_id = "test-agent"
            llm_profiles = SimpleNamespace(decide_model="test-model")
            defaults = SimpleNamespace(
                auto_save_lessons=False, auto_stage_policy_candidates=False
            )
            budgets = SimpleNamespace(
                max_ticks_per_user_turn=10,
                max_tool_calls=5,
                max_a2a_calls=3,
                max_total_llm_tokens=1000,
                max_elapsed_ms=60000,
            )

        class _MockRegistry:
            def __init__(self):
                self._tools = {
                    "weather.openmeteo.current": SimpleNamespace(
                        name="weather.openmeteo.current"
                    ),
                }

        runner = BrainRunner(
            profile=_Profile(),
            session_api=fake_session_api(),
            tool_api=SimpleNamespace(registry=_MockRegistry()),
        )

        state = WorkingState(
            session_id="test-session",
            agent_id="test-agent",
            llm_calls_max=5,
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=3, tokens=1000, time_ms=60000
            ),
        )

        result = runner._resolve_browser_tool(state=state)
        self.assertIsNone(result)
