"""Regression test for #483: memory/self_awareness must render before turn_input."""

from __future__ import annotations

import unittest

from openminion.modules.context.schemas import ContextSegment
from openminion.modules.context.segment.messages import segments_to_messages


def _segment(
    seg_id: str, bucket: str, content: str, *, role: str = "system"
) -> ContextSegment:
    return ContextSegment(
        id=seg_id,
        bucket=bucket,  # type: ignore[arg-type]
        role=role,  # type: ignore[arg-type]
        content=content,
        token_estimate=1,
    )


class SegmentBucketOrderTests(unittest.TestCase):
    def test_memory_and_self_awareness_render_before_turn_input(self) -> None:
        # Deliberately unsorted input: turn_input first to prove sorting.
        segments = [
            _segment("turn", "turn_input", "user says hi", role="user"),
            _segment("mem", "memory", "remembered fact"),
            _segment("self", "self_awareness", "self model"),
            _segment("prefix", "static_prefix", "system prompt"),
        ]
        messages = segments_to_messages(segments)
        block_order = [m.meta["block_kind"] for m in messages]
        self.assertEqual(
            block_order,
            ["static_prefix", "self_awareness", "memory", "turn_input"],
        )
        self.assertLess(
            block_order.index("self_awareness"), block_order.index("turn_input")
        )
        self.assertLess(block_order.index("memory"), block_order.index("turn_input"))
        # Final user input is the last rendered message.
        self.assertEqual(messages[-1].role, "user")
        self.assertEqual(messages[-1].meta["block_kind"], "turn_input")

    def test_stable_ordering_within_bucket(self) -> None:
        segments = [
            _segment("self-1", "self_awareness", "first self note"),
            _segment("self-2", "self_awareness", "second self note"),
            _segment("turn", "turn_input", "go", role="user"),
        ]
        messages = segments_to_messages(segments)
        self_awareness = next(
            m for m in messages if m.meta["block_kind"] == "self_awareness"
        )
        self.assertIn("first self note", self_awareness.content)
        self.assertIn("second self note", self_awareness.content)
        self.assertLess(
            self_awareness.content.index("first self note"),
            self_awareness.content.index("second self note"),
        )
