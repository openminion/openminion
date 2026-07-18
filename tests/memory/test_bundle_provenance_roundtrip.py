from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from openminion.modules.memory.contracts.provenance import (
    MemoryProvenanceEntry,
    TurnProvenanceTrace,
)
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.portability.codec import (
    read_bundle_snapshot,
    write_bundle_snapshot,
)
from openminion.modules.memory.portability.models import (
    MemoryBundleExportOptions,
    MemoryBundleImportOptions,
)
from openminion.modules.memory.runtime.provenance import (
    MemoryProvenanceRecorder,
    default_provenance_recorder,
    set_default_provenance_recorder,
)
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


class TestBundleProvenanceRoundTrip(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmpdir.name)
        self._original_recorder = default_provenance_recorder()
        self._recorder = MemoryProvenanceRecorder()
        set_default_provenance_recorder(self._recorder)

    def tearDown(self) -> None:
        set_default_provenance_recorder(self._original_recorder)
        self.tmpdir.cleanup()

    def _seed_record(self, store: SQLiteMemoryStore, *, scope: str, id: str) -> None:
        store.put(
            MemoryRecord(
                id=id,
                scope=scope,
                type="fact",
                content={"text": f"record {id}"},
                created_at="2026-05-18T00:00:00Z",
                updated_at="2026-05-18T00:00:00Z",
                source="tool_output",
            )
        )

    def _seed_trace(self, *, session_id: str, turn_id: str, memory_id: str) -> None:
        self._recorder.record_turn_trace(
            TurnProvenanceTrace(
                session_id=session_id,
                turn_id=turn_id,
                recorded_at="2026-05-18T01:00:00Z",
                entries=(
                    MemoryProvenanceEntry(
                        memory_id=memory_id,
                        source="tool_output",
                        written_at="2026-05-18T00:00:00Z",
                        retrieval_score=0.9,
                    ),
                ),
                query="who is the user",
            )
        )

    def _export_snapshot(
        self,
        *,
        include_provenance: bool,
        scope: str = "session:s1",
        record_id: str = "r1",
        turn_id: str = "t1",
    ):
        db_path = self.tmp_path / "src.db"
        store = SQLiteMemoryStore(db_path)
        self._seed_record(store, scope=scope, id=record_id)
        self._seed_trace(
            session_id=scope.split(":", 1)[1], turn_id=turn_id, memory_id=record_id
        )
        service = MemoryService(store)
        return service.export_bundle_snapshot(
            MemoryBundleExportOptions(scopes=["session:s1"])
            if not include_provenance
            else MemoryBundleExportOptions(
                scopes=[scope],
                include_provenance=True,
            )
        )

    def _invoke_cli_export(self, *, include_provenance: bool) -> Path:
        from typer.testing import CliRunner

        from openminion.modules.memory.cli import _build_app

        db_path = self.tmp_path / "cli.db"
        store = SQLiteMemoryStore(db_path)
        self._seed_record(store, scope="session:s1", id="r1")
        self._seed_trace(session_id="s1", turn_id="t1", memory_id="r1")

        bundle_path = self.tmp_path / "cli-bundle.tar.gz"
        args = [
            "export",
            "--scope",
            "session:s1",
            "--bundle",
            "--out",
            str(bundle_path),
            "--db",
            str(db_path),
        ]
        if include_provenance:
            args.insert(3, "--include-provenance")
        result = CliRunner().invoke(_build_app(), args)
        self.assertEqual(result.exit_code, 0, msg=result.output)
        return bundle_path

    def test_include_provenance_off_writes_no_provenance_section(self) -> None:
        snapshot = self._export_snapshot(include_provenance=False)
        self.assertEqual(snapshot.provenance_traces, [])

        bundle_path = self.tmp_path / "bundle-off.tar.gz"
        write_bundle_snapshot(snapshot, bundle_path)
        reloaded = read_bundle_snapshot(bundle_path)
        self.assertEqual(reloaded.provenance_traces, [])
        self.assertFalse(reloaded.manifest["sections"]["provenance_traces"])

    def test_include_provenance_on_round_trips_through_disk(self) -> None:
        snapshot = self._export_snapshot(include_provenance=True)
        self.assertEqual(len(snapshot.provenance_traces), 1)
        self.assertEqual(snapshot.provenance_traces[0].turn_id, "t1")

        bundle_path = self.tmp_path / "bundle-on.tar.gz"
        write_bundle_snapshot(snapshot, bundle_path)
        reloaded = read_bundle_snapshot(bundle_path)
        self.assertEqual(len(reloaded.provenance_traces), 1)
        self.assertEqual(reloaded.provenance_traces[0].turn_id, "t1")
        self.assertEqual(reloaded.provenance_traces[0].entries[0].memory_id, "r1")
        self.assertTrue(reloaded.manifest["sections"]["provenance_traces"])
        self.assertEqual(reloaded.manifest["counts"]["provenance_traces"], 1)

        fresh_recorder = MemoryProvenanceRecorder()
        set_default_provenance_recorder(fresh_recorder)

        target_db = self.tmp_path / "target.db"
        target_store = SQLiteMemoryStore(target_db)
        target_service = MemoryService(target_store)
        result = target_service.import_bundle_snapshot(
            reloaded,
            MemoryBundleImportOptions(
                trust_mode="direct",
                conflict_mode="skip",
                id_mode="preserve",
            ),
        )
        self.assertTrue(result.applied)
        self.assertEqual(result.imported_provenance_traces, 1)

        roundtripped = fresh_recorder.get_turn_trace(session_id="s1", turn_id="t1")
        self.assertIsNotNone(roundtripped)
        self.assertEqual(roundtripped.entries[0].memory_id, "r1")
        by_mem = fresh_recorder.find_traces_citing_memory("r1")
        self.assertEqual(len(by_mem), 1)

    def test_session_scope_filter_isolates_traces(self) -> None:
        db_path = self.tmp_path / "src.db"
        store = SQLiteMemoryStore(db_path)
        self._seed_record(store, scope="session:s1", id="r1")
        self._seed_record(store, scope="session:s2", id="r2")
        self._seed_trace(session_id="s1", turn_id="t1", memory_id="r1")
        self._seed_trace(session_id="s2", turn_id="t2", memory_id="r2")

        service = MemoryService(store)
        snapshot = service.export_bundle_snapshot(
            MemoryBundleExportOptions(
                scopes=["session:s1"],
                include_provenance=True,
            )
        )
        self.assertEqual(len(snapshot.provenance_traces), 1)
        self.assertEqual(snapshot.provenance_traces[0].session_id, "s1")

    def test_dry_run_does_not_mutate_recorder(self) -> None:
        snapshot = self._export_snapshot(include_provenance=True)

        fresh_recorder = MemoryProvenanceRecorder()
        set_default_provenance_recorder(fresh_recorder)

        target_db = self.tmp_path / "target.db"
        target_store = SQLiteMemoryStore(target_db)
        target_service = MemoryService(target_store)
        result = target_service.import_bundle_snapshot(
            snapshot,
            MemoryBundleImportOptions(
                trust_mode="direct",
                conflict_mode="skip",
                id_mode="preserve",
                dry_run=True,
            ),
        )
        self.assertFalse(result.applied)
        self.assertEqual(result.imported_provenance_traces, 1)
        self.assertIsNone(fresh_recorder.get_turn_trace(session_id="s1", turn_id="t1"))

    def test_cli_export_flag_threads_through(self) -> None:
        bundle_path = self._invoke_cli_export(include_provenance=True)
        self.assertTrue(bundle_path.exists())

        reloaded = read_bundle_snapshot(bundle_path)
        self.assertEqual(len(reloaded.provenance_traces), 1)
        self.assertEqual(reloaded.provenance_traces[0].turn_id, "t1")

    def test_cli_export_without_flag_omits_provenance(self) -> None:
        bundle_path = self._invoke_cli_export(include_provenance=False)
        reloaded = read_bundle_snapshot(bundle_path)
        self.assertEqual(reloaded.provenance_traces, [])

    def test_candidate_mode_skips_provenance_section(self) -> None:
        db_path = self.tmp_path / "src.db"
        store = SQLiteMemoryStore(db_path)
        self._seed_record(store, scope="session:s1", id="r1")
        self._seed_trace(session_id="s1", turn_id="t1", memory_id="r1")

        service = MemoryService(store)
        snapshot = service.export_bundle_snapshot(
            MemoryBundleExportOptions(
                scopes=["session:s1"],
                include_provenance=True,
            )
        )

        fresh_recorder = MemoryProvenanceRecorder()
        set_default_provenance_recorder(fresh_recorder)

        target_db = self.tmp_path / "target.db"
        target_store = SQLiteMemoryStore(target_db)
        target_service = MemoryService(target_store)
        result = target_service.import_bundle_snapshot(
            snapshot,
            MemoryBundleImportOptions(
                trust_mode="candidate",
                conflict_mode="skip",
                id_mode="preserve",
            ),
        )
        self.assertTrue(result.applied)
        self.assertIn("provenance_traces", result.skipped_sections)
        self.assertIsNone(fresh_recorder.get_turn_trace(session_id="s1", turn_id="t1"))
