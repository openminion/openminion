from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace

from openminion.modules.brain.loop.adaptive import effective_soft_cap
from openminion.modules.brain.loop.tools.budget_extension import (
    ADAPTIVE_BUDGET_HARD_CAP,
    STOP_BUDGET_EXHAUSTED,
    STOP_HARD_CAP,
    STOP_NOOP_GUARD,
    STOP_SESSION_EXTENSIONS_EXHAUSTED,
    STOP_TOKEN_BUDGET_EXHAUSTED,
    approve_pending_extension,
    apply_extension,
    check_safety_rails,
    clear_pending_extension,
    compose_pause_question,
    consume_approved_extension,
    get_pending_extension,
    get_session_extensions_used,
    is_pending_extension_expired,
    mark_pending_extension,
    record_session_extension,
)
from openminion.modules.brain.loop.tools.contracts import AdaptiveToolLoopState
from openminion.modules.brain.runner.tick.confirmation import (
    process as confirmation_process,
)
from openminion.modules.brain.runner.tick.context import TickRunContext
from openminion.modules.brain.schemas import AdaptiveBudgetConfig, AskUserCommand


class UpFrontScalingFromMaxStepsHintTests(unittest.TestCase):
    def test_hint_below_soft_cap_keeps_soft_cap(self) -> None:
        cfg = AdaptiveBudgetConfig(soft_cap=24)
        dec = SimpleNamespace(max_steps_hint=10, sub_intents=[])
        self.assertEqual(effective_soft_cap(dec, cfg), 24)

    def test_hint_above_soft_cap_scales_up(self) -> None:
        cfg = AdaptiveBudgetConfig(soft_cap=24)
        dec = SimpleNamespace(max_steps_hint=40, sub_intents=[])
        self.assertEqual(effective_soft_cap(dec, cfg), 46)  # 40 + 6

    def test_hint_zero_or_none_keeps_soft_cap(self) -> None:
        cfg = AdaptiveBudgetConfig(soft_cap=24)
        dec_none = SimpleNamespace(max_steps_hint=None, sub_intents=[])
        dec_zero = SimpleNamespace(max_steps_hint=0, sub_intents=[])
        self.assertEqual(effective_soft_cap(dec_none, cfg), 24)
        self.assertEqual(effective_soft_cap(dec_zero, cfg), 24)


class UpFrontScalingFromSubIntentsTests(unittest.TestCase):
    def test_single_sub_intent_does_not_scale(self) -> None:
        cfg = AdaptiveBudgetConfig(soft_cap=24)
        dec = SimpleNamespace(max_steps_hint=None, sub_intents=["only"])
        self.assertEqual(effective_soft_cap(dec, cfg), 24)

    def test_two_sub_intents_doubles(self) -> None:
        cfg = AdaptiveBudgetConfig(soft_cap=24)
        dec = SimpleNamespace(max_steps_hint=None, sub_intents=["a", "b"])
        self.assertEqual(effective_soft_cap(dec, cfg), 48)  # 24 * 2

    def test_many_sub_intents_scales_with_count(self) -> None:
        cfg = AdaptiveBudgetConfig(soft_cap=20)
        dec = SimpleNamespace(max_steps_hint=None, sub_intents=["a", "b", "c", "d"])
        self.assertEqual(effective_soft_cap(dec, cfg), 80)  # 20 * 4

    def test_hint_and_sub_intents_use_max(self) -> None:
        cfg = AdaptiveBudgetConfig(soft_cap=24)
        dec = SimpleNamespace(max_steps_hint=60, sub_intents=["a", "b"])
        self.assertEqual(effective_soft_cap(dec, cfg), 66)


class InteractiveExtensionApprovalPathTests(unittest.TestCase):
    def test_mark_pending_stamps_metadata_with_expiry(self) -> None:
        state = SimpleNamespace(module_state={})
        clock = iter([1000.0, 1000.0]).__next__
        meta = mark_pending_extension(
            state=state,
            cap_at_pause=24,
            extend_by=12,
            idle_timeout_s=300,
            clock=clock,
        )
        self.assertEqual(meta["cap_at_pause"], 24)
        self.assertEqual(meta["extend_by"], 12)
        self.assertEqual(meta["expires_at"], 1300.0)
        pending = get_pending_extension(state=state)
        self.assertEqual(pending, meta)

    def test_clear_pending_returns_and_removes(self) -> None:
        state = SimpleNamespace(module_state={})
        mark_pending_extension(
            state=state, cap_at_pause=24, extend_by=12, idle_timeout_s=300
        )
        cleared = clear_pending_extension(state=state)
        self.assertIsNotNone(cleared)
        self.assertIsNone(get_pending_extension(state=state))

    def test_approval_applies_extension_and_counts(self) -> None:
        state = SimpleNamespace(module_state={})
        loop_state = AdaptiveToolLoopState()
        loop_state.effective_max_iterations = 24
        cfg = AdaptiveBudgetConfig(soft_cap=24, extend_by=12)

        mark_pending_extension(
            state=state, cap_at_pause=24, extend_by=12, idle_timeout_s=300
        )
        clear_pending_extension(state=state)
        new_cap = apply_extension(config=cfg, loop_state=loop_state)
        session_used = record_session_extension(state=state)

        self.assertEqual(new_cap, 36)
        self.assertEqual(loop_state.effective_max_iterations, 36)
        self.assertEqual(loop_state.extensions_used, 1)
        self.assertEqual(session_used, 1)

    def test_approval_metadata_is_one_shot_for_next_adaptive_turn(self) -> None:
        state = SimpleNamespace(module_state={})
        mark_pending_extension(
            state=state, cap_at_pause=24, extend_by=12, idle_timeout_s=300
        )

        approved = approve_pending_extension(state=state)

        self.assertIsNotNone(approved)
        self.assertIsNone(get_pending_extension(state=state))
        self.assertEqual(approved["target_cap"], 36)
        self.assertEqual(approved["session_extensions_used"], 1)
        self.assertEqual(consume_approved_extension(state=state), approved)
        self.assertIsNone(consume_approved_extension(state=state))


class _BudgetConfirmationPolicy:
    def parse_confirmation_response(self, text: str) -> str:
        text = text.strip().lower()
        if text in {"yes", "y"}:
            return "affirm"
        if text in {"no", "n"}:
            return "deny"
        return "unclear"


class _BudgetConfirmationRunner:
    def __init__(self) -> None:
        self.policy_api = _BudgetConfirmationPolicy()
        self.options = SimpleNamespace(
            adaptive_replan_retained_step_outputs=0,
            max_replans=0,
            max_retries_per_step=0,
        )

    def _respond_with_meta(self, **kwargs):
        return SimpleNamespace(**kwargs)


class _BudgetConfirmationLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(self, name: str, payload: dict[str, object], **_kwargs: object) -> None:
        self.events.append((name, dict(payload)))


def _budget_confirmation_state() -> SimpleNamespace:
    state = SimpleNamespace(
        session_id="sess-aib-confirm",
        trace_id="trace-aib-confirm",
        module_state={},
        pending_confirmation_command=AskUserCommand(
            title="Iteration budget reached",
            question="Continue for more iterations?",
            inputs={"adaptive_budget_extension": True, "cap": 24},
            success_criteria={"extension_approved": True},
        ),
        pending_confirmation_sub_intents=[],
        pending_confirmation_sub_intent_refs=[],
        pending_confirmation_rationale="",
        pending_confirmation_success_criteria={},
        pending_confirmation_feasibility_state={},
        pending_confirmation_feasibility_report=None,
        post_action_user_message="Continue for more iterations?",
    )
    mark_pending_extension(
        state=state, cap_at_pause=24, extend_by=12, idle_timeout_s=300
    )
    return state


class InteractiveConfirmationIntegrationTests(unittest.TestCase):
    def test_affirm_approves_one_shot_extension_and_continues_dispatch(self) -> None:
        state = _budget_confirmation_state()
        logger = _BudgetConfirmationLogger()
        tick_ctx = TickRunContext(session_id=state.session_id, user_input="yes")

        result = confirmation_process(
            runner=_BudgetConfirmationRunner(),
            state=state,
            logger=logger,
            tick_ctx=tick_ctx,
        )

        self.assertIsNone(result)
        self.assertIsNone(state.pending_confirmation_command)
        self.assertIsNone(get_pending_extension(state=state))
        self.assertEqual(consume_approved_extension(state=state)["target_cap"], 36)
        self.assertIsNone(tick_ctx.user_input)
        self.assertFalse(tick_ctx.skip_decide)
        self.assertTrue(tick_ctx.consume_user_input_for_command)
        self.assertIn(
            (
                "budget.extended",
                {
                    "by": 12,
                    "total": 36,
                    "extensions_used": 1,
                    "session_extensions_used": 1,
                    "trigger": "user",
                },
            ),
            logger.events,
        )

    def test_deny_emits_declined_and_clears_pending_extension(self) -> None:
        state = _budget_confirmation_state()
        logger = _BudgetConfirmationLogger()
        tick_ctx = TickRunContext(session_id=state.session_id, user_input="no")

        result = confirmation_process(
            runner=_BudgetConfirmationRunner(),
            state=state,
            logger=logger,
            tick_ctx=tick_ctx,
        )

        self.assertIsNotNone(result)
        self.assertIsNone(state.pending_confirmation_command)
        self.assertIsNone(get_pending_extension(state=state))
        self.assertTrue(tick_ctx.consume_user_input_for_command)
        event_names = [name for name, _payload in logger.events]
        self.assertIn("budget.user_declined", event_names)


class InteractiveDeclineAndTimeoutPathsTests(unittest.TestCase):
    def test_timeout_check_before_expiry(self) -> None:
        state = SimpleNamespace(module_state={})
        clock = iter([1000.0, 1200.0]).__next__
        mark_pending_extension(
            state=state,
            cap_at_pause=24,
            extend_by=12,
            idle_timeout_s=300,
            clock=clock,
        )
        meta = get_pending_extension(state=state)
        self.assertIsNotNone(meta)
        self.assertFalse(is_pending_extension_expired(meta, clock=lambda: 1200.0))

    def test_timeout_check_after_expiry(self) -> None:
        state = SimpleNamespace(module_state={})
        clock = iter([1000.0]).__next__
        mark_pending_extension(
            state=state,
            cap_at_pause=24,
            extend_by=12,
            idle_timeout_s=300,
            clock=clock,
        )
        meta = get_pending_extension(state=state)
        self.assertIsNotNone(meta)
        self.assertTrue(is_pending_extension_expired(meta, clock=lambda: 1400.0))

    def test_decline_clears_without_applying_extension(self) -> None:
        state = SimpleNamespace(module_state={})
        loop_state = AdaptiveToolLoopState()
        loop_state.effective_max_iterations = 24
        mark_pending_extension(
            state=state, cap_at_pause=24, extend_by=12, idle_timeout_s=300
        )
        cleared = clear_pending_extension(state=state)
        self.assertIsNotNone(cleared)
        self.assertEqual(loop_state.effective_max_iterations, 24)
        self.assertEqual(loop_state.extensions_used, 0)


class AutonomousSilentExtensionTests(unittest.TestCase):
    def test_apply_extension_bumps_cap_by_extend_by(self) -> None:
        loop_state = AdaptiveToolLoopState()
        loop_state.effective_max_iterations = 24
        cfg = AdaptiveBudgetConfig(soft_cap=24, extend_by=12)
        new_cap = apply_extension(config=cfg, loop_state=loop_state)
        self.assertEqual(new_cap, 36)
        self.assertEqual(loop_state.extensions_used, 1)

    def test_multiple_extensions_compound(self) -> None:
        loop_state = AdaptiveToolLoopState()
        loop_state.effective_max_iterations = 24
        cfg = AdaptiveBudgetConfig(soft_cap=24, extend_by=12)
        apply_extension(config=cfg, loop_state=loop_state)
        apply_extension(config=cfg, loop_state=loop_state)
        self.assertEqual(loop_state.effective_max_iterations, 48)
        self.assertEqual(loop_state.extensions_used, 2)


class StrictModeZeroExtensionsTests(unittest.TestCase):
    def test_zero_per_turn_blocks_extension(self) -> None:
        cfg = AdaptiveBudgetConfig(max_extensions_per_turn=0)
        loop_state = AdaptiveToolLoopState()
        loop_state.effective_max_iterations = 24
        stop = check_safety_rails(
            config=cfg,
            loop_state=loop_state,
            session_extensions_used=0,
            tokens_used=0,
            max_total_llm_tokens=500_000,
        )
        self.assertEqual(stop, STOP_BUDGET_EXHAUSTED)


class SafetyRailsBlockExtensionTests(unittest.TestCase):
    def test_noop_guard_blocks_when_threshold_hit(self) -> None:
        cfg = AdaptiveBudgetConfig(
            max_extensions_per_turn=3, max_adaptive_noops_per_turn=3
        )
        loop_state = AdaptiveToolLoopState()
        loop_state.effective_max_iterations = 24
        loop_state.consecutive_noops = 3
        stop = check_safety_rails(
            config=cfg,
            loop_state=loop_state,
            session_extensions_used=0,
            tokens_used=0,
            max_total_llm_tokens=500_000,
        )
        self.assertEqual(stop, STOP_NOOP_GUARD)

    def test_token_budget_exhausted_blocks(self) -> None:
        cfg = AdaptiveBudgetConfig(max_extensions_per_turn=3)
        loop_state = AdaptiveToolLoopState()
        loop_state.effective_max_iterations = 24
        stop = check_safety_rails(
            config=cfg,
            loop_state=loop_state,
            session_extensions_used=0,
            tokens_used=455_000,
            max_total_llm_tokens=500_000,
        )
        self.assertEqual(stop, STOP_TOKEN_BUDGET_EXHAUSTED)

    def test_session_extensions_exhausted_blocks(self) -> None:
        cfg = AdaptiveBudgetConfig(
            max_extensions_per_turn=3, max_extensions_per_session=10
        )
        loop_state = AdaptiveToolLoopState()
        loop_state.effective_max_iterations = 24
        stop = check_safety_rails(
            config=cfg,
            loop_state=loop_state,
            session_extensions_used=10,
            tokens_used=0,
            max_total_llm_tokens=500_000,
        )
        self.assertEqual(stop, STOP_SESSION_EXTENSIONS_EXHAUSTED)

    def test_all_rails_pass_returns_none(self) -> None:
        cfg = AdaptiveBudgetConfig(max_extensions_per_turn=3)
        loop_state = AdaptiveToolLoopState()
        loop_state.effective_max_iterations = 24
        stop = check_safety_rails(
            config=cfg,
            loop_state=loop_state,
            session_extensions_used=0,
            tokens_used=0,
            max_total_llm_tokens=500_000,
        )
        self.assertIsNone(stop)


class HardCapNeverExceededTests(unittest.TestCase):
    def test_apply_extension_clamps_to_hard_cap(self) -> None:
        loop_state = AdaptiveToolLoopState()
        loop_state.effective_max_iterations = 120
        cfg = AdaptiveBudgetConfig(soft_cap=24, extend_by=50)
        new_cap = apply_extension(config=cfg, loop_state=loop_state)
        self.assertEqual(new_cap, ADAPTIVE_BUDGET_HARD_CAP)
        self.assertEqual(loop_state.effective_max_iterations, 128)

    def test_safety_rail_blocks_at_hard_cap(self) -> None:
        cfg = AdaptiveBudgetConfig(max_extensions_per_turn=10)
        loop_state = AdaptiveToolLoopState()
        loop_state.effective_max_iterations = ADAPTIVE_BUDGET_HARD_CAP
        stop = check_safety_rails(
            config=cfg,
            loop_state=loop_state,
            session_extensions_used=0,
            tokens_used=0,
            max_total_llm_tokens=500_000,
        )
        self.assertEqual(stop, STOP_HARD_CAP)


class PPLDenominatorUsesDynamicCapTests(unittest.TestCase):
    def test_no_emission_site_uses_static_profile_cap(self) -> None:
        from openminion.modules.brain.loop.tools import engine

        source = inspect.getsource(engine)
        self.assertNotIn(
            "llm_call_limit=profile.max_iterations",
            source,
            "AIB-06 regression guard: every `_set_turn_progress` site "
            "must use `_effective_cap(profile, loop_state)`.",
        )

    def test_helper_reads_loop_state_dynamic_cap_when_set(self) -> None:
        from openminion.modules.brain.loop.tools.engine import _effective_cap

        profile = SimpleNamespace(max_iterations=24)
        loop_state = AdaptiveToolLoopState()
        self.assertEqual(_effective_cap(profile, loop_state), 24)
        loop_state.effective_max_iterations = 36
        self.assertEqual(_effective_cap(profile, loop_state), 36)


class CanonicalEventRegistrationTests(unittest.TestCase):
    EXPECTED_EVENTS = frozenset(
        {
            "budget.allocated",
            "budget.extended",
            "budget.exhausted",
            "budget.noop_guard",
            "budget.user_declined",
            "budget.user_timeout",
            "budget.high_watermark",
        }
    )

    def test_all_budget_events_registered(self) -> None:
        from openminion.modules.session.storage.replay import (
            _KNOWN_CANONICAL_EVENT_TYPES,
        )

        missing = self.EXPECTED_EVENTS - _KNOWN_CANONICAL_EVENT_TYPES
        self.assertFalse(
            missing,
            f"AIB-09: canonical event registry is missing "
            f"{sorted(missing)}. All `budget.*` events must register "
            f"so replay consumers can dispatch them.",
        )


class PausePromptCompositionTests(unittest.TestCase):
    def test_prompt_includes_iteration_and_cap(self) -> None:
        loop_state = AdaptiveToolLoopState()
        loop_state.iteration = 24
        loop_state.effective_max_iterations = 24
        cfg = AdaptiveBudgetConfig(extend_by=12)
        question = compose_pause_question(
            config=cfg,
            loop_state=loop_state,
            active_work_summary="Plan next-step search",
        )
        self.assertIn("Budget reached: 24/24 iterations", question)
        self.assertIn("Working on: Plan next-step search", question)
        self.assertIn("Continue for up to 12 more iterations?", question)

    def test_prompt_includes_step_summaries_capped_at_five(self) -> None:
        loop_state = AdaptiveToolLoopState()
        loop_state.iteration = 24
        loop_state.effective_max_iterations = 24
        cfg = AdaptiveBudgetConfig()
        steps = tuple(f"step-{i}" for i in range(8))
        question = compose_pause_question(
            config=cfg,
            loop_state=loop_state,
            step_summaries=steps,
        )
        for step in ("step-3", "step-4", "step-5", "step-6", "step-7"):
            self.assertIn(step, question)
        self.assertNotIn("step-0", question)
        self.assertNotIn("step-1", question)
        self.assertNotIn("step-2", question)

    def test_prompt_includes_remaining_estimate_when_hint_set(self) -> None:
        loop_state = AdaptiveToolLoopState()
        loop_state.iteration = 20
        loop_state.effective_max_iterations = 24
        cfg = AdaptiveBudgetConfig()
        question = compose_pause_question(
            config=cfg,
            loop_state=loop_state,
            max_steps_hint=30,  # remaining = 30 - 20 = 10
        )
        self.assertIn("Remaining estimate: ~10 steps", question)


class SessionExtensionsCounterTests(unittest.TestCase):
    def test_counter_increments_per_recorded_extension(self) -> None:
        state = SimpleNamespace(module_state={})
        self.assertEqual(get_session_extensions_used(state=state), 0)
        record_session_extension(state=state)
        record_session_extension(state=state)
        self.assertEqual(get_session_extensions_used(state=state), 2)
