from __future__ import annotations

import inspect
import unittest

from openminion.modules.brain.diagnostics.status import (
    PhaseStatus,
    format_phase_status_text,
)


class GapAPreCallEmitAlwaysFiresTests(unittest.TestCase):
    def test_orchestration_pre_call_emit_is_no_longer_gated_on_estimate(
        self,
    ) -> None:
        from openminion.modules.brain.loop import orchestration

        source = inspect.getsource(orchestration)
        self.assertIn(
            '_pre_call_emit = getattr(runner, "_emit_phase_status", None)',
            source,
            "pre-call emit hook should still be resolved from runner.",
        )
        self.assertNotIn(
            "if callable(_pre_call_emit) and _estimated_outbound > 0:",
            source,
            "Gap A is fixed (PPL-02): the `_estimated_outbound > 0` "
            "gate must NOT be present; the pre-call emit should fire "
            "whenever the hook is callable.",
        )
        self.assertIn(
            "if callable(_pre_call_emit):",
            source,
            "Pre-call emit now guards only on hook availability. If a "
            "future refactor reintroduces the estimate gate, this test "
            "fails loudly.",
        )

    def test_pre_call_emit_carries_call_count_and_decide_phase(self) -> None:
        from openminion.modules.brain.loop import orchestration

        source = inspect.getsource(orchestration)
        # The emit block follows the `if callable(_pre_call_emit):` guard.
        guard_marker = "if callable(_pre_call_emit):"
        start = source.find(guard_marker)
        self.assertGreaterEqual(start, 0)
        body_region = source[start : start + 1200]
        self.assertIn('source_phase="DECIDE"', body_region)
        self.assertIn('"turn.llm_call_count": 1', body_region)
        self.assertIn('"turn.llm_call_limit": 1', body_region)
        self.assertIn("if _estimated_outbound > 0:", body_region)
        conditional_start = body_region.find("if _estimated_outbound > 0:")
        conditional_region = body_region[conditional_start : conditional_start + 400]
        self.assertIn("total_input_tokens_used", conditional_region)
        self.assertIn("token_usage_estimated", conditional_region)


class GapCPhaseLabelComposedWithProgressTests(unittest.TestCase):
    def test_specific_phase_label_injected_after_llm_slot(self) -> None:
        text = format_phase_status_text(
            PhaseStatus(
                trace_id="trace-ppl-gap-c-analyzing",
                status_key="analyzing",
                label="Analyzing request...",
                llm_call_count=1,
                llm_call_limit=12,
            )
        )
        self.assertEqual(text, "LLM 1/12 | Analyzing request...")

    def test_specific_phase_label_injected_with_tokens_only(self) -> None:
        text = format_phase_status_text(
            PhaseStatus(
                trace_id="trace-ppl-gap-c-tokens-only",
                status_key="executing",
                label="Executing step...",
                total_input_tokens_used=6200,
                total_output_tokens_used=412,
            )
        )
        self.assertEqual(text, "Executing step... | ↑6.2k ↓412 tokens")

    def test_specific_phase_label_with_all_slots(self) -> None:
        text = format_phase_status_text(
            PhaseStatus(
                trace_id="trace-ppl-gap-c-full",
                status_key="executing",
                label="Executing step...",
                llm_call_count=2,
                llm_call_limit=12,
                total_input_tokens_used=14100,
                total_output_tokens_used=722,
                tool_name="web.search",
            )
        )
        self.assertEqual(
            text,
            "LLM 2/12 | Executing step... | ↑14.1k ↓722 tokens | tool: Web Search",
        )

    def test_generic_working_status_stays_progress_only(self) -> None:
        text = format_phase_status_text(
            PhaseStatus(
                trace_id="trace-ppl-gap-c-working",
                status_key="working",
                label="Working...",
                llm_call_count=2,
                llm_call_limit=12,
                total_tokens_used=1500,
                tool_name="location.get",
            )
        )
        self.assertEqual(
            text,
            "LLM 2/12 | 1.5k tokens | tool: location.get",
        )

    def test_phase_label_preserved_when_no_progress_fields(self) -> None:
        text = format_phase_status_text(
            PhaseStatus(
                trace_id="trace-ppl-gap-c-control",
                status_key="analyzing",
                label="Analyzing request...",
                detail_text="Preparing turn...",
            )
        )
        self.assertEqual(text, "Analyzing request... Preparing turn...")

    def test_terminal_and_waiting_states_are_label_only(self) -> None:
        waiting = format_phase_status_text(
            PhaseStatus(
                trace_id="trace-ppl-gap-c-waiting",
                status_key="waiting_for_user",
                label="Waiting for your reply...",
                # Even with progress fields set, waiting must stay label-only.
                llm_call_count=3,
                llm_call_limit=12,
            )
        )
        self.assertEqual(waiting, "Waiting for your reply...")

        completed = format_phase_status_text(
            PhaseStatus(
                trace_id="trace-ppl-gap-c-completed",
                status_key="completed",
                label="Completed.",
                terminal=True,
            )
        )
        self.assertEqual(completed, "Completed.")


class GapDPrepareTurnProgressCallbackTests(unittest.TestCase):
    def test_prepare_turn_accepts_progress_callback_kwarg(self) -> None:
        from openminion.services.brain.post_execution.mixin import (
            BrainBridgeTurnMixin,
        )

        prepare_sig = inspect.signature(BrainBridgeTurnMixin._prepare_turn)
        self.assertIn(
            "progress_callback",
            prepare_sig.parameters,
            "Gap D fix (PPL-07): `_prepare_turn` must accept "
            "`progress_callback` as a kwarg so prep sub-steps can emit "
            "phase status. If a refactor drops this param, cold-start "
            "silent-window regresses.",
        )
        param = prepare_sig.parameters["progress_callback"]
        self.assertIs(
            param.default,
            None,
            "`progress_callback` must default to None so non-interactive "
            "callers (e.g. daemon streaming, Telegram, HTTP) are unaffected.",
        )

    def test_run_turn_forwards_progress_callback_to_prepare_and_execute(
        self,
    ) -> None:
        from openminion.services.brain.post_execution import mixin as mixin_module

        source = inspect.getsource(mixin_module.BrainBridgeTurnMixin.run_turn)
        self.assertIn(
            "await self._prepare_turn(",
            source,
            "run_turn should still delegate prep to _prepare_turn.",
        )
        self.assertIn(
            '"progress_callback": progress_callback',
            source,
            "run_turn must preserve the callback in the shared execute arguments.",
        )
        self.assertIn("self._execute_turn(**execute_kwargs)", source)
        # _prepare_turn now ALSO receives the callback.
        prepare_region_start = source.find("self._prepare_turn(")
        self.assertGreaterEqual(prepare_region_start, 0)
        # Grab a generous window for the multi-line kwargs block.
        prepare_region = source[prepare_region_start : prepare_region_start + 1200]
        self.assertIn(
            "progress_callback=progress_callback",
            prepare_region,
            "Gap D fix (PPL-07): `run_turn` must forward "
            "`progress_callback` into `_prepare_turn`. Pre-PPL-07 only "
            "`_execute_turn` received it.",
        )

    def test_prepare_turn_emits_at_entry_and_sub_steps(self) -> None:
        from openminion.services.brain.post_execution import mixin as mixin_module

        prep_source = inspect.getsource(mixin_module.BrainBridgeTurnMixin._prepare_turn)
        self.assertIn("_emit_prep_status(", prep_source)
        self.assertIn('detail_text="Preparing turn..."', prep_source)
        self.assertIn('detail_text="Loading memory context..."', prep_source)
        self.assertIn('detail_text="Loading session history..."', prep_source)

    def test_emit_prep_status_reuses_existing_status_key(self) -> None:
        from openminion.services.brain.post_execution.mixin import _emit_prep_status

        captured: list[PhaseStatus] = []

        def _capture(status: PhaseStatus) -> None:
            captured.append(status)

        _emit_prep_status(
            _capture,
            trace_id="test-prep-trace",
            detail_text="Preparing turn...",
        )
        self.assertEqual(len(captured), 1)
        emitted = captured[0]
        self.assertEqual(
            emitted.status_key,
            "analyzing",
            "Prep emissions must normalize to an existing StatusKey; "
            "a new `preparing` key would require label/map/test/doc "
            "updates across the status module (see spec §4.4 guardrail).",
        )
        self.assertEqual(emitted.label, "Analyzing request...")
        self.assertEqual(emitted.detail_text, "Preparing turn...")
        self.assertEqual(emitted.source_phase, "DECIDE")

    def test_emit_prep_status_with_none_callback_is_noop(self) -> None:
        from openminion.services.brain.post_execution.mixin import _emit_prep_status

        _emit_prep_status(
            None,
            trace_id="test-prep-trace",
            detail_text="Preparing turn...",
        )

    def test_emit_prep_status_swallows_callback_exceptions(self) -> None:
        from openminion.services.brain.post_execution.mixin import _emit_prep_status

        def _bad_callback(_status: PhaseStatus) -> None:
            raise RuntimeError("callback blew up")

        # Should not raise.
        _emit_prep_status(
            _bad_callback,
            trace_id="test-prep-trace",
            detail_text="Preparing turn...",
        )


# Gap B (regression) — continuation forwards progress_callback


class GapBContinuationCallbackForwardedTests(unittest.TestCase):
    @staticmethod
    def _continuation_call_site_kwargs() -> str:
        from openminion.modules.brain.loop import continuation as continuation_module

        fn_source = inspect.getsource(
            continuation_module.run_with_autonomous_continuation
        )
        kwarg_marker = 'trigger="plan_continuation",'
        kwarg_idx = fn_source.find(kwarg_marker)
        assert kwarg_idx >= 0, (
            'Could not locate the `trigger="plan_continuation",` '
            "kwarg in `run_with_autonomous_continuation`. The real "
            "continuation call site must use the kwarg (with trailing "
            "comma) — docstring references use a closing-paren form."
        )
        call_start = fn_source.rfind("runner.run(", 0, kwarg_idx)
        assert call_start >= 0, (
            "Could not locate an enclosing `runner.run(` before the "
            "continuation trigger kwarg."
        )
        return fn_source[call_start : call_start + 2000]

    def test_continuation_runner_run_forwards_progress_callback(self) -> None:
        kwargs_region = self._continuation_call_site_kwargs()
        self.assertIn(
            "progress_callback=progress_callback",
            kwargs_region,
            "Gap B regression: continuation turn must forward "
            "`progress_callback`. Previously dropped; fixed by the PAE "
            "round-three review. Do not silently drop it in future "
            "refactors.",
        )

    def test_continuation_runner_run_forwards_approval_callback(self) -> None:
        kwargs_region = self._continuation_call_site_kwargs()
        self.assertIn(
            "approval_callback=approval_callback",
            kwargs_region,
            "Autonomous continuation turns must preserve the interactive "
            "approval owner so approved work can finish in the same turn.",
        )


# PPL-03 — LLM-boundary emit alignment audit (regression guard)


class PPL03LLMBoundaryEmitAlignmentTests(unittest.TestCase):
    def test_every_set_turn_progress_call_in_engine_has_call_count_and_limit(
        self,
    ) -> None:
        from openminion.modules.brain.loop.tools import engine as engine_module
        from openminion.modules.brain.loop.tools.iteration import (
            dispatch as loop_dispatch,
            execution as loop_execution,
        )
        from openminion.modules.brain.loop.tools.postprocess import (
            engine as postprocess_engine,
        )

        source = "\n".join(
            (
                inspect.getsource(engine_module),
                inspect.getsource(loop_dispatch),
                inspect.getsource(loop_execution),
                inspect.getsource(postprocess_engine),
            )
        )
        # Find every `_set_turn_progress(...)` call site.
        cursor = 0
        call_sites: list[str] = []
        while True:
            idx = source.find("set_turn_progress(", cursor)
            if idx < 0:
                break
            # Skip the definition itself — we want call sites only.
            if source[idx - len("def ") : idx].endswith("def "):
                cursor = idx + len("set_turn_progress(")
                continue
            # Grab a generous kwargs window. Calls are multi-line with
            # `loop_state,` + kwargs; 500 chars covers all observed sites.
            call_sites.append(source[idx : idx + 500])
            cursor = idx + len("set_turn_progress(")

        # Engine has at least 5 callsites (thinking, post-LLM, tool
        # selection, plan tool, tool_request tool, generic tool).
        self.assertGreaterEqual(
            len(call_sites),
            5,
            f"Expected ≥5 _set_turn_progress callsites across the adaptive loop owners; "
            f"found {len(call_sites)}. If the loop is smaller, this "
            f"threshold can drop, but the audit coverage should be "
            f"re-verified.",
        )
        for idx, call in enumerate(call_sites):
            self.assertIn(
                "llm_call_count=",
                call,
                f"_set_turn_progress call #{idx} in the adaptive loop owners does not "
                f"pass `llm_call_count=`. PPL-03 invariant: every "
                f"adaptive-loop turn-progress update carries the LLM "
                f"call count so the display's `LLM N/M` slot stays "
                f"populated.\nCall window:\n{call}",
            )
            self.assertIn(
                "llm_call_limit=",
                call,
                f"_set_turn_progress call #{idx} in the adaptive loop owners does not "
                f"pass `llm_call_limit=`. PPL-03 invariant: every "
                f"adaptive-loop turn-progress update carries the LLM "
                f"call limit so the display's `LLM N/M` slot shows the "
                f"denominator correctly.\nCall window:\n{call}",
            )
