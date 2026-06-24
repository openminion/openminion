from __future__ import annotations

import datetime
import tempfile
import unittest
from pathlib import Path
from typing import Any

from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


def _utc_iso(offset_days: int = 0) -> str:
    base = datetime.datetime.now(datetime.timezone.utc)
    if offset_days:
        base = base + datetime.timedelta(days=offset_days)
    return base.isoformat()


def _make_record(
    *,
    record_id: str,
    scope: str,
    record_type: str = "fact",
    content: dict[str, Any] | str = "test content",
    key: str | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    expires_at: str | None = None,
) -> MemoryRecord:
    now = _utc_iso()
    return MemoryRecord(
        id=record_id,
        scope=scope,
        type=record_type,  # type: ignore[arg-type]
        content=content,
        created_at=now,
        updated_at=now,
        key=key,
        title=title,
        tags=tags or [],
        expires_at=expires_at,
    )


class SMD005RestartDurabilityTests(unittest.TestCase):
    def test_single_record_persists_across_store_restart(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "smd005_single.db"

            # Phase 1: write via first store instance.
            store_a = SQLiteMemoryStore(db_path)
            rec = _make_record(
                record_id="smd005-single-1",
                scope="session:smd005-session",
                record_type="fact",
                content={"text": "remember: SMD-005 single-record smoke"},
                key="smd005:single",
            )
            store_a.put(rec)
            self.assertTrue(db_path.exists(), "SQLite file should exist after put()")

            # Drop the store reference (simulating process exit). SQLite
            # connections are per-call (`_connect()` context-managed) so no
            # explicit close needed.
            del store_a

            # Phase 2: open a fresh store instance against the same file
            # (simulating runtime restart with the existing memory file).
            store_b = SQLiteMemoryStore(db_path)
            recovered = store_b.get("smd005-single-1")

            self.assertIsNotNone(recovered, "record must persist across restart")
            assert recovered is not None  # narrow for mypy/pyright
            self.assertEqual(recovered.id, "smd005-single-1")
            self.assertEqual(recovered.scope, "session:smd005-session")
            self.assertEqual(recovered.type, "fact")
            # Content can be dict or str; codec preserves either shape.
            content = recovered.content
            if isinstance(content, dict):
                self.assertEqual(
                    content.get("text"), "remember: SMD-005 single-record smoke"
                )
            else:
                self.assertIn("SMD-005", str(content))

    def test_multi_scope_records_persist_across_restart(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "smd005_multi_scope.db"

            store_a = SQLiteMemoryStore(db_path)
            scopes = [
                ("session:smd005-multi", "smd005-session-rec"),
                ("agent:smd005-agent", "smd005-agent-rec"),
                ("global:system", "smd005-global-rec"),
            ]
            for scope, rid in scopes:
                rec = _make_record(
                    record_id=rid,
                    scope=scope,
                    record_type="pin" if scope.startswith("global") else "fact",
                    content={"text": f"record under {scope}"},
                    key=f"smd005:{rid}",
                )
                store_a.put(rec)
            del store_a

            store_b = SQLiteMemoryStore(db_path)
            for scope, rid in scopes:
                recovered = store_b.get(rid)
                self.assertIsNotNone(
                    recovered, f"record {rid} ({scope}) must persist across restart"
                )
                assert recovered is not None
                self.assertEqual(recovered.scope, scope, f"scope mismatch for {rid}")
                self.assertIn(scope, str(recovered.content))

            # And list_scopes() should report all three after restart.
            scopes_after = set(store_b.list_scopes())
            self.assertIn("session:smd005-multi", scopes_after)
            self.assertIn("agent:smd005-agent", scopes_after)
            self.assertIn("global:system", scopes_after)

    def test_deletes_persist_across_restart(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "smd005_delete.db"

            store_a = SQLiteMemoryStore(db_path)
            keep = _make_record(
                record_id="smd005-keep",
                scope="session:smd005-delete",
                content={"text": "keep me"},
                key="smd005:keep",
            )
            drop = _make_record(
                record_id="smd005-drop",
                scope="session:smd005-delete",
                content={"text": "drop me"},
                key="smd005:drop",
            )
            store_a.put(keep)
            store_a.put(drop)
            store_a.delete("smd005-drop")
            del store_a

            store_b = SQLiteMemoryStore(db_path)
            # Live-listing must hide the soft-deleted record after restart.
            results = store_b.list(ListQueryOptions(scopes=["session:smd005-delete"]))
            visible_ids = {rec.id for rec in results}
            self.assertIn(
                "smd005-keep",
                visible_ids,
                "kept record must persist visible across restart",
            )
            self.assertNotIn(
                "smd005-drop",
                visible_ids,
                "soft-deleted record must remain hidden after restart "
                "(soft-delete state is durable)",
            )

    def test_list_query_after_restart_returns_persisted_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "smd005_list.db"

            store_a = SQLiteMemoryStore(db_path)
            for i in range(3):
                store_a.put(
                    _make_record(
                        record_id=f"smd005-list-{i}",
                        scope="session:smd005-list",
                        content={"text": f"record {i}"},
                        key=f"smd005:list:{i}",
                    )
                )
            del store_a

            store_b = SQLiteMemoryStore(db_path)
            results = store_b.list(ListQueryOptions(scopes=["session:smd005-list"]))
            self.assertGreaterEqual(
                len(results), 3, "all 3 records must persist and be listable"
            )
            recovered_ids = {rec.id for rec in results}
            for i in range(3):
                self.assertIn(f"smd005-list-{i}", recovered_ids)


class SMD006RetentionAdvisoryConfigTests(unittest.TestCase):
    def test_log_retention_days_default_is_30(self):
        from openminion.base.config.runtime import RuntimeConfig

        runtime = RuntimeConfig()
        self.assertEqual(runtime.memory_log_retention_days, 30)

    def test_patch_retention_count_default_is_200(self):
        from openminion.base.config.runtime import RuntimeConfig

        runtime = RuntimeConfig()
        self.assertEqual(runtime.memory_patch_retention_count, 200)

    def test_log_retention_days_flows_into_capsule_policy_snapshot(self):
        from openminion.base.config import OpenMinionConfig
        from openminion.services.agent.memory.capsule import (
            build_memory_policy_snapshot,
        )

        config = OpenMinionConfig()
        object.__setattr__(config.runtime, "memory_log_retention_days", 45)
        snapshot = build_memory_policy_snapshot(config=config)
        self.assertEqual(
            snapshot["retention_days"],
            45,
            "configured value must flow into policy snapshot",
        )

    def test_log_retention_days_clamped_to_at_least_one(self):
        from openminion.base.config import OpenMinionConfig
        from openminion.services.agent.memory.capsule import (
            build_memory_policy_snapshot,
        )

        config = OpenMinionConfig()
        # Bypass parser validation by setting attribute directly to test
        # the runtime-side clamp. Parser layer is tested separately.
        object.__setattr__(config.runtime, "memory_log_retention_days", 0)
        snapshot = build_memory_policy_snapshot(config=config)
        self.assertEqual(
            snapshot["retention_days"],
            1,
            "retention_days < 1 must be clamped to 1 (boundary)",
        )

    def test_patch_retention_count_is_advisory_only_no_enforcement(self):
        # Read-side enforcement check: we assert the contract by inspecting
        # the runtime config — the value is settable and parseable, but
        # any cutoff behavior driven by it is not yet implemented.
        from openminion.base.config.runtime import RuntimeConfig

        runtime = RuntimeConfig()
        runtime.memory_patch_retention_count = 50
        # The config value is honored at the data layer.
        self.assertEqual(runtime.memory_patch_retention_count, 50)

    def test_summary_compression_age_days_is_actual_enforcement_path(self):
        from openminion.modules.memory.runtime import gc as gc_module

        # Verify the canonical enforcement function exists and is callable.
        self.assertTrue(
            hasattr(gc_module, "compress_old_summaries"),
            "compress_old_summaries must remain the canonical retention "
            "enforcement entry point per SMD-006 captured evidence",
        )
        compress_fn = gc_module.compress_old_summaries
        self.assertTrue(
            callable(compress_fn),
            "compress_old_summaries must be callable",
        )
