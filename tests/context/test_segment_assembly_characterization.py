from __future__ import annotations

import inspect
import unittest

import openminion.modules.context.segment as segment_assembly
from openminion.modules.context.segment import (
    LayoutDisciplineError,
    apply_trim_ladder,
    assemble_segments,
    assert_layout_discipline,
    make_segment,
    map_turn_role,
    normalize_mode_name,
    protected_decide_recent_turn_indexes,
    render_context_drop_visibility_note,
    segments_to_messages,
)


EXPECTED_PUBLIC_NAMES: tuple[str, ...] = (
    "assemble_segments",
    "make_segment",
    "segments_to_messages",
    "apply_trim_ladder",
    "position_aware_v1",
    "LayoutDisciplineError",
    "assert_layout_discipline",
    "render_context_drop_visibility_note",
    "inject_context_drop_visibility_note",
    "map_turn_role",
    "protected_decide_recent_turn_indexes",
    "normalize_mode_name",
)


EXPECTED_PRIVATE_NAMES: tuple[str, ...] = (
    "_render_trailer_feedback",
    "_rank_decision_memory_cards",
    "_rank_improvement_note_cards",
    "_rank_strategy_outcome_cards",
    "_render_decision_memory_cards",
    "_render_improvement_note_cards",
    "_render_strategy_outcome_cards",
)


class SegmentAssemblyPublicSurfaceTests(unittest.TestCase):
    def test_every_expected_public_name_resolves(self) -> None:
        for name in EXPECTED_PUBLIC_NAMES:
            with self.subTest(symbol=name):
                self.assertTrue(
                    hasattr(segment_assembly, name),
                    f"segment_assembly lost public symbol `{name}`.",
                )

    def test_every_expected_private_name_resolves(self) -> None:
        for name in EXPECTED_PRIVATE_NAMES:
            with self.subTest(symbol=name):
                self.assertTrue(
                    hasattr(segment_assembly, name),
                    f"segment_assembly lost private-but-imported symbol `{name}`.",
                )

    def test_layout_discipline_error_is_runtime_error_subclass(self) -> None:
        self.assertTrue(issubclass(LayoutDisciplineError, RuntimeError))


class SegmentAssemblySignatureTests(unittest.TestCase):
    def test_assemble_segments_returns_a_callable(self) -> None:
        self.assertTrue(callable(assemble_segments))

    def test_make_segment_signature_pinned(self) -> None:
        sig = inspect.signature(make_segment)
        params = list(sig.parameters.values())
        self.assertEqual(params[0].name, "seg_id")
        self.assertEqual(params[1].name, "bucket")
        self.assertEqual(params[2].name, "content")
        self.assertIn("estimate_tokens", sig.parameters)

    def test_apply_trim_ladder_is_callable(self) -> None:
        self.assertTrue(callable(apply_trim_ladder))

    def test_segments_to_messages_signature_pinned(self) -> None:
        sig = inspect.signature(segments_to_messages)
        params = list(sig.parameters.values())
        self.assertEqual(params[0].name, "segments")


class SegmentAssemblyBehaviorPinsTests(unittest.TestCase):
    def test_map_turn_role_user(self) -> None:
        self.assertEqual(map_turn_role("user"), "user")

    def test_map_turn_role_assistant(self) -> None:
        self.assertEqual(map_turn_role("assistant"), "assistant")

    def test_map_turn_role_inbound_alias(self) -> None:
        self.assertEqual(map_turn_role("inbound"), "user")

    def test_map_turn_role_outbound_alias(self) -> None:
        self.assertEqual(map_turn_role("outbound"), "assistant")

    def test_map_turn_role_unknown_falls_back_to_user(self) -> None:
        self.assertEqual(map_turn_role("garbage"), "user")

    def test_normalize_mode_name_known(self) -> None:
        self.assertEqual(normalize_mode_name("respond"), "respond")
        self.assertEqual(normalize_mode_name("act"), "act")
        self.assertEqual(normalize_mode_name("plan"), "plan")

    def test_normalize_mode_name_unknown_returns_none(self) -> None:
        self.assertIsNone(normalize_mode_name("garbage"))

    def test_normalize_mode_name_none_input(self) -> None:
        self.assertIsNone(normalize_mode_name(None))

    def test_render_context_drop_visibility_note_empty(self) -> None:
        out = render_context_drop_visibility_note({})
        self.assertIsInstance(out, str)

    def test_assert_layout_discipline_accepts_empty(self) -> None:
        assert_layout_discipline([])

    def test_protected_decide_recent_turn_indexes_non_decide_returns_empty(
        self,
    ) -> None:
        result = protected_decide_recent_turn_indexes([], purpose="not-decide")
        self.assertEqual(result, set())
