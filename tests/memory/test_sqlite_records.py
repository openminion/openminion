import unittest
import tempfile
import datetime
from pathlib import Path

from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.memory.models import MemoryRecord, ArtifactRef
from openminion.modules.memory.storage.base import ListQueryOptions, RecordOrder

_VALID_REF_A = "a" * 64
_VALID_REF_B = "b" * 64


def _artifact_ref(ref: str) -> ArtifactRef:
    return ArtifactRef(
        ref=ref,
        mime="application/octet-stream",
        sha256=ref.rsplit("/", 1)[-1],
        size_bytes=1,
    )


def _non_artifact_ref(ref: str = "mem://skip") -> ArtifactRef:
    return ArtifactRef(
        ref=ref,
        mime="application/octet-stream",
        sha256="unknown",
        size_bytes=0,
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


def create_mock_record(
    id1: str, scope: str, type_val: str, content: str = "mock content"
) -> MemoryRecord:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return MemoryRecord(
        id=id1,
        scope=scope,
        type=type_val,
        content=content,
        created_at=now,
        updated_at=now,
        tags=["a", "b"],
        evidence_refs=[
            ArtifactRef(ref="doc", mime="text/plain", sha256="123", size_bytes=100)
        ],
    )


class TestSQLiteRecords(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        self.artifactctl = _RecordingArtifactCtl()
        self.store = SQLiteMemoryStore(self.db_path, artifactctl=self.artifactctl)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_put_and_get(self):
        record = create_mock_record("r1", "session:foo", "fact")
        self.store.put(record)

        fetched = self.store.get("r1")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.id, record.id)
        self.assertEqual(fetched.content, record.content)
        self.assertEqual(len(fetched.evidence_refs), 1)
        self.assertEqual(fetched.evidence_refs[0].ref, "doc")

    def test_delete_is_soft(self):
        record = create_mock_record("r2", "session:foo", "fact")
        self.store.put(record)
        self.store.delete("r2")

        fetched = self.store.get("r2")
        self.assertIsNotNone(fetched)
        self.assertTrue(fetched.is_deleted)

    def test_list_excludes_deleted_by_default(self):
        r1 = create_mock_record("r1", "session:foo", "fact")
        r2 = create_mock_record("r2", "session:foo", "task")
        self.store.put(r1)
        self.store.put(r2)

        res = self.store.list(ListQueryOptions(scopes=["session:foo"]))
        self.assertEqual(len(res), 2)

        self.store.delete("r1")
        res2 = self.store.list(ListQueryOptions(scopes=["session:foo"]))
        self.assertEqual(len(res2), 1)
        self.assertEqual(res2[0].id, "r2")

    def test_list_filters_and_ordering(self):
        r1 = create_mock_record("r1", "session:foo", "fact")
        r2 = create_mock_record("r2", "session:bar", "task")
        r3 = create_mock_record("r3", "session:foo", "fact")
        self.store.put(r1)
        self.store.put(r2)
        self.store.put(r3)

        res_scope = self.store.list(ListQueryOptions(scopes=["session:bar"]))
        self.assertEqual(len(res_scope), 1)
        self.assertEqual(res_scope[0].id, "r2")

        res_type = self.store.list(
            ListQueryOptions(scopes=["session:foo", "session:bar"], types=["fact"])
        )
        self.assertEqual(len(res_type), 2)

        res_order = self.store.list(
            ListQueryOptions(
                scopes=["session:foo"], order_by=RecordOrder.UPDATED_AT_DESC
            )
        )
        self.assertEqual(len(res_order), 2)

    def test_list_excludes_invalidated_by_default_and_can_include_them(self):
        r1 = create_mock_record("r1", "session:foo", "fact")
        r2 = create_mock_record("r2", "session:foo", "fact")
        self.store.put(r1)
        self.store.put(r2)
        self.store.invalidate(
            "r1",
            valid_to="2026-05-21T00:00:00+00:00",
            reason="corrected",
        )

        active = self.store.list(ListQueryOptions(scopes=["session:foo"]))
        self.assertEqual([item.id for item in active], ["r2"])

        with_invalidated = self.store.list(
            ListQueryOptions(scopes=["session:foo"], include_invalidated=True)
        )
        self.assertEqual({item.id for item in with_invalidated}, {"r1", "r2"})

    def test_put_and_delete_manage_artifact_edges_and_ignore_invalid_refs(self):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        record = MemoryRecord(
            id="edge-put",
            scope="session:foo",
            type="fact",
            content="tracked artifact",
            created_at=now,
            updated_at=now,
            evidence_refs=[_artifact_ref(_VALID_REF_A), _non_artifact_ref()],
        )

        self.store.put(record)

        self.assertEqual(
            self.artifactctl.calls,
            [("add", "memory", "edge-put", _VALID_REF_A)],
        )
        self.assertIn(
            ("memory", "edge-put", _VALID_REF_A), self.artifactctl.active_refs
        )

        self.store.delete("edge-put")

        self.assertEqual(
            self.artifactctl.calls[-1],
            ("remove", "memory", "edge-put", _VALID_REF_A),
        )
        self.assertNotIn(
            ("memory", "edge-put", _VALID_REF_A),
            self.artifactctl.active_refs,
        )

    def test_upsert_rehomes_artifact_edges_to_new_owner(self):
        first = self.store.upsert(
            "session:foo",
            "fact",
            "favorite-color",
            {
                "content": "blue",
                "evidence_refs": [_artifact_ref(_VALID_REF_A)],
            },
        )
        second = self.store.upsert(
            "session:foo",
            "fact",
            "favorite-color",
            {
                "content": "green",
                "evidence_refs": [_artifact_ref(_VALID_REF_B), _non_artifact_ref()],
            },
        )

        self.assertNotEqual(first.id, second.id)
        self.assertEqual(
            self.artifactctl.calls,
            [
                ("add", "memory", first.id, _VALID_REF_A),
                ("remove", "memory", first.id, _VALID_REF_A),
                ("add", "memory", second.id, _VALID_REF_B),
            ],
        )
        self.assertNotIn(
            ("memory", first.id, _VALID_REF_A), self.artifactctl.active_refs
        )
        self.assertIn(("memory", second.id, _VALID_REF_B), self.artifactctl.active_refs)

    def test_tombstone_removes_active_artifact_edges(self):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        record = MemoryRecord(
            id="edge-tombstone",
            scope="session:foo",
            type="fact",
            key="artifact-topic",
            content="tracked artifact",
            created_at=now,
            updated_at=now,
            evidence_refs=[_artifact_ref(_VALID_REF_A)],
        )
        self.store.put(record)
        self.artifactctl.calls.clear()

        self.store.tombstone("session:foo", "fact", "artifact-topic")

        self.assertEqual(
            self.artifactctl.calls,
            [("remove", "memory", "edge-tombstone", _VALID_REF_A)],
        )
        self.assertNotIn(
            ("memory", "edge-tombstone", _VALID_REF_A),
            self.artifactctl.active_refs,
        )

    def test_supersede_by_contradiction_moves_artifact_edges(self):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        old_record = MemoryRecord(
            id="old-record",
            scope="agent:main",
            type="fact",
            content="old truth",
            created_at=now,
            updated_at=now,
            evidence_refs=[_artifact_ref(_VALID_REF_A)],
        )
        new_record = MemoryRecord(
            id="new-record",
            scope="agent:main",
            type="fact",
            content="new truth",
            created_at=now,
            updated_at=now,
            evidence_refs=[_artifact_ref(_VALID_REF_B)],
        )
        self.store.put(old_record)
        self.store.put(new_record)
        self.artifactctl.calls.clear()

        self.store.supersede_by_contradiction("old-record", "new-record")

        self.assertEqual(
            self.artifactctl.calls,
            [
                ("remove", "memory", "old-record", _VALID_REF_A),
                ("add", "memory", "new-record", _VALID_REF_B),
            ],
        )
        self.assertNotIn(
            ("memory", "old-record", _VALID_REF_A),
            self.artifactctl.active_refs,
        )
        self.assertIn(
            ("memory", "new-record", _VALID_REF_B),
            self.artifactctl.active_refs,
        )
        old = self.store.get("old-record")
        new = self.store.get("new-record")
        self.assertIsNotNone(old)
        self.assertIsNotNone(new)
        self.assertEqual(old.valid_to, new.created_at)
