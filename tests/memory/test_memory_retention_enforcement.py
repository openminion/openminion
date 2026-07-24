from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path
from typing import Any

from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.runtime.retention import (
    RuntimeMemoryRetentionPolicy,
    dry_run_runtime_memory_retention,
    enforce_runtime_memory_retention,
)
from openminion.modules.memory.storage.audit import (
    AuditedMemoryStore,
    InMemoryMemoryAuditSink,
)
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.telemetry.events.catalog import MEMORY_RETENTION_ENFORCE


def _iso(base: datetime, *, days: int = 0, seconds: int = 0) -> str:
    return (base + timedelta(days=days, seconds=seconds)).isoformat()


def _record(
    record_id: str,
    *,
    scope: str,
    created_at: str,
    updated_at: str | None = None,
    meta: dict[str, Any] | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        scope=scope,
        type="fact",
        key=record_id,
        title=record_id,
        content={"text": f"content for {record_id}"},
        created_at=created_at,
        updated_at=updated_at or created_at,
        meta=dict(meta or {}),
    )


class RuntimeMemoryRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "memory.db"
        self.store = SQLiteMemoryStore(self.db_path)
        self.now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_dry_run_reports_eligible_ids_without_mutating(self) -> None:
        old = _record(
            "old-session",
            scope="session:s1",
            created_at=_iso(self.now, days=-45),
        )
        new = _record(
            "new-session",
            scope="session:s1",
            created_at=_iso(self.now, days=-2),
        )
        durable = _record(
            "durable-agent",
            scope="agent:ops",
            created_at=_iso(self.now, days=-45),
        )
        for item in (old, new, durable):
            self.store.put(item)

        report = dry_run_runtime_memory_retention(
            self.store,
            RuntimeMemoryRetentionPolicy(
                log_retention_days=30,
                patch_retention_count=20,
            ),
            now=self.now,
        )

        self.assertEqual(report.status, "dry_run")
        self.assertEqual(report.eligible_record_ids, ("old-session",))
        self.assertIsNotNone(self.store.get("old-session"))
        self.assertIsNotNone(self.store.get("new-session"))
        self.assertIsNotNone(self.store.get("durable-agent"))

    def test_enforcement_soft_deletes_before_cutoff_not_exact_or_after(self) -> None:
        before = _record(
            "before-cutoff",
            scope="session:s1",
            created_at=_iso(self.now, days=-31),
        )
        exact = _record(
            "exact-cutoff",
            scope="session:s1",
            created_at=_iso(self.now, days=-30),
        )
        after = _record(
            "after-cutoff",
            scope="session:s1",
            created_at=_iso(self.now, days=-29),
        )
        for item in (before, exact, after):
            self.store.put(item)

        report = enforce_runtime_memory_retention(
            self.store,
            RuntimeMemoryRetentionPolicy(
                log_retention_days=30,
                patch_retention_count=20,
            ),
            now=self.now,
        )

        self.assertEqual(report.status, "enforced")
        self.assertEqual(report.deleted_record_ids, ("before-cutoff",))
        visible_ids = {
            item.id for item in self.store.list(ListQueryOptions(scopes=["session:s1"]))
        }
        self.assertNotIn("before-cutoff", visible_ids)
        self.assertIn("exact-cutoff", visible_ids)
        self.assertIn("after-cutoff", visible_ids)
        deleted = self.store.get("before-cutoff")
        self.assertIsNotNone(deleted)
        assert deleted is not None
        self.assertTrue(deleted.is_deleted)
        self.assertEqual(deleted.deleted_reason, "runtime_retention_enforced")

    def test_patch_count_prunes_oldest_session_records_per_scope(self) -> None:
        for index in range(4):
            self.store.put(
                _record(
                    f"s1-{index}",
                    scope="session:s1",
                    created_at=_iso(self.now, seconds=index),
                    updated_at=_iso(self.now, seconds=index),
                )
            )
        self.store.put(
            _record(
                "s2-keep",
                scope="session:s2",
                created_at=_iso(self.now),
                updated_at=_iso(self.now),
            )
        )

        report = enforce_runtime_memory_retention(
            self.store,
            RuntimeMemoryRetentionPolicy(
                log_retention_days=365,
                patch_retention_count=2,
            ),
            now=self.now,
        )

        self.assertEqual(set(report.deleted_record_ids), {"s1-0", "s1-1"})
        s1_visible = {
            item.id for item in self.store.list(ListQueryOptions(scopes=["session:s1"]))
        }
        self.assertEqual(s1_visible, {"s1-2", "s1-3"})
        s2_visible = {
            item.id for item in self.store.list(ListQueryOptions(scopes=["session:s2"]))
        }
        self.assertEqual(s2_visible, {"s2-keep"})

    def test_structural_retention_hold_preserves_eligible_record(self) -> None:
        self.store.put(
            _record(
                "held",
                scope="session:s1",
                created_at=_iso(self.now, days=-45),
                meta={"privacy_policy": {"decision_reason": "retention_hold"}},
            )
        )
        self.store.put(
            _record(
                "not-held",
                scope="session:s1",
                created_at=_iso(self.now, days=-45),
            )
        )

        report = enforce_runtime_memory_retention(
            self.store,
            RuntimeMemoryRetentionPolicy(
                log_retention_days=30,
                patch_retention_count=20,
            ),
            now=self.now,
        )

        self.assertEqual(report.retained_record_ids, ("held",))
        self.assertEqual(report.deleted_record_ids, ("not-held",))
        self.assertFalse(self.store.get("held").is_deleted)  # type: ignore[union-attr]

    def test_audit_event_is_content_free(self) -> None:
        sink = InMemoryMemoryAuditSink()
        audited_store = AuditedMemoryStore(self.store, sink=sink)
        self.store.put(
            _record(
                "audit-old",
                scope="session:s1",
                created_at=_iso(self.now, days=-45),
            )
        )

        enforce_runtime_memory_retention(
            audited_store,
            RuntimeMemoryRetentionPolicy(
                log_retention_days=30,
                patch_retention_count=20,
            ),
            now=self.now,
        )

        event = sink.events[-1]
        self.assertEqual(event.event_type, MEMORY_RETENTION_ENFORCE)
        self.assertEqual(event.details["deleted_ids"], ["audit-old"])
        self.assertEqual(event.details["deleted_count"], 1)
        self.assertNotIn("content", str(event.details).lower())
        self.assertNotIn("content for audit-old", str(event.details))

    def test_failure_rolls_back_before_commit(self) -> None:
        self.store.put(
            _record(
                "rollback-old",
                scope="session:s1",
                created_at=_iso(self.now, days=-45),
            )
        )

        with self.assertRaises(RuntimeError):
            enforce_runtime_memory_retention(
                self.store,
                RuntimeMemoryRetentionPolicy(
                    log_retention_days=30,
                    patch_retention_count=20,
                ),
                now=self.now,
                before_commit=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            )

        recovered = self.store.get("rollback-old")
        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertFalse(recovered.is_deleted)

    def test_unsupported_backend_reports_before_mutation(self) -> None:
        @dataclass
        class UnsupportedStore:
            mutated: bool = False

            def delete(self, record_id: str) -> None:
                del record_id
                self.mutated = True

        store = UnsupportedStore()
        report = enforce_runtime_memory_retention(
            store,
            RuntimeMemoryRetentionPolicy(
                log_retention_days=30,
                patch_retention_count=20,
            ),
            now=self.now,
        )

        self.assertEqual(report.status, "unsupported")
        self.assertFalse(store.mutated)


if __name__ == "__main__":
    unittest.main()
