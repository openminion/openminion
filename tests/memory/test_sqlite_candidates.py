import unittest
import tempfile
from pathlib import Path

from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.memory.models import (
    ArtifactRef,
    CandidateReview,
    MemoryCandidate,
    MemoryRecord,
)

_VALID_REF_A = "a" * 64
_VALID_REF_B = "b" * 64
_VALID_REF_C = "c" * 64


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


class TestSQLiteCandidateCRUD(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        self.artifactctl = _RecordingArtifactCtl()
        self.store = SQLiteMemoryStore(self.db_path, artifactctl=self.artifactctl)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_candidate_put_get_delete(self):
        candidate = MemoryCandidate(
            candidate_id="can1",
            session_id="sess1",
            proposed_scope="session:sess1",
            type="fact",
            content="A proposed fact",
            evidence_refs=[_artifact_ref(_VALID_REF_A)],
        )

        # Put
        self.store.candidate_put(candidate)
        self.assertIn(("memory", "can1", _VALID_REF_A), self.artifactctl.active_refs)

        # Get
        retrieved = self.store.candidate_get("can1")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.candidate_id, "can1")
        self.assertEqual(retrieved.content, "A proposed fact")
        self.assertEqual(retrieved.status, "proposed")

        # Update with review
        reviewed_candidate = MemoryCandidate(
            candidate_id="can1",
            session_id="sess1",
            proposed_scope="session:sess1",
            type="fact",
            content="A proposed fact",
            status="approved",
            review=CandidateReview(
                reviewer="agent", decided_at="2024-01-01T00:00:00Z", note="Looks good"
            ),
        )
        self.store.candidate_put(reviewed_candidate)

        retrieved2 = self.store.candidate_get("can1")
        self.assertEqual(retrieved2.status, "approved")
        self.assertEqual(retrieved2.review.reviewer, "agent")
        self.assertEqual(retrieved2.review.note, "Looks good")

        # Delete
        self.store.candidate_delete("can1")
        self.assertIsNone(self.store.candidate_get("can1"))
        self.assertNotIn(("memory", "can1", _VALID_REF_A), self.artifactctl.active_refs)

    def test_candidate_put_replaces_previous_artifact_edges(self):
        original = MemoryCandidate(
            candidate_id="can1",
            session_id="sess1",
            proposed_scope="session:sess1",
            type="fact",
            content="A proposed fact",
            evidence_refs=[_artifact_ref(_VALID_REF_A)],
        )
        updated = MemoryCandidate(
            candidate_id="can1",
            session_id="sess1",
            proposed_scope="session:sess1",
            type="fact",
            content="A replacement fact",
            evidence_refs=[_artifact_ref(_VALID_REF_B), _non_artifact_ref()],
        )

        self.store.candidate_put(original)
        self.store.candidate_put(updated)

        self.assertEqual(
            self.artifactctl.calls,
            [
                ("add", "memory", "can1", _VALID_REF_A),
                ("remove", "memory", "can1", _VALID_REF_A),
                ("add", "memory", "can1", _VALID_REF_B),
            ],
        )
        self.assertNotIn(("memory", "can1", _VALID_REF_A), self.artifactctl.active_refs)
        self.assertIn(("memory", "can1", _VALID_REF_B), self.artifactctl.active_refs)

    def test_candidate_list(self):
        from openminion.modules.memory.storage.base import CandidateListOptions

        c1 = MemoryCandidate(
            candidate_id="c1",
            session_id="s1",
            proposed_scope="session:global",
            type="fact",
            content="fact 1",
        )
        c2 = MemoryCandidate(
            candidate_id="c2",
            session_id="s1",
            proposed_scope="session:global",
            type="fact",
            content="fact 2",
        )
        self.store.candidate_put(c1)
        self.store.candidate_put(c2)

        # List
        candidates = self.store.candidate_list(CandidateListOptions(session_id="s1"))
        self.assertEqual(len(candidates), 2)
        self.assertCountEqual([c.candidate_id for c in candidates], ["c1", "c2"])

    def test_candidate_update(self):
        c1 = MemoryCandidate(
            candidate_id="c1",
            session_id="s1",
            proposed_scope="session:global",
            type="fact",
            content="fact 1",
        )
        self.store.candidate_put(c1)

        updated = self.store.candidate_update(
            "c1",
            {
                "status": "approved",
                "review": CandidateReview(
                    reviewer="agent",
                    decided_at="2024-01-01T00:00:00Z",
                    note="Good fact",
                ),
            },
        )
        self.assertEqual(updated.status, "approved")
        self.assertEqual(updated.review.reviewer, "agent")

    def test_candidate_meta_roundtrips_and_scope_listing_can_cross_sessions(self):
        from openminion.modules.memory.storage.base import CandidateListOptions

        c1 = MemoryCandidate(
            candidate_id="c-scope-1",
            session_id="s1",
            proposed_scope="agent:test",
            type="fact",
            content="fact 1",
            meta={"reconfirmation_count": 1},
        )
        c2 = MemoryCandidate(
            candidate_id="c-scope-2",
            session_id="s2",
            proposed_scope="agent:test",
            type="fact",
            content="fact 2",
            meta={"retrieval_hit_count": 2},
        )
        self.store.candidate_put(c1)
        self.store.candidate_put(c2)

        updated = self.store.candidate_update(
            "c-scope-1",
            {
                "meta": {"reconfirmation_count": 2, "contradicted": True},
                "confidence": 0.6,
            },
        )
        self.assertEqual(updated.meta["reconfirmation_count"], 2)
        self.assertTrue(updated.meta["contradicted"])
        self.assertEqual(updated.confidence, 0.6)

        candidates = self.store.candidate_list(
            CandidateListOptions(proposed_scope="agent:test", status="proposed")
        )
        self.assertEqual(len(candidates), 2)
        self.assertCountEqual(
            [candidate.candidate_id for candidate in candidates],
            ["c-scope-1", "c-scope-2"],
        )

    def test_candidate_claim_key_contract_roundtrips_via_meta(self) -> None:
        candidate = MemoryCandidate(
            candidate_id="c-claim-1",
            session_id="s1",
            proposed_scope="agent:test",
            type="fact",
            content="Use structured trust keys.",
            claim_key="pref:lint",
            polarity="negates",
            source_class="llm_extracted",
        )

        self.store.candidate_put(candidate)

        retrieved = self.store.candidate_get("c-claim-1")
        assert retrieved is not None
        self.assertEqual(retrieved.claim_key, "pref:lint")
        self.assertEqual(retrieved.polarity, "negates")
        self.assertEqual(retrieved.source_class, "llm_extracted")
        self.assertEqual(retrieved.meta["claim_key"], "pref:lint")
        self.assertEqual(retrieved.meta["polarity"], "negates")
        self.assertEqual(retrieved.meta["source_class"], "llm_extracted")

    def test_promote_candidate(self):
        c1 = MemoryCandidate(
            candidate_id="c1",
            session_id="s1",
            proposed_scope="session:global",
            type="fact",
            content="fact 1",
            status="approved",
            evidence_refs=[_artifact_ref(_VALID_REF_A), _non_artifact_ref()],
        )
        self.store.candidate_put(c1)
        self.artifactctl.calls.clear()

        record = self.store.promote_candidate("c1", "session:global")
        self.assertEqual(record.scope, "session:global")
        self.assertEqual(record.content, "fact 1")

        # The promoted record should now be queryable by id.
        get_record = self.store.get(record.id)
        self.assertIsNotNone(get_record)
        self.assertEqual(get_record.id, record.id)
        self.assertEqual(get_record.content, "fact 1")
        self.assertEqual(
            self.artifactctl.calls,
            [
                ("add", "memory", record.id, _VALID_REF_A),
                ("remove", "memory", "c1", _VALID_REF_A),
            ],
        )
        self.assertNotIn(("memory", "c1", _VALID_REF_A), self.artifactctl.active_refs)
        self.assertIn(("memory", record.id, _VALID_REF_A), self.artifactctl.active_refs)

    def test_promote_candidate_key_collision_rehomes_superseded_record_edges(self):
        existing = MemoryRecord(
            id="old-rec",
            scope="global:all",
            type="fact",
            key="shared-key",
            content="old fact",
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
            evidence_refs=[_artifact_ref(_VALID_REF_B)],
        )
        self.store.put(existing)
        self.artifactctl.calls.clear()

        candidate = MemoryCandidate(
            candidate_id="c2",
            session_id="s1",
            proposed_scope="session:s1",
            type="fact",
            content="new fact",
            status="approved",
            key="shared-key",
            evidence_refs=[_artifact_ref(_VALID_REF_C)],
        )
        self.store.candidate_put(candidate)
        self.artifactctl.calls.clear()

        record = self.store.promote_candidate("c2", "global:all")

        self.assertEqual(
            self.artifactctl.calls,
            [
                ("add", "memory", record.id, _VALID_REF_C),
                ("remove", "memory", "old-rec", _VALID_REF_B),
                ("remove", "memory", "c2", _VALID_REF_C),
            ],
        )
        self.assertNotIn(
            ("memory", "old-rec", _VALID_REF_B), self.artifactctl.active_refs
        )
        self.assertNotIn(("memory", "c2", _VALID_REF_C), self.artifactctl.active_refs)
        self.assertIn(("memory", record.id, _VALID_REF_C), self.artifactctl.active_refs)
