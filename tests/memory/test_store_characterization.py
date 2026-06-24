from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
    RecordOrder,
    SearchQueryOptions,
)
from openminion.modules.memory.storage.sqlite import store as store_module
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


# Section 1 — Public-surface pinning (module-level)


class StoreModulePublicSurfaceTests(unittest.TestCase):
    def test_sqlite_memory_store_class_is_exported(self) -> None:
        self.assertTrue(hasattr(store_module, "SQLiteMemoryStore"))
        self.assertTrue(inspect.isclass(store_module.SQLiteMemoryStore))

    def test_clamp01_helper_is_present(self) -> None:
        self.assertTrue(hasattr(store_module, "_clamp01"))
        self.assertEqual(store_module._clamp01(-0.5), 0.0)
        self.assertEqual(store_module._clamp01(1.5), 1.0)
        self.assertEqual(store_module._clamp01(0.25), 0.25)


# Section 2 — SQLiteMemoryStore method-surface pinning


EXPECTED_METHODS: tuple[str, ...] = (
    # Construction + private helpers
    "__init__",
    "_connect",
    "_resolve_artifactctl",
    "_add_artifact_refs",
    "_remove_artifact_refs",
    # Record CRUD
    "put",
    "upsert",
    "get",
    "delete",
    "invalidate",
    "tombstone",
    # Listing + retrieval
    "list",
    "list_scopes",
    "touch_last_hit",
    "apply_outcome_feedback",
    "search",
    "retrieve_by_entities",
    "list_records_by_goal_id",
    # Tiering
    "transition_tier",
    "list_tier_transitions",
    "put_tier_transition",
    # Relations
    "put_relation",
    "list_relations",
    "get_related_records",
    # Candidates
    "candidate_put",
    "candidate_get",
    "candidate_delete",
    "candidate_list",
    "candidate_update",
    "promote_candidate",
    # Version history
    "history",
    "supersede_by_contradiction",
)


class StoreMethodSurfaceTests(unittest.TestCase):
    def test_every_expected_method_is_present(self) -> None:
        for name in EXPECTED_METHODS:
            with self.subTest(method=name):
                self.assertTrue(
                    hasattr(SQLiteMemoryStore, name),
                    f"SQLiteMemoryStore lost method `{name}` — split regression.",
                )
                attr = getattr(SQLiteMemoryStore, name)
                self.assertTrue(
                    callable(attr) or isinstance(attr, staticmethod),
                    f"`{name}` is no longer callable.",
                )

    def test_row_decoder_staticmethods_remain_bound(self) -> None:
        for name in (
            "_decode_evidence_ref_values",
            "_create_record_from_row",
            "_create_relation_from_row",
            "_create_candidate_from_row",
            "_create_tier_transition_from_row",
        ):
            with self.subTest(member=name):
                self.assertTrue(
                    hasattr(SQLiteMemoryStore, name),
                    f"row-decoder binding `{name}` missing.",
                )

    def test_init_signature_pinned(self) -> None:
        sig = inspect.signature(SQLiteMemoryStore.__init__)
        # First positional parameter (after self) must be db_path-shaped.
        params = list(sig.parameters.values())
        self.assertGreaterEqual(len(params), 2, "lost db_path positional.")
        self.assertEqual(params[0].name, "self")
        # Second param's name is the canonical entry point — pin to whatever
        # the current rev exposes; this catches a rename to e.g. `path` or
        # `database` which would break callers.
        self.assertIn(
            params[1].name,
            ("db_path", "path", "database"),
            f"first positional arg renamed to `{params[1].name}` — review.",
        )


# Section 3 — End-to-end smoke (lightweight)


class StoreSmokeTests(unittest.TestCase):
    def test_construct_and_round_trip(self) -> None:
        from openminion.modules.memory.models import MemoryRecord
        import datetime

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "store.db"
            store = SQLiteMemoryStore(db)
            self.assertTrue(db.exists())
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            record = MemoryRecord(
                id="r1",
                scope="session:smoke",
                type="fact",
                key=None,
                title=None,
                content="hello",
                tags=[],
                entities=[],
                created_at=now,
                updated_at=now,
            )
            rid = store.put(record)
            self.assertEqual(rid, "r1")
            roundtrip = store.get("r1")
            self.assertIsNotNone(roundtrip)
            assert roundtrip is not None  # for type-narrower
            self.assertEqual(roundtrip.id, "r1")
            self.assertEqual(roundtrip.scope, "session:smoke")

    def test_list_scopes_returns_inserted_scopes(self) -> None:
        from openminion.modules.memory.models import MemoryRecord
        import datetime

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "store.db")
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            for i, scope in enumerate(("session:a", "session:b")):
                store.put(
                    MemoryRecord(
                        id=f"r{i}",
                        scope=scope,
                        type="fact",
                        key=None,
                        title=None,
                        content=f"text-{i}",
                        tags=[],
                        entities=[],
                        created_at=now,
                        updated_at=now,
                    )
                )
            scopes = store.list_scopes()
            self.assertIn("session:a", scopes)
            self.assertIn("session:b", scopes)

    def test_list_with_options_returns_records(self) -> None:
        from openminion.modules.memory.models import MemoryRecord
        import datetime

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "store.db")
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            store.put(
                MemoryRecord(
                    id="r1",
                    scope="session:list",
                    type="fact",
                    key=None,
                    title=None,
                    content="alpha",
                    tags=[],
                    entities=[],
                    created_at=now,
                    updated_at=now,
                )
            )
            opts = ListQueryOptions(
                scopes=["session:list"],
                order_by=RecordOrder.UPDATED_AT_DESC,
            )
            results = store.list(opts)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].id, "r1")

    def test_search_options_dataclass_accepts_query(self) -> None:
        opts = SearchQueryOptions(query="hello", scopes=["session:x"])
        self.assertEqual(opts.query, "hello")
        self.assertEqual(opts.scopes, ["session:x"])

    def test_candidate_options_dataclass_shape(self) -> None:
        opts = CandidateListOptions(session_id="s1")
        self.assertEqual(opts.session_id, "s1")
        self.assertIsNone(opts.status)
