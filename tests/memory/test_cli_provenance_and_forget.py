from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from typer.testing import CliRunner

from openminion.modules.memory.cli import _build_app
from openminion.modules.memory.contracts.provenance import (
    MemoryProvenanceEntry,
    TurnProvenanceTrace,
)
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.runtime.provenance import (
    MemoryProvenanceRecorder,
    default_provenance_recorder,
    set_default_provenance_recorder,
)
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


class _RecorderCase(unittest.TestCase):
    def setUp(self) -> None:
        self._original_recorder = default_provenance_recorder()
        self._recorder = MemoryProvenanceRecorder()
        set_default_provenance_recorder(self._recorder)

    def tearDown(self) -> None:
        set_default_provenance_recorder(self._original_recorder)

    def _seed_trace(self, session_id: str, turn_id: str) -> TurnProvenanceTrace:
        trace = TurnProvenanceTrace(
            session_id=session_id,
            turn_id=turn_id,
            recorded_at="2026-05-18T00:00:00Z",
            entries=(
                MemoryProvenanceEntry(
                    memory_id="m1",
                    source="tool_output",
                    written_at="2026-05-18T00:00:00Z",
                    retrieval_score=0.9,
                ),
            ),
            retrieval_cutoff=0.3,
            query="who is the user",
        )
        self._recorder.record_turn_trace(trace)
        return trace


class TestMemoryProvenanceCLI(_RecorderCase):
    def setUp(self) -> None:
        super().setUp()
        self.runner = CliRunner()
        self.app = _build_app()

    def _invoke_provenance(self, *args: str):
        return self.runner.invoke(self.app, ["provenance", *args])

    def test_provenance_by_session_and_turn(self) -> None:
        self._seed_trace("s1", "t1")
        result = self._invoke_provenance("--session", "s1", "--turn", "t1", "--json")
        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.output)
        self.assertEqual(payload["session_id"], "s1")
        self.assertEqual(payload["turn_id"], "t1")
        self.assertEqual(len(payload["entries"]), 1)
        self.assertEqual(payload["entries"][0]["memory_id"], "m1")

    def test_provenance_by_memory(self) -> None:
        self._seed_trace("s1", "t1")
        result = self._invoke_provenance("--memory", "m1", "--json")
        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.output)
        self.assertEqual(payload["memory_id"], "m1")
        self.assertEqual(payload["trace_count"], 1)

    def test_provenance_missing_args_fails(self) -> None:
        result = self._invoke_provenance()
        self.assertNotEqual(result.exit_code, 0)

    def test_provenance_mixing_modes_fails(self) -> None:
        result = self._invoke_provenance(
            "--memory",
            "m1",
            "--session",
            "s1",
            "--turn",
            "t1",
        )
        self.assertNotEqual(result.exit_code, 0)

    def test_provenance_unknown_session_turn_returns_error(self) -> None:
        result = self._invoke_provenance("--session", "nope", "--turn", "nope")
        self.assertNotEqual(result.exit_code, 0)


class TestMemoryForgetCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self.app = _build_app()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        store = SQLiteMemoryStore(Path(self.db_path))
        self.r1 = MemoryRecord(
            id="r1",
            scope="agent:test",
            type="fact",
            content={"text": "fact one"},
            created_at="2026-05-18T00:00:00Z",
            updated_at="2026-05-18T00:00:00Z",
            source="tool_output",
        )
        self.r2 = MemoryRecord(
            id="r2",
            scope="agent:test",
            type="fact",
            content={"text": "fact two"},
            created_at="2026-05-18T00:00:00Z",
            updated_at="2026-05-18T00:00:00Z",
            source="tool_output",
        )
        self.r3 = MemoryRecord(
            id="r3",
            scope="agent:test",
            type="fact",
            content={"text": "fact three"},
            created_at="2026-05-18T00:00:00Z",
            updated_at="2026-05-18T00:00:00Z",
            source="user_said",
        )
        store.put(self.r1)
        store.put(self.r2)
        store.put(self.r3)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _invoke_forget(self, *args: str):
        return self.runner.invoke(self.app, ["forget", *args])

    def test_forget_requires_reason(self) -> None:
        result = self._invoke_forget("--memory", "r1", "--db", self.db_path)
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("reason", result.output.lower())

    def test_forget_requires_memory_or_source(self) -> None:
        result = self._invoke_forget("--reason", "audit", "--db", self.db_path)
        self.assertNotEqual(result.exit_code, 0)

    def test_forget_rejects_both_memory_and_source(self) -> None:
        result = self._invoke_forget(
            "--memory",
            "r1",
            "--source",
            "tool_output",
            "--reason",
            "audit",
            "--db",
            self.db_path,
        )
        self.assertNotEqual(result.exit_code, 0)

    def test_forget_single_memory(self) -> None:
        result = self._invoke_forget(
            "--memory",
            "r1",
            "--reason",
            "operator audit",
            "--db",
            self.db_path,
            "--json",
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.output)
        self.assertEqual(payload["mode"], "memory")
        self.assertEqual(payload["memory_id"], "r1")
        self.assertTrue(payload["deleted"])
        self.assertEqual(payload["reason"], "operator audit")

    def test_forget_by_source_dry_run_default(self) -> None:
        result = self._invoke_forget(
            "--source",
            "tool_output",
            "--reason",
            "audit",
            "--db",
            self.db_path,
            "--json",
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.output)
        self.assertEqual(payload["mode"], "source")
        self.assertEqual(payload["source"], "tool_output")
        self.assertFalse(payload["applied"])
        self.assertEqual(set(payload["matched_ids"]), {"r1", "r2"})

    def test_forget_by_source_apply_mutates(self) -> None:
        result = self._invoke_forget(
            "--source",
            "tool_output",
            "--reason",
            "operator audit",
            "--apply",
            "--db",
            self.db_path,
            "--json",
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.output)
        self.assertTrue(payload["applied"])
        self.assertEqual(set(payload["matched_ids"]), {"r1", "r2"})

    def test_audit_fields_persist_on_real_sqlite_store(self) -> None:
        result = self._invoke_forget(
            "--memory",
            "r1",
            "--reason",
            "MPF-05 persistence test",
            "--db",
            self.db_path,
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)

        store = SQLiteMemoryStore(Path(self.db_path))
        re_read = store.get("r1")
        self.assertIsNotNone(re_read)
        self.assertTrue(re_read.is_deleted)
        self.assertEqual(re_read.deleted_reason, "MPF-05 persistence test")
        self.assertIsNotNone(re_read.deleted_at)
        self.assertIn("T", re_read.deleted_at)

    def test_audit_fields_persist_on_batch_forget(self) -> None:
        result = self._invoke_forget(
            "--source",
            "tool_output",
            "--reason",
            "batch audit cleanup",
            "--apply",
            "--db",
            self.db_path,
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)

        store = SQLiteMemoryStore(Path(self.db_path))
        for record_id in ("r1", "r2"):
            re_read = store.get(record_id)
            self.assertIsNotNone(re_read)
            self.assertTrue(re_read.is_deleted)
            self.assertEqual(re_read.deleted_reason, "batch audit cleanup")
            self.assertIsNotNone(re_read.deleted_at)
        r3 = store.get("r3")
        self.assertIsNotNone(r3)
        self.assertFalse(r3.is_deleted)
        self.assertIsNone(r3.deleted_at)
        self.assertIsNone(r3.deleted_reason)

    def test_audit_fields_are_null_when_legacy_delete_no_reason(self) -> None:
        store = SQLiteMemoryStore(Path(self.db_path))
        store.delete("r1")  # no reason kwarg

        re_read = store.get("r1")
        self.assertIsNotNone(re_read)
        self.assertTrue(re_read.is_deleted)
        self.assertIsNone(re_read.deleted_at)
        self.assertIsNone(re_read.deleted_reason)

    def test_forget_by_source_apply_idempotent(self) -> None:
        first = self._invoke_forget(
            "--source",
            "tool_output",
            "--reason",
            "first audit",
            "--apply",
            "--db",
            self.db_path,
            "--json",
        )
        self.assertEqual(first.exit_code, 0)
        self.assertEqual(set(json.loads(first.output)["matched_ids"]), {"r1", "r2"})

        second = self._invoke_forget(
            "--source",
            "tool_output",
            "--reason",
            "second audit",
            "--apply",
            "--db",
            self.db_path,
            "--json",
        )
        self.assertEqual(second.exit_code, 0)
        self.assertEqual(json.loads(second.output)["matched_ids"], [])

    def test_forget_by_source_pages_through_large_scope(self) -> None:
        from openminion.modules.memory.service import MemoryService

        page_size = MemoryService._FORGET_PAGE_SIZE
        target_count = page_size + 5  # one full page + a partial follow-up

        store = SQLiteMemoryStore(Path(self.db_path))
        for i in range(target_count):
            store.put(
                MemoryRecord(
                    id=f"bulk-{i}",
                    scope="agent:bulk",
                    type="fact",
                    content={"text": f"bulk fact {i}"},
                    created_at="2026-05-18T00:00:00Z",
                    updated_at="2026-05-18T00:00:00Z",
                    source="tool_output",
                )
            )

        result = self._invoke_forget(
            "--source",
            "tool_output",
            "--reason",
            "bulk audit",
            "--apply",
            "--db",
            self.db_path,
            "--json",
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.output)
        matched = set(payload["matched_ids"])
        expected = {"r1", "r2"} | {f"bulk-{i}" for i in range(target_count)}
        self.assertEqual(matched, expected)

    def test_forget_emits_delete_event_to_audit_sink(self) -> None:
        from openminion.modules.memory.storage.audit import (
            SQLiteMemoryAuditSink,
            default_memory_audit_db_path,
        )

        result = self._invoke_forget(
            "--memory",
            "r1",
            "--reason",
            "MPF-06 audit-surface assertion",
            "--db",
            self.db_path,
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)

        audit_db_path = default_memory_audit_db_path(self.db_path)
        sink = SQLiteMemoryAuditSink(audit_db_path)
        events = sink.list_events()
        delete_events = [
            ev
            for ev in events
            if ev["event_type"] == "memory.record.delete" and ev["target_id"] == "r1"
        ]
        self.assertEqual(
            len(delete_events),
            1,
            msg=f"expected exactly one delete event for r1; got {delete_events!r}",
        )
        details = delete_events[0]["details"]
        self.assertEqual(details["reason"], "MPF-06 audit-surface assertion")
        self.assertIn("deleted_at", details)
        self.assertIn("T", details["deleted_at"])


class TestMemoryProvenanceRouteViaDispatcher(_RecorderCase):
    def test_dispatch_returns_trace(self) -> None:
        from openminion.api.server.app import dispatch_request

        self._seed_trace("s1", "t1")
        status, payload = dispatch_request(
            "GET",
            "/memory/provenance",
            None,  # config_path
            query="session_id=s1&turn_id=t1",
            runtime=None,
            runtime_bootstrap_error=None,
            request_headers={},
            request_id="test-req",
        )
        self.assertEqual(int(status), 200, msg=payload)
        self.assertEqual(payload["session_id"], "s1")
        self.assertEqual(len(payload["entries"]), 1)

    def test_dispatch_returns_404_when_no_trace(self) -> None:
        from openminion.api.server.app import dispatch_request

        status, payload = dispatch_request(
            "GET",
            "/memory/provenance",
            None,
            query="session_id=unknown&turn_id=unknown",
            runtime=None,
            runtime_bootstrap_error=None,
            request_headers={},
            request_id="test-req",
        )
        self.assertEqual(int(status), 404)
