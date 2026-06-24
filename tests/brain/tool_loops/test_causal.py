from __future__ import annotations

from openminion.modules.brain.loop.tools import classify_batch
from openminion.modules.llm.schemas import ToolCall


def test_classify_batch_keeps_two_reads_independent() -> None:
    batch = classify_batch(
        [
            ToolCall(id="tc-1", name="file.read", arguments={"path": "/src/a.py"}),
            ToolCall(id="tc-2", name="file.read", arguments={"path": "/src/b.py"}),
        ]
    )

    assert batch.groups == ((0, 1),)
    assert batch.ordered_pairs == ()


def test_classify_batch_orders_write_then_read_same_path() -> None:
    batch = classify_batch(
        [
            ToolCall(
                id="tc-1",
                name="file.write",
                arguments={"path": "/src/a.py", "content": "updated"},
            ),
            ToolCall(id="tc-2", name="file.read", arguments={"path": "/src/a.py"}),
        ]
    )

    assert batch.groups == ((0,), (1,))
    assert batch.ordered_pairs == ((0, 1),)


def test_classify_batch_orders_two_writes_to_same_path() -> None:
    batch = classify_batch(
        [
            ToolCall(
                id="tc-1",
                name="file.write",
                arguments={"path": "/src/a.py", "content": "left"},
            ),
            ToolCall(
                id="tc-2",
                name="file.write",
                arguments={"path": "/src/a.py", "content": "right"},
            ),
        ]
    )

    assert batch.groups == ((0,), (1,))
    assert batch.ordered_pairs == ((0, 1),)


def test_classify_batch_mixed_dependencies_keep_only_independent_reads_parallel() -> (
    None
):
    batch = classify_batch(
        [
            ToolCall(id="tc-1", name="file.read", arguments={"path": "/src/a.py"}),
            ToolCall(id="tc-2", name="file.read", arguments={"path": "/src/b.py"}),
            ToolCall(
                id="tc-3",
                name="file.write",
                arguments={"path": "/src/a.py", "content": "patched"},
            ),
            ToolCall(id="tc-4", name="file.read", arguments={"path": "/src/a.py"}),
        ]
    )

    assert batch.groups == ((0, 1), (2,), (3,))
    assert batch.ordered_pairs == ((0, 2), (2, 3))


def test_classify_batch_defaults_unknown_path_pair_to_ordered() -> None:
    batch = classify_batch(
        [
            ToolCall(id="tc-1", name="file.read", arguments={}),
            ToolCall(id="tc-2", name="file.read", arguments={"path": "/src/a.py"}),
        ]
    )

    assert batch.groups == ((0,), (1,))
    assert batch.ordered_pairs == ((0, 1),)
