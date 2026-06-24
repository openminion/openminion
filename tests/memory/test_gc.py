import unittest
import tempfile
import datetime
from pathlib import Path

from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.memory.models import ArtifactRef, MemoryCandidate, MemoryRecord
from openminion.modules.memory.runtime.gc import apply_confidence_decay, run_gc

_VALID_REF_A = "a" * 64
_VALID_REF_B = "b" * 64


def _artifact_ref(ref: str) -> ArtifactRef:
    return ArtifactRef(
        ref=ref,
        mime="application/octet-stream",
        sha256=ref.rsplit("/", 1)[-1],
        size_bytes=1,
    )


class _RecordingArtifactCtl:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []
        self.active_refs: set[tuple[str, str, str]] = set()

    def ref_add(self, owner_type: str, owner_id: str, ref_or_sha: str) -> None:
        self.calls.append(("add", owner_type, owner_id, ref_or_sha))
        self.active_refs.add((owner_type, owner_id, ref_or_sha))

    def ref_remove(self, owner_type: str, owner_id: str, ref_or_sha: str) -> None:
        self.calls.append(("remove", owner_type, owner_id, ref_or_sha))
        self.active_refs.discard((owner_type, owner_id, ref_or_sha))


def make_record(
    rid,
    scope="session:s1",
    expired=False,
    deleted=False,
    evidence_refs=None,
    confidence=0.5,
):
    now = datetime.datetime.now(datetime.timezone.utc)
    created_at = now.isoformat()
    updated_at = created_at
    expires_at = None
    if expired:
        expires_at = (now - datetime.timedelta(days=1)).isoformat()
    rec = MemoryRecord(
        id=rid,
        scope=scope,
        type="fact",
        content=f"content {rid}",
        created_at=created_at,
        updated_at=updated_at,
        expires_at=expires_at,
        evidence_refs=list(evidence_refs or []),
        confidence=confidence,
        is_deleted=deleted,
    )
    return rec


class TestGC(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        self.artifactctl = _RecordingArtifactCtl()
        self.store = SQLiteMemoryStore(self.db_path, artifactctl=self.artifactctl)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_gc_purges_deleted_records(self):
        rec1 = make_record("r1")
        rec2 = make_record("r2")
        self.store.put(rec1)
        self.store.put(rec2)
        self.store.delete("r1")

        result = run_gc(self.store)

        self.assertEqual(result.deleted_records, 1)
        self.assertIsNone(self.store.get("r1"))
        self.assertIsNotNone(self.store.get("r2"))

    def test_gc_purges_expired_records(self):
        expired = make_record("r_expired", expired=True)
        alive = make_record("r_alive")
        self.store.put(expired)
        self.store.put(alive)

        result = run_gc(self.store)
        self.assertEqual(result.deleted_records, 1)
        self.assertIsNone(self.store.get("r_expired"))
        self.assertIsNotNone(self.store.get("r_alive"))

    def test_gc_preserves_superseded_referenced_records(self):
        old = make_record("old", deleted=True)
        self.store.put(old)
        new = make_record("new")
        self.store.put(new)
        # Simulate a chain: 'new' supersedes 'old', 'old' is superseded_by 'new'
        with self.store._connect() as conn:
            conn.execute("UPDATE memory_records SET supersedes_id='old' WHERE id='new'")
            conn.execute(
                "UPDATE memory_records SET superseded_by_id='new' WHERE id='old'"
            )

        result = run_gc(self.store)
        # 'old' is deleted=1 but 'new' has supersedes_id='old', so it should NOT be purged
        self.assertEqual(result.deleted_records, 0)

    def test_gc_purges_rejected_and_promoted_candidates(self):
        c_proposed = MemoryCandidate(
            candidate_id="c0",
            session_id="s1",
            proposed_scope="session:s1",
            type="fact",
            content="pending",
            status="proposed",
        )
        c_rejected = MemoryCandidate(
            candidate_id="c1",
            session_id="s1",
            proposed_scope="session:s1",
            type="fact",
            content="rejected",
            status="rejected",
        )
        c_promoted = MemoryCandidate(
            candidate_id="c2",
            session_id="s1",
            proposed_scope="session:s1",
            type="fact",
            content="done",
            status="promoted",
        )
        self.store.candidate_put(c_proposed)
        self.store.candidate_put(c_rejected)
        self.store.candidate_put(c_promoted)

        result = run_gc(self.store)
        self.assertEqual(result.deleted_candidates, 2)
        self.assertIsNotNone(self.store.candidate_get("c0"))
        self.assertIsNone(self.store.candidate_get("c1"))
        self.assertIsNone(self.store.candidate_get("c2"))

    def test_gc_returns_deterministic_counts(self):
        self.store.put(make_record("a", deleted=True))
        self.store.put(make_record("b", deleted=True))
        result = run_gc(self.store)
        self.assertEqual(result.deleted_records, 2)

    def test_gc_removes_edges_for_expired_records_and_rejected_candidates(self):
        expired = make_record(
            "r_expired",
            scope="agent:main",
            expired=True,
            evidence_refs=[_artifact_ref(_VALID_REF_A)],
        )
        rejected = MemoryCandidate(
            candidate_id="c_rejected",
            session_id="s1",
            proposed_scope="session:s1",
            type="fact",
            content="rejected",
            status="rejected",
            evidence_refs=[_artifact_ref(_VALID_REF_B)],
        )
        self.store.put(expired)
        self.store.candidate_put(rejected)
        self.artifactctl.calls.clear()

        result = run_gc(self.store)

        self.assertEqual(result.deleted_records, 1)
        self.assertEqual(result.deleted_candidates, 1)
        self.assertEqual(
            self.artifactctl.calls,
            [
                ("remove", "memory", "r_expired", _VALID_REF_A),
                ("remove", "memory", "c_rejected", _VALID_REF_B),
            ],
        )
        self.assertNotIn(
            ("memory", "r_expired", _VALID_REF_A),
            self.artifactctl.active_refs,
        )
        self.assertNotIn(
            ("memory", "c_rejected", _VALID_REF_B),
            self.artifactctl.active_refs,
        )

    def test_confidence_decay_eviction_removes_artifact_edges(self):
        stale_time = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)
        ).isoformat()
        record = make_record(
            "decay-edge",
            scope="agent:main",
            evidence_refs=[_artifact_ref(_VALID_REF_A)],
            confidence=0.1,
        )
        self.store.put(record)
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE memory_records SET updated_at = ? WHERE id = ?",
                (stale_time, "decay-edge"),
            )
        self.artifactctl.calls.clear()

        decayed, evicted = apply_confidence_decay(
            self.store,
            interval_days=1,
            decay_rate=0.2,
            min_confidence=0.3,
        )

        self.assertEqual((decayed, evicted), (1, 1))
        self.assertEqual(
            self.artifactctl.calls,
            [("remove", "memory", "decay-edge", _VALID_REF_A)],
        )
        deleted = self.store.get("decay-edge")
        self.assertIsNotNone(deleted)
        self.assertTrue(deleted.is_deleted)
