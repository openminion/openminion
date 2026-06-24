from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


class _StoreTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.store = SQLiteSessionStore(self._tmp.name)
        self.session_id = self.store.create_session(title="test-session")

    def tearDown(self):
        self.store.close()
        os.unlink(self._tmp.name)
        storage_dir = Path(self._tmp.name).parent / "storage"
        if storage_dir.exists():
            import shutil

            shutil.rmtree(storage_dir, ignore_errors=True)


class TestV15PromptContextLifecycle(_StoreTestBase):
    def test_create_prompt_context(self):
        pc_id = self.store.create_prompt_context(self.session_id)
        self.assertTrue(pc_id)
        active = self.store.get_active_prompt_context(self.session_id)
        self.assertIsNotNone(active)
        self.assertEqual(active["prompt_context_id"], pc_id)
        self.assertEqual(active["status"], "active")

    def test_create_closes_previous(self):
        pc1 = self.store.create_prompt_context(self.session_id)
        pc2 = self.store.create_prompt_context(self.session_id)
        active = self.store.get_active_prompt_context(self.session_id)
        self.assertEqual(active["prompt_context_id"], pc2)
        self.assertNotEqual(pc1, pc2)

    def test_close_prompt_context(self):
        pc_id = self.store.create_prompt_context(self.session_id)
        self.store.close_prompt_context(pc_id, rollover_reason="token_pressure")
        active = self.store.get_active_prompt_context(self.session_id)
        self.assertIsNone(active)

    def test_no_active_context_returns_none(self):
        active = self.store.get_active_prompt_context(self.session_id)
        self.assertIsNone(active)


class TestV15CompressionCheckpoints(_StoreTestBase):
    def test_save_and_get_checkpoint(self):
        bundle = {"bundle_id": "b1", "summary_text": "test", "tiers": []}
        cp_id = self.store.save_compression_checkpoint(
            self.session_id, json.dumps(bundle), reason="manual"
        )
        self.assertTrue(cp_id)
        latest = self.store.get_latest_checkpoint(self.session_id)
        self.assertIsNotNone(latest)
        self.assertEqual(latest["checkpoint_id"], cp_id)
        self.assertEqual(latest["reason"], "manual")
        stored_bundle = json.loads(latest["bundle_json"])
        self.assertEqual(stored_bundle["bundle_id"], "b1")

    def test_no_checkpoint_returns_none(self):
        latest = self.store.get_latest_checkpoint(self.session_id)
        self.assertIsNone(latest)


class TestV15SeedBundles(_StoreTestBase):
    def test_save_and_get_seed(self):
        sections = [{"section_type": "summary", "text": "hello", "token_count": 1}]
        seed_id = self.store.save_seed_bundle(
            self.session_id, "bundle-1", json.dumps(sections), 1
        )
        self.assertTrue(seed_id)
        latest = self.store.get_latest_seed_bundle(self.session_id)
        self.assertIsNotNone(latest)
        self.assertEqual(latest["seed_id"], seed_id)
        self.assertEqual(latest["total_tokens"], 1)
        self.assertEqual(len(latest["sections"]), 1)

    def test_no_seed_returns_none(self):
        latest = self.store.get_latest_seed_bundle(self.session_id)
        self.assertIsNone(latest)


class TestV15RunRecords(_StoreTestBase):
    def test_create_and_finish_run(self):
        run_id = self.store.create_run_record(self.session_id, "llm", model_id="gpt-4")
        self.assertTrue(run_id)
        self.store.finish_run_record(run_id, input_tokens=100, output_tokens=50)

    def test_create_run_with_prompt_context(self):
        pc_id = self.store.create_prompt_context(self.session_id)
        run_id = self.store.create_run_record(
            self.session_id, "llm", prompt_context_id=pc_id
        )
        self.assertTrue(run_id)


class TestV15MessageRefs(_StoreTestBase):
    def test_add_message_ref(self):
        ref_id = self.store.add_message_ref(
            self.session_id, "user", content_inline="Hello"
        )
        self.assertTrue(ref_id)

    def test_message_ref_auto_increments_seq(self):
        r1 = self.store.add_message_ref(self.session_id, "user", content_inline="A")
        r2 = self.store.add_message_ref(
            self.session_id, "assistant", content_inline="B"
        )
        self.assertNotEqual(r1, r2)


class TestV15DerivedViews(_StoreTestBase):
    def test_update_derived_views_empty(self):
        result = self.store.update_derived_views(self.session_id)
        self.assertEqual(result["events_processed"], 0)

    def test_update_derived_views_with_events(self):
        self.store.append_event(
            session_id=self.session_id,
            event_type="task.created",
            payload={"description": "Build widget"},
            actor_type="user",
        )
        self.store.append_event(
            session_id=self.session_id,
            event_type="turn.user",
            payload={"text": "hello"},
            actor_type="user",
        )
        result = self.store.update_derived_views(self.session_id)
        self.assertEqual(result["events_processed"], 2)
        self.assertIn("Build widget", result["open_tasks"])


class TestV15GetSliceV15(_StoreTestBase):
    def test_get_slice_includes_context(self):
        # Populate basic data
        self.store.append_event(
            session_id=self.session_id,
            event_type="turn.user",
            payload={"text": "hello"},
            actor_type="user",
        )
        pc_id = self.store.create_prompt_context(self.session_id)
        result = self.store.get_slice(self.session_id, "act")
        self.assertEqual(result["prompt_context_id"], pc_id)
        self.assertIn("session_id", result)


class TestV15ManifestEnforcement(_StoreTestBase):
    def test_enforce_valid_manifest(self):
        pc_id = self.store.create_prompt_context(self.session_id)
        manifest = {
            "prompt_context_id": pc_id,
            "included_segment_ids": ["s1", "s2"],
            "dropped_segment_ids": [],
        }
        result = self.store.enforce_context_manifest(self.session_id, manifest)
        self.assertTrue(result["valid"])
        self.assertEqual(result["included_segments"], 2)

    def test_enforce_mismatched_manifest(self):
        self.store.create_prompt_context(self.session_id)
        manifest = {"prompt_context_id": "wrong-id"}
        result = self.store.enforce_context_manifest(self.session_id, manifest)
        self.assertFalse(result["valid"])
        self.assertGreater(len(result["warnings"]), 0)


class TestV15CanonicalEvents(_StoreTestBase):
    def test_emit_known_event(self):
        eid = self.store.emit_canonical_event(
            self.session_id, "llm.request", {"model": "gpt-4"}
        )
        self.assertTrue(eid)

    def test_emit_unknown_event_adds_warning(self):
        eid = self.store.emit_canonical_event(
            self.session_id, "custom.unknown", {"data": "test"}
        )
        self.assertTrue(eid)


class TestV15ReplayResume(_StoreTestBase):
    def test_get_replay_events(self):
        self.store.append_event(
            session_id=self.session_id,
            event_type="turn.user",
            payload={"text": "hello"},
            actor_type="user",
        )
        self.store.append_event(
            session_id=self.session_id,
            event_type="turn.assistant",
            payload={"text": "hi"},
            actor_type="assistant",
        )
        events = self.store.get_replay_events(self.session_id)
        self.assertEqual(len(events), 2)

    def test_get_replay_events_filters_by_type(self):
        self.store.append_event(
            session_id=self.session_id,
            event_type="turn.user",
            payload={},
            actor_type="user",
        )
        self.store.append_event(
            session_id=self.session_id,
            event_type="tool.completed",
            payload={},
            actor_type="system",
        )
        events = self.store.get_replay_events(
            self.session_id, event_types=["turn.user"]
        )
        self.assertEqual(len(events), 1)

    def test_get_resume_state(self):
        state = self.store.get_resume_state(self.session_id)
        self.assertEqual(state["session_id"], self.session_id)
        self.assertIn("prompt_context", state)
        self.assertIn("latest_checkpoint", state)

    def test_get_resume_state_includes_clarify_order_and_cursor_keys(self):
        self.store.put_working_state(
            self.session_id,
            state_inline={
                "phase": "CLARIFY",
                "cursor": 2,
                "trace_id": "trace-clarify",
                "status": "waiting_user",
                "unresolved_clarify_items": [{"id": "q1", "question": "Where?"}],
                "clarify_responses": {"q0": "prior answer"},
            },
        )
        self.store.append_event(
            session_id=self.session_id,
            event_type="brain.clarify.requested",
            payload={"clarify_id": "clar-1"},
            actor_type="assistant",
            trace_id="trace-clarify",
        )
        self.store.append_event(
            session_id=self.session_id,
            event_type="brain.clarify.answered",
            payload={"clarify_id": "clar-1", "question_id": "q1"},
            actor_type="user",
            trace_id="trace-clarify",
        )

        resume = self.store.get_resume_state(self.session_id)
        self.assertIn("resume_keys", resume)
        self.assertEqual(resume["resume_keys"]["phase"], "CLARIFY")
        self.assertEqual(resume["resume_keys"]["cursor"], 2)
        self.assertEqual(resume["resume_keys"]["trace_id"], "trace-clarify")
        self.assertEqual(resume["resume_keys"]["unresolved_clarify_count"], 1)
        self.assertEqual(resume["resume_keys"]["clarify_response_count"], 1)
        self.assertEqual(
            [e["event_type"] for e in resume["clarify_events"]],
            ["brain.clarify.requested", "brain.clarify.answered"],
        )


class TestV15Backfill(_StoreTestBase):
    def test_backfill_events(self):
        events = [
            {"event_type": "turn.user", "payload": {"text": "hello"}},
            {"event_type": "turn.assistant", "payload": {"text": "hi"}},
            {},  # should be skipped (no event_type)
        ]
        result = self.store.backfill_events(self.session_id, events)
        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["total"], 3)
