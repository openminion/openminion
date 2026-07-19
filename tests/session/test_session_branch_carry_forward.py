from __future__ import annotations

import pytest

from openminion.modules.session.fork_restore.branch import (
    SessionBranchCarryForwardDeniedError,
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
