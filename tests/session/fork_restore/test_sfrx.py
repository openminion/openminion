from __future__ import annotations

import os
import sqlite3
import tempfile

from openminion.modules.session.fork_restore import (
    SessionForkAPI,
    dispatch_restore_command,
    dispatch_session_fork_command,
)
from openminion.modules.session.fork_restore.fork import SessionForkRecord
from openminion.modules.session.fork_restore.restore import (
    build_file_checkpoint,
    restore_file_checkpoint,
)
from openminion.modules.session.fork_restore.telemetry import (
    stamp_file_restore,
    stamp_session_fork,
)


class _FakeSnapshotCreator:
    def __init__(self):
        self.calls = []

    def create_snapshot(self, session_id: str, seq_upto: int | None = None) -> str:
        self.calls.append((session_id, seq_upto))
        return f"snap-{len(self.calls)}"


class _Logger:
    def __init__(self):
        self.events = []

    def log_canonical_event(self, *, event_type, payload):
        self.events.append((event_type, payload))


def _make_api():
    conn = sqlite3.connect(":memory:")
    creator = _FakeSnapshotCreator()
    api = SessionForkAPI(snapshot_creator=creator, conn=conn)
    return api, creator


# --- SFRX-01 fork API ---


def test_fork_creates_snapshot_and_returns_typed_record():
    api, creator = _make_api()
    record = api.fork("sess-parent", new_name="exploration")
    assert isinstance(record, SessionForkRecord)
    assert record.parent_session_id == "sess-parent"
    assert record.name == "exploration"
    assert record.snapshot_id == "snap-1"
    assert record.decision_action == "fork_thread"
    assert creator.calls == [("sess-parent", None)]


def test_fork_passes_seq_upto_to_snapshot_creator():
    api, creator = _make_api()
    api.fork("p", seq_upto=42)
    assert creator.calls[-1] == ("p", 42)


def test_fork_persists_record_queryable_via_list_forks_of():
    api, _ = _make_api()
    api.fork("parent", new_name="a")
    api.fork("parent", new_name="b")
    forks = api.list_forks_of("parent")
    assert [f.name for f in forks] == ["a", "b"]


def test_lookup_fork_returns_record_for_new_session_id():
    api, _ = _make_api()
    record = api.fork("p")
    found = api.lookup_fork(record.new_session_id)
    assert found is not None
    assert found.fork_id == record.fork_id


# --- SFRX-02 fork CLI ---


def test_dispatch_session_fork_command_requires_parent():
    api, _ = _make_api()
    result = dispatch_session_fork_command(api, [])
    assert result["ok"] is False
    assert "usage" in result


def test_dispatch_session_fork_command_returns_fork_dict():
    api, _ = _make_api()
    result = dispatch_session_fork_command(api, ["parent", "feature-x"])
    assert result["ok"] is True
    assert result["fork"]["parent_session_id"] == "parent"
    assert result["fork"]["name"] == "feature-x"
    assert result["fork"]["snapshot_id"] == "snap-1"


# --- SFRX-03 file-restore primitive ---


def test_restore_file_checkpoint_writes_files_to_disk():
    with tempfile.TemporaryDirectory() as root:
        checkpoint = build_file_checkpoint(
            checkpoint_id="cp1",
            files={"a.txt": "alpha", "sub/b.txt": "beta"},
        )
        result = restore_file_checkpoint(checkpoint, root=root)
        assert set(result.restored_paths) == {"a.txt", "sub/b.txt"}
        assert result.missing_paths == ()
        with open(os.path.join(root, "a.txt")) as f:
            assert f.read() == "alpha"
        with open(os.path.join(root, "sub", "b.txt")) as f:
            assert f.read() == "beta"


def test_restore_file_checkpoint_creates_intermediate_dirs():
    with tempfile.TemporaryDirectory() as root:
        cp = build_file_checkpoint(
            checkpoint_id="cp",
            files={"deep/nested/path/c.txt": "gamma"},
        )
        result = restore_file_checkpoint(cp, root=root)
        assert "deep/nested/path/c.txt" in result.restored_paths


# --- SFRX-04 restore CLI ---


def test_dispatch_restore_command_with_none_checkpoint():
    result = dispatch_restore_command(None)
    assert result["ok"] is False
    assert result["error"] == "no_checkpoint"


def test_dispatch_restore_command_returns_result_dict():
    with tempfile.TemporaryDirectory() as root:
        cp = build_file_checkpoint(checkpoint_id="cp", files={"x.txt": "x"})
        result = dispatch_restore_command(cp, root=root)
        assert result["ok"] is True
        assert "x.txt" in result["restored_paths"]
        assert result["checkpoint_id"] == "cp"


# --- SFRX-05 telemetry ---


def test_stamp_session_fork_emits_canonical_event():
    api, _ = _make_api()
    record = api.fork("parent")
    logger = _Logger()
    stamp_session_fork(logger, record)
    assert len(logger.events) == 1
    assert logger.events[0][0] == "sfrx_session_fork"
    assert logger.events[0][1]["parent_session_id"] == "parent"


def test_stamp_file_restore_emits_canonical_event():
    with tempfile.TemporaryDirectory() as root:
        cp = build_file_checkpoint(checkpoint_id="cp", files={"x.txt": "x"})
        result = restore_file_checkpoint(cp, root=root)
        logger = _Logger()
        stamp_file_restore(logger, result)
        assert logger.events[0][0] == "sfrx_file_restore"


def test_stamp_helpers_swallow_logger_failures():
    class _Bad:
        def log_canonical_event(self, *, event_type, payload):
            raise RuntimeError("boom")

    api, _ = _make_api()
    record = api.fork("parent")
    stamp_session_fork(_Bad(), record)  # no raise


def test_stamp_helpers_safe_with_none_logger():
    api, _ = _make_api()
    record = api.fork("parent")
    stamp_session_fork(None, record)  # no raise


# --- SFRX-06 E2E smoke ---


def test_e2e_smoke_fork_then_restore_via_cli():
    api, creator = _make_api()
    logger = _Logger()

    # Surface 1: fork via CLI
    fork_result = dispatch_session_fork_command(api, ["sess-orig", "explore"])
    assert fork_result["ok"] is True
    new_sess = fork_result["fork"]["new_session_id"]

    # Surface 2: fork lookup returns the typed record
    record = api.lookup_fork(new_sess)
    assert record is not None

    # Surface 3: stamp telemetry for the fork
    stamp_session_fork(logger, record)
    assert any(e[0] == "sfrx_session_fork" for e in logger.events)

    # Surface 4: edit file → checkpoint → restore reverts it
    with tempfile.TemporaryDirectory() as root:
        checkpoint = build_file_checkpoint(
            checkpoint_id="cp-pre",
            files={"f.txt": "original"},
        )
        # Apply the checkpoint to disk (simulate pre-edit state)
        restore_file_checkpoint(checkpoint, root=root)
        # Mutate the file (simulate edit)
        with open(os.path.join(root, "f.txt"), "w") as f:
            f.write("mutated")
        # Restore via CLI
        restore_result = dispatch_restore_command(checkpoint, root=root)
        assert restore_result["ok"] is True
        stamp_file_restore(logger, restore_file_checkpoint(checkpoint, root=root))
        with open(os.path.join(root, "f.txt")) as f:
            assert f.read() == "original"

    # Surface 5: at least one of each event type fired
    event_types = {e[0] for e in logger.events}
    assert "sfrx_session_fork" in event_types
    assert "sfrx_file_restore" in event_types
