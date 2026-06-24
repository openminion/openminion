from __future__ import annotations

from openminion.services.agent.execution.failure import (
    AR09_VOCABULARY_VALUE,
    RepeatedFailureTracker,
    build_repeated_failure_metadata,
)


def test_vocabulary_value_matches_ar12_constant() -> None:
    assert AR09_VOCABULARY_VALUE == "repeated_failure_stalled"


def test_single_triple_count_rises() -> None:
    tracker = RepeatedFailureTracker()
    assert (
        tracker.record(
            tool_name="file.read", args_signature="{path:'a'}", error_code="ENOENT"
        )
        == 1
    )
    assert (
        tracker.record(
            tool_name="file.read", args_signature="{path:'a'}", error_code="ENOENT"
        )
        == 2
    )
    assert (
        tracker.record(
            tool_name="file.read", args_signature="{path:'a'}", error_code="ENOENT"
        )
        == 3
    )


def test_distinct_triples_count_independently() -> None:
    tracker = RepeatedFailureTracker()
    tracker.record(tool_name="file.read", args_signature="a", error_code="X")
    tracker.record(tool_name="file.read", args_signature="b", error_code="X")
    tracker.record(tool_name="file.write", args_signature="a", error_code="X")
    tracker.record(tool_name="file.read", args_signature="a", error_code="Y")
    assert tracker.count(tool_name="file.read", args_signature="a", error_code="X") == 1
    assert tracker.count(tool_name="file.read", args_signature="b", error_code="X") == 1
    assert (
        tracker.count(tool_name="file.write", args_signature="a", error_code="X") == 1
    )
    assert tracker.count(tool_name="file.read", args_signature="a", error_code="Y") == 1


def test_not_stalled_below_threshold() -> None:
    tracker = RepeatedFailureTracker(threshold=3)
    tracker.record(tool_name="file.read", args_signature="a", error_code="X")
    tracker.record(tool_name="file.read", args_signature="a", error_code="X")
    assert tracker.is_stalled() is False


def test_stalled_at_threshold() -> None:
    tracker = RepeatedFailureTracker(threshold=3)
    for _ in range(3):
        tracker.record(tool_name="file.read", args_signature="a", error_code="X")
    assert tracker.is_stalled() is True


def test_stalled_triple_returns_offending_key() -> None:
    tracker = RepeatedFailureTracker(threshold=3)
    for _ in range(3):
        tracker.record(
            tool_name="exec.run", args_signature="{cmd:'pytest'}", error_code="EXIT_1"
        )
    triple = tracker.stalled_triple()
    assert triple == ("exec.run", "{cmd:'pytest'}", "EXIT_1")


def test_threshold_is_configurable() -> None:
    tracker = RepeatedFailureTracker(threshold=5)
    for _ in range(4):
        tracker.record(tool_name="x", args_signature="y", error_code="z")
    assert tracker.is_stalled() is False
    tracker.record(tool_name="x", args_signature="y", error_code="z")
    assert tracker.is_stalled() is True


def test_metadata_uses_ar12_vocabulary_value() -> None:
    tracker = RepeatedFailureTracker(threshold=3)
    for _ in range(3):
        tracker.record(
            tool_name="git.push",
            args_signature="--force",
            error_code="GIT_DESTRUCTIVE_NOT_APPROVED",
        )
    metadata = build_repeated_failure_metadata(tracker)
    assert metadata["tool_loop_termination_reason"] == AR09_VOCABULARY_VALUE
    assert metadata["stalled_tool_name"] == "git.push"
    assert metadata["stalled_args_signature"] == "--force"
    assert metadata["stalled_error_code"] == "GIT_DESTRUCTIVE_NOT_APPROVED"
    assert metadata["threshold"] == "3"


def test_metadata_when_no_triple_stalled_omits_triple_keys() -> None:
    tracker = RepeatedFailureTracker(threshold=3)
    tracker.record(tool_name="x", args_signature="y", error_code="z")
    metadata = build_repeated_failure_metadata(tracker)
    assert metadata["tool_loop_termination_reason"] == AR09_VOCABULARY_VALUE
    # Triple keys absent because nothing has stalled yet
    assert "stalled_tool_name" not in metadata


def test_snapshot_is_deterministic() -> None:
    tracker = RepeatedFailureTracker()
    tracker.record(tool_name="b", args_signature="x", error_code="X")
    tracker.record(tool_name="a", args_signature="x", error_code="X")
    snap = tracker.snapshot()
    keys = list(snap.keys())
    # `dict` preserves insertion order but the snapshot is built via
    # sorted(items()) so it is alpha-sorted.
    assert keys == sorted(keys)


def test_whitespace_in_triple_is_stripped() -> None:
    tracker = RepeatedFailureTracker(threshold=2)
    tracker.record(tool_name="file.read", args_signature="a", error_code="X")
    tracker.record(tool_name="  file.read  ", args_signature="a", error_code="X")
    assert tracker.is_stalled() is True
