from __future__ import annotations

import inspect
import unittest

from openminion.modules.brain.config import (
    ADAPTIVE_MAX_ITERATIONS,
    ADAPTIVE_MAX_TOOL_CALLS,
    CODING_MAX_ITERATIONS,
    CODING_MAX_SELF_CORRECTIONS,
)
from openminion.modules.brain.schemas.agent import ModeProfileConfig


# Claim (a) — default adaptive soft cap is ADAPTIVE_MAX_ITERATIONS


class DefaultAdaptiveCapConstantsTests(unittest.TestCase):
    def test_adaptive_max_iterations_is_24(self) -> None:
        self.assertEqual(ADAPTIVE_MAX_ITERATIONS, 24)

    def test_adaptive_max_tool_calls_is_32(self) -> None:
        self.assertEqual(ADAPTIVE_MAX_TOOL_CALLS, 32)

    def test_coding_max_iterations_is_40(self) -> None:
        self.assertEqual(CODING_MAX_ITERATIONS, 40)

    def test_coding_max_self_corrections_is_7(self) -> None:
        self.assertEqual(CODING_MAX_SELF_CORRECTIONS, 7)

    def test_max_adaptive_iterations_pydantic_ceiling_is_100(self) -> None:
        fields = ModeProfileConfig.model_fields
        self.assertIn("max_adaptive_iterations", fields)
        field = fields["max_adaptive_iterations"]
        metadata = list(field.metadata)
        le_constraints = [m for m in metadata if type(m).__name__ == "Le"]
        self.assertTrue(
            le_constraints,
            "max_adaptive_iterations field has no `le` constraint.",
        )
        self.assertEqual(
            le_constraints[0].le,
            100,
            "Expected AIB-02 Pydantic ceiling of 100.",
        )


# Claim (b) — up-front scaling from typed Decision fields (AIB-05 fix)


class UpFrontScalingFromDecisionFieldsTests(unittest.TestCase):
    def test_effective_soft_cap_helper_exists(self) -> None:
        from openminion.modules.brain.loop import adaptive

        self.assertTrue(
            hasattr(adaptive, "effective_soft_cap"),
            "AIB-05: `effective_soft_cap(decision, config)` must exist "
            "in `modules/brain/loop/adaptive.py`.",
        )

    def test_effective_soft_cap_bumps_from_max_steps_hint(self) -> None:
        from openminion.modules.brain.loop.adaptive import effective_soft_cap
        from openminion.modules.brain.schemas import AdaptiveBudgetConfig

        class _Dec:
            max_steps_hint = 30
            sub_intents: list[str] = []

        cap = effective_soft_cap(_Dec(), AdaptiveBudgetConfig(soft_cap=24))
        self.assertEqual(cap, 36)  # 30 + 6

    def test_effective_soft_cap_scales_with_sub_intents(self) -> None:
        from openminion.modules.brain.loop.adaptive import effective_soft_cap
        from openminion.modules.brain.schemas import AdaptiveBudgetConfig

        class _Dec:
            max_steps_hint = None
            sub_intents = ["a", "b", "c"]

        cap = effective_soft_cap(_Dec(), AdaptiveBudgetConfig(soft_cap=24))
        self.assertEqual(cap, 72)  # 24 * 3

    def test_effective_soft_cap_clamps_to_hard_cap(self) -> None:
        from openminion.modules.brain.loop.adaptive import effective_soft_cap
        from openminion.modules.brain.schemas import AdaptiveBudgetConfig

        class _Dec:
            max_steps_hint = 500  # unreasonably large
            sub_intents: list[str] = []

        cap = effective_soft_cap(_Dec(), AdaptiveBudgetConfig(soft_cap=24))
        self.assertEqual(cap, 128)  # clamped


# Claim (c) — dynamic cap via loop state + extension mechanism (AIB-06 fix)


class LoopStateCarriesDynamicCapTests(unittest.TestCase):
    def test_loop_state_carries_dynamic_cap_fields(self) -> None:
        from openminion.modules.brain.loop.tools.contracts import (
            AdaptiveToolLoopState,
        )

        annotations = getattr(AdaptiveToolLoopState, "__annotations__", {})
        self.assertIn(
            "effective_max_iterations",
            annotations,
            "AIB-06: `AdaptiveToolLoopState` must carry "
            "`effective_max_iterations` for mid-turn extension.",
        )
        self.assertIn(
            "extensions_used",
            annotations,
            "AIB-06: `AdaptiveToolLoopState` must carry `extensions_used` counter.",
        )
        self.assertIn(
            "consecutive_noops",
            annotations,
            "AIB-06: `AdaptiveToolLoopState` must carry "
            "`consecutive_noops` safety counter.",
        )

    def test_loop_predicate_uses_dynamic_cap_helper(self) -> None:
        from openminion.modules.brain.loop.tools import (
            engine,
            loop_dispatch,
            loop_execution,
        )

        source = "\n".join(
            (
                inspect.getsource(engine),
                inspect.getsource(loop_dispatch),
                inspect.getsource(loop_execution),
            )
        )
        self.assertIn(
            "if loop_state.iteration >= _effective_cap(profile, loop_state):",
            source,
            "AIB remediation: the loop cap gate must read the dynamic cap "
            "via `_effective_cap(profile, loop_state)` before deciding "
            "whether to extend or terminate.",
        )
        self.assertIn(
            "extension_result = _maybe_extend_iteration_budget(",
            source,
            "AIB remediation: hitting the dynamic cap must call the "
            "production extension seam, not fall through to generic "
            "iteration-cap termination.",
        )
        self.assertIn(
            "if extension_result is True:",
            source,
            "AIB remediation: successful extension must continue the loop "
            "under the larger dynamic cap.",
        )
        # The old static predicate must NOT remain.
        self.assertNotIn(
            "while loop_state.iteration < int(profile.max_iterations):",
            source,
            "Pre-AIB predicate must be gone after AIB-06.",
        )

    def test_emission_sites_use_dynamic_cap(self) -> None:
        from openminion.modules.brain.loop.tools import (
            engine,
            loop_dispatch,
            loop_execution,
        )

        source = "\n".join(
            (
                inspect.getsource(engine),
                inspect.getsource(loop_dispatch),
                inspect.getsource(loop_execution),
            )
        )
        # No emission site passes the static profile cap after AIB-06.
        self.assertNotIn(
            "llm_call_limit=profile.max_iterations",
            source,
            "AIB-06: no `_set_turn_progress` site should pass the "
            "static `profile.max_iterations`. All must go through "
            "`_effective_cap(profile, loop_state)`.",
        )
        # Exactly 6 dynamic-cap passes (matches pre-AIB count).
        dynamic_count = source.count(
            "llm_call_limit=_effective_cap(profile, loop_state)"
        ) + source.count("llm_call_limit=effective_cap(profile, loop_state)")
        self.assertGreaterEqual(
            dynamic_count,
            6,
            f"Expected ≥6 dynamic-cap `_set_turn_progress` sites; "
            f"got {dynamic_count}. AIB-03 PPL audit counted 6 "
            f"`llm_call_limit=` calls across the adaptive loop owners.",
        )


# Claim (d) — coding profile uses CODING_MAX_ITERATIONS


class CodingProfileIterationCapTests(unittest.TestCase):
    def test_coding_handler_references_coding_max_iterations(self) -> None:
        from openminion.modules.brain.loop.strategies.coding import handler

        source = inspect.getsource(handler)
        self.assertIn(
            "CODING_MAX_ITERATIONS",
            source,
            "Coding handler must reference the module-level "
            "`CODING_MAX_ITERATIONS` constant. AIB-02 only bumps the "
            "constant's value, not the reference pattern.",
        )

    def test_coding_handler_references_coding_max_self_corrections(
        self,
    ) -> None:
        from openminion.modules.brain.loop.strategies.coding import handler

        source = inspect.getsource(handler)
        self.assertIn("CODING_MAX_SELF_CORRECTIONS", source)
