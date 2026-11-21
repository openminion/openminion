from __future__ import annotations

from typing import Any

import pytest

from openminion.modules.session.fork_restore.branch import (
    SessionBranchCarryForwardDeniedError,
    SessionBranchError,
    carry_forward_branch_fields,
    diff_session_branches,
)
from openminion.modules.session.storage import SQLiteSessionStore


def test_branch_diff_is_structural_without_prose_inference() -> None:
    store = SQLiteSessionStore(":memory:")
    left = store.create_session(session_id="left")
    right = store.create_session(session_id="right")
    store.update_summary(left, "left summary", based_on_seq=1)
    store.update_summary(right, "right summary", based_on_seq=1)
    store.put_working_state(left, state_inline={"task": "a"})
    store.put_working_state(right, state_inline={"task": "b"})

    diff = diff_session_branches(store, left_session_id=left, right_session_id=right)

    statuses = {item.field: item.status for item in diff.items}
    assert statuses["summary"] == "changed"
    assert statuses["working_state"] == "changed"
    assert all("conflict" not in item.status for item in diff.items)


def test_carry_forward_allowlist_creates_child_without_mutating_sources() -> None:
    store = SQLiteSessionStore(":memory:")
    source = store.create_session(session_id="source")
    target = store.create_session(session_id="target")
    store.update_summary(source, "source summary", based_on_seq=1)
    store.put_working_state(source, state_inline={"task": "keep"})
    store.add_message_ref(source, "assistant", content_ref="blob://message-1")

    result = carry_forward_branch_fields(
        store,
        source_session_id=source,
        target_parent_session_id=target,
        fields=["summary", "working_state", "message_refs"],
    )
    child = result["child_session_id"]

    assert store.get_summary(child) == "source summary"
    assert store.get_latest_working_state(child)["state_inline"] == {"task": "keep"}
    rows = store._record_store.query_rows("message_refs", where={"session_id": child})
    assert rows[0]["content_ref"] == "blob://message-1"
    assert rows[0]["content_inline"] is None
    assert store.get_summary(source) == "source summary"
    assert store.get_session(target) is not None


def test_carry_forward_rejects_unknown_secret_and_raw_transcript_fields() -> None:
    store = SQLiteSessionStore(":memory:")
    source = store.create_session(session_id="source")
    target = store.create_session(session_id="target")

    for fields in (["raw_transcript"], ["secret"], ["unknown"]):
        with pytest.raises(SessionBranchCarryForwardDeniedError):
            carry_forward_branch_fields(
                store,
                source_session_id=source,
                target_parent_session_id=target,
                fields=list(fields),
            )


def test_branch_diff_handles_empty_added_and_identical_structural_content() -> None:
    store = SQLiteSessionStore(":memory:")
    left = store.create_session(session_id="left")
    right = store.create_session(session_id="right")

    empty_diff = diff_session_branches(
        store, left_session_id=left, right_session_id=right
    )
    assert [
        (item.field, item.status, item.left_ref, item.right_ref)
        for item in empty_diff.items
    ] == [
        ("summary", "same", "empty", "empty"),
        ("working_state", "same", "empty", "empty"),
        ("message_refs", "same", "empty", "empty"),
    ]

    store.update_summary(
        left, "short summary", summary_long="long summary a", based_on_seq=4
    )
    store.set_summary_base(left, "blob://base-1")
    store.append_summary_delta(left, "blob://delta-1")
    store.put_working_state(left, state_inline={"step": 1})
    store.add_message_ref(left, "assistant", content_ref="blob://msg-1")

    one_sided = diff_session_branches(
        store, left_session_id=left, right_session_id=right
    )
    assert [(item.field, item.status, item.right_ref) for item in one_sided.items] == [
        ("summary", "added_or_removed", "empty"),
        ("working_state", "added_or_removed", "empty"),
        ("message_refs", "added_or_removed", "empty"),
    ]

    carried = carry_forward_branch_fields(
        store,
        source_session_id=left,
        target_parent_session_id=right,
        fields=("summary", "working_state", "message_refs"),
    )
    child = carried["child_session_id"]
    carried_diff = diff_session_branches(
        store, left_session_id=left, right_session_id=child
    )
    assert [(item.field, item.status) for item in carried_diff.items] == [
        ("summary", "same"),
        ("working_state", "same"),
        ("message_refs", "same"),
    ]
    child_summaries = store.get_summaries(child)
    assert child_summaries["base_ref"] == "blob://base-1"
    assert child_summaries["delta_refs"] == ["blob://delta-1"]

    store.update_summary(
        child, "short summary", summary_long="long summary b", based_on_seq=0
    )
    long_diff = diff_session_branches(
        store, left_session_id=left, right_session_id=child
    )
    assert {item.field: item.status for item in long_diff.items}["summary"] == "changed"


def test_carry_forward_preserves_child_local_summary_refresh_and_isolation() -> None:
    store = SQLiteSessionStore(":memory:")
    source = store.create_session(session_id="source")
    target = store.create_session(session_id="target")
    for idx in range(100):
        store.append_event(
            source,
            event_type="turn.note",
            payload={"idx": idx},
            actor_type="user",
        )
    store.update_summary(source, "condensed 100 events", based_on_seq=100)

    child = carry_forward_branch_fields(
        store,
        source_session_id=source,
        target_parent_session_id=target,
        fields=["summary"],
    )["child_session_id"]

    diff = diff_session_branches(store, left_session_id=source, right_session_id=child)
    assert [(item.field, item.status) for item in diff.items] == [
        ("summary", "same"),
        ("working_state", "same"),
        ("message_refs", "same"),
    ]
    assert store.needs_summary_update(child, threshold_events=40) is False
    for idx in range(40):
        store.append_event(
            child,
            event_type="turn.note",
            payload={"child_idx": idx},
            actor_type="user",
        )
    assert store.needs_summary_update(child, threshold_events=40) is True
    assert store.needs_summary_update(source, threshold_events=40) is False
    assert store.get_summary(target) == ""


def test_carry_forward_preserves_ref_only_working_state_and_base_only_summaries() -> (
    None
):
    store = SQLiteSessionStore(":memory:")
    source = store.create_session(session_id="source")
    target = store.create_session(session_id="target")
    store.set_summary_base(source, "blob://base-only")
    store.append_summary_delta(source, "blob://delta-only")
    store.put_working_state(source, state_ref="blob://ws-ref")

    child = carry_forward_branch_fields(
        store,
        source_session_id=source,
        target_parent_session_id=target,
        fields=["summary", "working_state"],
    )["child_session_id"]

    ws = store.get_latest_working_state(child)
    assert ws is not None
    assert ws["state_ref"] == "blob://ws-ref"
    assert ws["state_inline"] is None
    diff = diff_session_branches(store, left_session_id=source, right_session_id=child)
    assert [(item.field, item.status) for item in diff.items] == [
        ("summary", "same"),
        ("working_state", "same"),
        ("message_refs", "same"),
    ]


def test_branch_ops_validate_sessions_and_sequence_allowlist_before_creating_child() -> (
    None
):
    store = SQLiteSessionStore(":memory:")
    source = store.create_session(session_id="source")
    target = store.create_session(session_id="target")

    with pytest.raises(SessionBranchError, match="session not found"):
        diff_session_branches(store, left_session_id=source, right_session_id="missing")
    with pytest.raises(SessionBranchError, match="session not found"):
        carry_forward_branch_fields(
            store,
            source_session_id="missing",
            target_parent_session_id=target,
            fields=["summary"],
        )
    with pytest.raises(SessionBranchError, match="session not found"):
        carry_forward_branch_fields(
            store,
            source_session_id=source,
            target_parent_session_id="missing",
            fields=["summary"],
        )
    invalid_inputs: tuple[Any, ...] = (
        [],
        ["   "],
        "summary",
        b"summary",
        [123],
        {"summary": False},
        {"summary"},
    )
    for invalid_fields in invalid_inputs:
        with pytest.raises(SessionBranchCarryForwardDeniedError):
            carry_forward_branch_fields(
                store,
                source_session_id=source,
                target_parent_session_id=target,
                fields=invalid_fields,
            )
    assert {item["session_id"] for item in store.list_sessions()} == {source, target}
