import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openminion.modules.storage.runtime import SessionStore as RuntimeSessionStore
from openminion.modules.storage.runtime.context import build_runtime_storage
from openminion.modules.storage.runtime.migrations import (
    DEFAULT_MIGRATIONS,
    MigrationError,
    migrate_database,
)
from openminion.modules.storage.runtime.pinned_context import (
    PinnedContextEntry,
    PinnedContextPolicy,
)
from openminion.modules.storage.runtime.session_store import (
    EventRecord,
    MessageRecord,
    RoomParticipant,
    SessionContextRecord,
    SessionRecord,
    SessionStore,
    build_session_key,
)
from openminion.modules.storage.runtime.session_store import run_facts
from openminion.modules.storage.runtime.sqlite import connect_database
from tests._room_participant_scenarios import (
    check_blank_active_agent_counts_as_none,
    check_concurrent_joins,
    check_explicit_active_agent,
    check_join_keeps_an_activation_committed_during_it,
    check_join_place_on_upsert_and_rejoin,
    check_joins_selection_and_leaves_on_two_connections,
    check_leave_and_rejoin_wait_for_each_other,
    check_leave_during_a_join_applies_after_it,
    check_leave_during_a_selection_applies_after_it,
    check_joins_write_no_session_value,
    check_legacy_owner_join_order,
    check_legacy_owner_records_activate_no_agent,
    check_legacy_session_selection,
    check_selection_during_a_join_applies_after_it,
    check_selection_during_a_leave_is_refused,
    check_tied_join_order,
)
from tests._run_status_scenarios import (
    check_event_facts,
    check_fact_writes,
    check_upgraded_history,
    legacy_event_rows,
    recorded_backfill_migrations,
    stored_events,
)
from openminion.modules.storage.runtime.session_store.models import (
    RuntimeSessionStoreIntegrityError,
)

EVENT_CLOCK = "openminion.modules.storage.runtime.session_store.lifecycle.utc_now_iso"
MESSAGE_CLOCK = "openminion.modules.storage.runtime.session_store.messages.utc_now_iso"
MESSAGE_UUID = "openminion.modules.storage.runtime.session_store.messages.uuid4"
TIED_AT = "2026-10-22T01:00:00+00:00"
# Each message id sorts before the id of the message appended before it.
DESCENDING_IDS = tuple(char * 32 for char in "fc80")


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database_path = Path(self._tmp.name) / "state" / "openminion.db"
        migrate_database(self.database_path)
        self.connection = connect_database(self.database_path)
        self.store = SessionStore(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self._tmp.cleanup()

    def test_public_import_contract_is_stable(self) -> None:
        self.assertIs(RuntimeSessionStore, SessionStore)
        self.assertEqual(
            build_session_key(agent_id="Agent", channel="Console", target="Room"),
            "agent:agent|channel:console|target:room",
        )

    def test_resolve_session_is_deterministic_for_canonical_key(self) -> None:
        first = self.store.resolve_session(
            agent_id="Main", channel=" Console ", target=" Team A "
        )
        second = self.store.resolve_session(
            agent_id="main", channel="console", target="team a"
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.channel, "console")
        self.assertEqual(first.target, "team a")
        self.assertEqual(first.status, "active")
        self.assertIsNone(first.closed_at)
        self.assertIsNone(first.expires_at)
        self.assertTrue(first.last_activity_at)

    def test_explicit_session_id_requires_matching_surface(self) -> None:
        session = self.store.resolve_session(
            agent_id="main",
            channel="console",
            target="team-a",
            session_id="manual-session-1",
        )

        self.assertEqual(session.id, "manual-session-1")
        self.assertIn("agent:main|", session.session_key)
        with self.assertRaisesRegex(ValueError, "belongs to target"):
            self.store.resolve_session(
                agent_id="main",
                channel="console",
                target="team-b",
                session_id="manual-session-1",
            )

    def test_explicit_session_id_rejects_cross_agent_resume(self) -> None:
        session = self.store.resolve_session(
            agent_id="agent-a",
            channel="console",
            target="team-a",
            session_id="manual-session-2",
        )

        with self.assertRaises(ValueError):
            self.store.resolve_session(
                agent_id="agent-b",
                channel="console",
                target="team-a",
                session_id=session.id,
            )

    def test_explicit_session_id_skips_agent_validation_for_operator_override(
        self,
    ) -> None:
        session = self.store.resolve_session(
            agent_id="agent-a",
            channel="console",
            target="team-a",
            session_id="manual-session-3",
        )

        resolved = self.store.resolve_session(
            agent_id="",
            channel="console",
            target="team-a",
            session_id=session.id,
        )

        self.assertEqual(resolved.id, session.id)

    def test_explicit_sessions_can_create_multiple_unique_records_for_same_lane(
        self,
    ) -> None:
        first = self.store.resolve_session(
            agent_id="agent-a",
            channel="console",
            target="team-a",
            session_id="explicit-1",
        )
        second = self.store.resolve_session(
            agent_id="agent-a",
            channel="console",
            target="team-a",
            session_id="explicit-2",
        )

        self.assertNotEqual(first.id, second.id)
        self.assertNotEqual(first.session_key, second.session_key)
        self.assertIn("|session:explicit-1", first.session_key)
        self.assertIn("|session:explicit-2", second.session_key)

    def test_deterministic_resolution_across_restart(self) -> None:
        created = self.store.resolve_session(
            agent_id="ops", channel="console", target="owner"
        )
        self.connection.close()

        reopened = connect_database(self.database_path)
        try:
            store = SessionStore(reopened)
            resolved = store.resolve_session(
                agent_id="OPS", channel="CONSOLE", target="OWNER"
            )
            self.assertEqual(created.id, resolved.id)
        finally:
            reopened.close()
            self.connection = connect_database(self.database_path)
            self.store = SessionStore(self.connection)

    def test_append_and_list_messages(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        first = self.store.append_message(
            session_id=session.id,
            role="inbound",
            body="hello",
            metadata={"source": "user"},
        )
        second = self.store.append_message(
            session_id=session.id,
            role="outbound",
            body="hi there",
            metadata={"source": "agent"},
        )

        messages = self.store.list_messages(session_id=session.id, limit=20)
        self.assertEqual([item.id for item in messages], [first.id, second.id])
        self.assertEqual(messages[0].metadata["source"], "user")
        self.assertEqual(messages[1].metadata["source"], "agent")

    def test_append_and_list_events(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        first = self.store.append_event(
            session_id=session.id,
            event_type="run_started",
            payload={"run_id": "r1"},
        )
        second = self.store.append_event(
            session_id=session.id,
            event_type="run_completed",
            payload={"run_id": "r1"},
        )

        events = self.store.list_events(session_id=session.id, limit=20)
        self.assertEqual([item.id for item in events], [first.id, second.id])
        self.assertEqual(events[0].payload["run_id"], "r1")
        self.assertEqual(events[1].event_type, "run_completed")
        self.assertIsNone(events[0].canonical_event_id)

    def test_runtime_event_migration_adds_canonical_ids_and_capture_holds(
        self,
    ) -> None:
        event_columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(events)").fetchall()
        }
        hold_table = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("session_retention_holds",),
        ).fetchone()
        migration = self.connection.execute(
            "SELECT name FROM migrations WHERE version = 11"
        ).fetchone()

        self.assertIn("canonical_event_id", event_columns)
        self.assertIsNotNone(hold_table)
        self.assertEqual(
            str(migration["name"]),
            "add_canonical_events_and_capture_holds",
        )

    def test_exact_event_lookup_uses_stable_canonical_id(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="canonical-event"
        )
        created = self.store.append_event(
            session_id=session.id,
            canonical_event_id="turn.outcome.v1:root-1",
            event_type="turn.outcome",
            payload={"capture_id": "capture-1", "capture_state": "excluded"},
        )

        exact = self.store.get_event_by_canonical_id("turn.outcome.v1:root-1")
        listed = self.store.list_events(session_id=session.id, limit=10)

        self.assertEqual(exact, created)
        self.assertEqual(listed, [created])
        self.assertEqual(created.canonical_event_id, "turn.outcome.v1:root-1")
        self.assertIsNone(self.store.get_event_by_canonical_id("missing-event"))

    def test_terminal_turn_commit_is_atomic_idempotent_and_conflict_safe(
        self,
    ) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="terminal-capture"
        )
        payload = {
            "capture_id": "capture-1",
            "capture_state": "pending",
            "root_turn_id": "root-1",
        }

        first = self.store.commit_terminal_capture_intent(
            session_id=session.id,
            canonical_event_id="turn.outcome.v1:root-1",
            capture_id="capture-1",
            payload=payload,
            payload_hash="payload-sha256",
        )
        replay = self.store.commit_terminal_capture_intent(
            session_id=session.id,
            canonical_event_id="turn.outcome.v1:root-1",
            capture_id="capture-1",
            payload=dict(reversed(tuple(payload.items()))),
            payload_hash="payload-sha256",
        )

        self.assertEqual(replay, first)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM session_retention_holds WHERE released_at IS NULL"
            ).fetchone()[0],
            1,
        )

        with self.assertRaises(RuntimeSessionStoreIntegrityError) as raised:
            self.store.commit_terminal_capture_intent(
                session_id=session.id,
                canonical_event_id="turn.outcome.v1:root-1",
                capture_id="capture-1",
                payload={**payload, "capture_state": "rejected"},
                payload_hash="conflicting-payload-sha256",
            )
        self.assertEqual(
            raised.exception.code,
            "SESSION_STORE_INTEGRITY_CONFLICT",
        )
        with self.assertRaises(RuntimeSessionStoreIntegrityError):
            self.store.commit_terminal_capture_intent(
                session_id=session.id,
                canonical_event_id="turn.outcome.v1:root-1",
                capture_id="different-capture",
                payload=payload,
                payload_hash="payload-sha256",
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM session_retention_holds"
            ).fetchone()[0],
            1,
        )

    def test_terminal_turn_commit_rolls_back_event_when_hold_insert_fails(
        self,
    ) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="capture-rollback"
        )
        self.connection.execute(
            """
            CREATE TRIGGER reject_capture_hold
            BEFORE INSERT ON session_retention_holds
            BEGIN
              SELECT RAISE(ABORT, 'capture hold rejected');
            END
            """
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.store.commit_terminal_capture_intent(
                session_id=session.id,
                canonical_event_id="turn.outcome.v1:rollback",
                capture_id="capture-rollback",
                payload={"capture_id": "capture-rollback", "capture_state": "pending"},
                payload_hash="payload-sha256",
            )

        self.assertIsNone(
            self.store.get_event_by_canonical_id("turn.outcome.v1:rollback")
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM session_retention_holds"
            ).fetchone()[0],
            0,
        )

    def test_capture_result_and_hold_release_are_atomic_and_idempotent(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="capture-result"
        )
        self.store.commit_terminal_capture_intent(
            session_id=session.id,
            canonical_event_id="turn.outcome.v1:result-root",
            capture_id="capture-result",
            payload={"capture_id": "capture-result", "capture_state": "pending"},
            payload_hash="payload-sha256",
        )
        result_payload = {
            "capture_id": "capture-result",
            "capture_state": "processed",
            "result_hash": "result-sha256",
        }

        first = self.store.commit_capture_result_and_release_hold(
            session_id=session.id,
            canonical_event_id="memory.capture.result.v1:capture-result",
            capture_id="capture-result",
            payload=result_payload,
        )
        replay = self.store.commit_capture_result_and_release_hold(
            session_id=session.id,
            canonical_event_id="memory.capture.result.v1:capture-result",
            capture_id="capture-result",
            payload=result_payload,
        )

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.event, first.event)
        assert first.retention_hold is not None
        self.assertIsNotNone(first.retention_hold.released_at)
        with self.assertRaises(RuntimeSessionStoreIntegrityError):
            self.store.commit_capture_result_and_release_hold(
                session_id=session.id,
                canonical_event_id="memory.capture.result.v1:capture-result",
                capture_id="capture-result",
                payload={**result_payload, "capture_state": "rejected"},
            )

        with self.assertRaises(RuntimeSessionStoreIntegrityError):
            self.store.commit_capture_result_and_release_hold(
                session_id=session.id,
                canonical_event_id="memory.capture.result.v1:second-result",
                capture_id="capture-result",
                payload=result_payload,
            )
        self.assertIsNone(
            self.store.get_event_by_canonical_id(
                "memory.capture.result.v1:second-result"
            )
        )

    def test_list_events_supports_prefix_and_descending_order(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        self.store.append_event(
            session_id=session.id,
            event_type="run.queued",
            payload={"run_id": "r1", "state": "queued"},
        )
        self.store.append_event(
            session_id=session.id,
            event_type="run.completed",
            payload={"run_id": "r1", "state": "completed"},
        )
        self.store.append_event(
            session_id=session.id,
            event_type="auth.denied",
            payload={"scope": "turn.execute"},
        )

        run_events = self.store.list_events(
            session_id=session.id,
            limit=10,
            event_type_prefix="run.",
            newest_first=True,
        )
        self.assertEqual(len(run_events), 2)
        self.assertEqual(run_events[0].event_type, "run.completed")
        self.assertEqual(run_events[1].event_type, "run.queued")

    def test_list_events_follows_insertion_order_when_clock_steps_back(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="clock-step-back"
        )
        # The wall clock steps back after the second and the fourth append.
        stamps = [
            "2026-10-21T17:16:59.564721+00:00",
            "2026-10-21T17:16:59.602657+00:00",
            "2026-10-21T17:16:58.715677+00:00",
            "2026-10-21T17:16:58.900000+00:00",
            "2026-10-21T17:16:58.800000+00:00",
        ]
        with patch(EVENT_CLOCK, side_effect=stamps):
            ids = [
                self.store.append_event(
                    session_id=session.id, event_type=f"step.{index}"
                ).id
                for index in range(len(stamps))
            ]

        ascending = self.store.list_events(session_id=session.id, limit=10)
        descending = self.store.list_events(
            session_id=session.id, limit=10, newest_first=True
        )
        oldest_two = self.store.list_events(session_id=session.id, limit=2)
        newest_two = self.store.list_events(
            session_id=session.id, limit=2, newest_first=True
        )
        high_water = self.store.event_high_water(session_id=session.id)
        after_id_page = self.store.list_events_after_id(
            session_id=session.id, after_id=0, high_water_id=high_water
        )
        before_id_page = self.store.list_events_before_id(
            session_id=session.id, before_id=high_water + 1, limit=10
        )

        self.assertEqual([event.id for event in ascending], ids)
        self.assertEqual([event.created_at for event in ascending], stamps)
        self.assertEqual([event.id for event in descending], ids[::-1])
        self.assertEqual([event.id for event in oldest_two], ids[:2])
        self.assertEqual([event.id for event in newest_two], ids[::-1][:2])
        self.assertEqual(newest_two[0].id, high_water)
        self.assertEqual(after_id_page, ascending)
        self.assertEqual(before_id_page, descending)

    def test_list_events_prefix_follows_insertion_order_when_clock_steps_back(
        self,
    ) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="clock-step-back-prefix"
        )
        entries = (
            ("run.queued", "2026-10-21T17:16:59.564721+00:00"),
            ("tool.call", "2026-10-21T17:16:59.580000+00:00"),
            ("run.running", "2026-10-21T17:16:59.602657+00:00"),
            ("run.failed", "2026-10-21T17:16:58.715677+00:00"),
            ("tool.result", "2026-10-21T17:16:58.720000+00:00"),
        )
        with patch(EVENT_CLOCK, side_effect=[stamp for _, stamp in entries]):
            appended = {
                event_type: self.store.append_event(
                    session_id=session.id, event_type=event_type
                )
                for event_type, _ in entries
            }
        run_ids = [
            appended[event_type].id
            for event_type in ("run.queued", "run.running", "run.failed")
        ]

        run_events = self.store.list_events(
            session_id=session.id, limit=10, event_type_prefix="run."
        )
        newest_run = self.store.list_events(
            session_id=session.id,
            limit=1,
            newest_first=True,
            event_type_prefix="run.",
        )
        oldest_runs = self.store.list_events(
            session_id=session.id, limit=2, event_type_prefix="run."
        )

        self.assertEqual([event.id for event in run_events], run_ids)
        self.assertEqual([event.id for event in newest_run], run_ids[-1:])
        self.assertEqual([event.id for event in oldest_runs], run_ids[:2])

    def _append(self, session_id: str, event_type: str, **payload) -> int:
        return self.store.append_event(
            session_id=session_id, event_type=event_type, payload=payload
        ).id

    def test_list_run_events_matches_the_stored_top_level_run_id(self) -> None:
        session, other = (
            self.store.resolve_session(agent_id="main", channel="console", target=t)
            for t in ("run-events", "run-events-other")
        )
        special = 'q"u\\o%t_e café'
        r1 = [self._append(session.id, "run.queued", run_id="r1")]
        self._append(session.id, "tool.call", run_id="r1")
        self._append(other.id, "run.queued", run_id="r1")
        self._append(session.id, "run.queued", run_id="r2", parent={"run_id": "r1"})
        self._append(session.id, "run.queued", run_id="R1")
        r1.append(self._append(session.id, "run.running", run_id="r1"))
        self._append(session.id, "run.queued", run_id="r1x")
        quoted = self._append(session.id, "run.queued", run_id=special)
        r1.append(self._append(session.id, "run.completed", run_id="r1"))

        def listed(run_id: str, limit: int = 10) -> list[int]:
            return [
                event.id
                for event in self.store.list_run_events(
                    session_id=session.id, run_id=run_id, limit=limit
                )
            ]

        self.assertEqual(listed("r1"), r1[::-1])
        self.assertEqual(listed("r1", limit=2), r1[::-1][:2])
        self.assertEqual(listed(special), [quoted])
        self.assertEqual(listed("r9"), [])
        self.assertEqual(listed("r1", limit=0), [])

    def test_summarize_recent_runs_reports_whole_runs_seen_in_the_window(
        self,
    ) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="run-summary"
        )
        stamps = [f"2026-10-23T10:00:0{second}.000001+00:00" for second in range(9)]
        with patch(EVENT_CLOCK, side_effect=stamps):
            ids = [
                self._append(session.id, event_type, **payload)
                for event_type, payload in (
                    ("run.queued", {"run_id": "a", "step": "accepted"}),
                    ("run.queued", {"run_id": "old", "state": "queued"}),
                    # The payload state, when it is a run state, decides.
                    ("run.queued", {"run_id": "a", "state": "failed", "error": 5}),
                    ("run.failed", {"run_id": "a", "state": " RUNNING ", "step": " "}),
                    ("run.completed", {"run_id": "b", "state": "bogus"}),
                    ("run.running", {"run_id": "a", "error": None}),
                    ("tool.call", {"run_id": "a"}),
                    ("run.running", {"step": "no run id"}),
                    ("run.cancel_requested", {"run_id": "b"}),
                )
            ]

        def summary(scan_limit: int) -> dict[str, dict]:
            return {
                row["run_id"]: row
                for row in self.store.summarize_recent_runs(
                    session_id=session.id,
                    scan_limit=scan_limit,
                    terminal_states=["completed", "failed"],
                )
            }

        # The newest 3 `run.` events are ids[5], ids[7] (no run id) and ids[8].
        self.assertEqual(sorted(summary(3)), ["a", "b"])
        self.assertEqual(
            summary(3)["a"],
            {
                "run_id": "a",
                "event_count": 4,
                "started_at": stamps[0],
                "last_id": ids[5],
                "step_id": ids[0],
                "error_id": ids[2],
                "terminal_id": ids[2],
            },
        )
        self.assertEqual(
            {key: summary(3)["b"][key] for key in ("last_id", "terminal_id")},
            {"last_id": ids[8], "terminal_id": ids[4]},
        )
        self.assertEqual(sorted(summary(100)), ["a", "b", "old"])

    def test_summarize_thread_events_follows_the_thread_rules(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="thread-summary"
        )

        def thread(event_type: str, conversation: str | None, thread_id: str, **p):
            payload = {"thread_id": thread_id, **p}
            if conversation is not None:
                payload["conversation_id"] = conversation
            return self._append(session.id, event_type, **payload)

        start = thread("run.queued", "c", "t", run_id="r1", invocation_id="i1")
        thread("run.queued", "c", "t", run_id="r2")
        writer = thread("client.attach", "c", "t", attach_id="w", attach_role="")
        thread("client.attach", "c", "t", attach_id="o", attach_role="observer")
        thread("client.attach", "c", "t", attach_id=" ", attach_role="writer")
        shared = thread("response.acked", None, "")
        other = thread("run.running", "c", "u", run_id="r3")

        def facts(conversation_id: str, thread_id: str) -> dict:
            return self.store.summarize_thread_events(
                session_id=session.id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                start_event_type="run.queued",
                attach_event_type="client.attach",
                writer_role="writer",
                flag_event_types=["response.acked", "client.detached"],
            )

        self.assertEqual(
            facts("c", "t"),
            {
                "latest_id": writer + 2,
                "run_event_id": start + 1,
                "start_id": start,
                "attach_id": writer,
                "present": set(),
            },
        )
        # An event without a conversation is in the thread only when the
        # conversation is the session itself; without a thread, in every thread.
        self.assertEqual(facts(session.id, "t")["latest_id"], shared)
        self.assertEqual(facts(session.id, "t")["present"], {"response.acked"})
        self.assertEqual(facts("", "u")["latest_id"], other)
        self.assertEqual(facts("", "u")["run_event_id"], other)
        self.assertIsNone(facts("missing", "t")["latest_id"])

    def test_list_events_by_id_reads_the_session_rows_in_chunks(self) -> None:
        session, other = (
            self.store.resolve_session(agent_id="main", channel="console", target=t)
            for t in ("by-id", "by-id-other")
        )
        ids = [self._append(session.id, "step", index=i) for i in range(1203)]
        foreign = self._append(other.id, "step")

        events = self.store.list_events_by_id(
            session_id=session.id, event_ids=[*ids[::-1], foreign, ids[0], 0]
        )

        self.assertEqual([event.id for event in events], ids)
        self.assertEqual(
            self.store.list_events_by_id(session_id=session.id, event_ids=[]), []
        )

    def _new_session(self, target: str) -> str:
        return self.store.resolve_session(
            agent_id="main", channel="console", target=target
        ).id

    def test_event_facts_read_payload_values_as_the_run_status_readers(self) -> None:
        check_event_facts(self.store, self._new_session("event-facts"))

    def test_event_facts_follow_the_event_transaction(self) -> None:
        check_fact_writes(self.store, self._new_session)

    def _legacy_database(self, count: int) -> tuple[Path, list[str]]:
        """A database at runtime migration 12 with `count` stored events."""
        legacy_path = Path(self._tmp.name) / "legacy" / "openminion.db"
        migrate_database(
            legacy_path,
            migrations=tuple(item for item in DEFAULT_MIGRATIONS if item.version < 13),
        )
        session_ids = ["legacy-a", "legacy-b"]
        with closing(connect_database(legacy_path)) as conn:
            conn.executemany(
                "INSERT INTO sessions(id, session_key, channel, target) "
                "VALUES (?, ?, 'console', ?)",
                [(sid, f"key-{sid}", sid) for sid in session_ids],
            )
            conn.executemany(
                "INSERT INTO events(session_id, event_type, payload_json, created_at) "
                "VALUES (:session_id, :event_type, :payload_json, :created_at)",
                legacy_event_rows(session_ids, count),
            )
            conn.commit()
        return legacy_path, session_ids

    def test_event_facts_migration_backfills_existing_history(self) -> None:
        # 2,345 events: three backfill batches of up to 1,000.
        legacy_path, session_ids = self._legacy_database(2345)
        with closing(connect_database(legacy_path)) as conn:
            stored = stored_events(SessionStore(conn))

        batches: list[int] = []
        result = migrate_database(
            legacy_path, migrations=recorded_backfill_migrations(batches)
        )

        self.assertEqual(result.applied_versions, (13, 14, 15))
        self.assertEqual(batches, [1000, 1000, 345, 0])
        with closing(connect_database(legacy_path)) as conn:
            store = SessionStore(conn)
            check_upgraded_history(
                store,
                lambda target: (
                    store.resolve_session(
                        agent_id="main", channel="console", target=target
                    ).id
                ),
                session_ids=session_ids,
                stored=stored,
            )

    def test_event_facts_migration_rolls_back_when_the_backfill_fails(self) -> None:
        legacy_path, _session_ids = self._legacy_database(1500)
        with closing(connect_database(legacy_path)) as conn:
            # A payload `json.loads` cannot read, after the first batch of 1,000.
            conn.execute(
                "UPDATE events SET payload_json = ? WHERE id = 1400", ('{"run_id": ',)
            )
            conn.commit()
            stored = stored_events(SessionStore(conn))

        def assert_not_applied() -> None:
            with closing(connect_database(legacy_path)) as conn:
                self.assertEqual(
                    conn.execute("SELECT MAX(version) FROM migrations").fetchone()[0],
                    12,
                )
                self.assertIsNone(
                    conn.execute(
                        "SELECT name FROM sqlite_master WHERE name = 'event_run_facts'"
                    ).fetchone()
                )
                self.assertEqual(stored_events(SessionStore(conn)), stored)

        # Any error in the backfill, here after its first batch of facts, rolls
        # the whole migration back.
        real_facts = run_facts.event_run_facts
        derived: list[int] = []

        def interrupted(event):
            derived.append(event.id)
            if len(derived) > 1000:
                raise RuntimeError("backfill interrupted")
            return real_facts(event)

        with patch.object(run_facts, "event_run_facts", side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, "backfill interrupted"):
                migrate_database(legacy_path)
        assert_not_applied()

        with self.assertRaises(MigrationError) as raised:
            migrate_database(legacy_path)
        self.assertIn(
            "v13 (add_event_run_facts) failed: event 1400 has a payload_json that is "
            "not JSON",
            str(raised.exception),
        )
        assert_not_applied()

        # Rerunning the migration after the fix applies it whole.
        with closing(connect_database(legacy_path)) as conn:
            conn.execute("UPDATE events SET payload_json = '{}' WHERE id = 1400")
            conn.commit()
        self.assertEqual(migrate_database(legacy_path).applied_versions, (13, 14, 15))
        with closing(connect_database(legacy_path)) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM event_run_facts").fetchone()[0], 1500
            )

    def test_list_sessions_and_count_sessions(self) -> None:
        first = self.store.resolve_session(
            agent_id="main", channel="console", target="chat-a"
        )
        second = self.store.resolve_session(
            agent_id="main", channel="console", target="chat-b"
        )
        self.store.append_message(
            session_id=second.id,
            role="inbound",
            body="latest activity",
            metadata={"source": "test"},
        )

        self.assertEqual(self.store.count_sessions(), 2)
        sessions = self.store.list_sessions(limit=10, newest_first=True)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0].id, second.id)
        self.assertEqual(sessions[1].id, first.id)

    def test_list_sessions_supports_agent_status_and_channel_filters(self) -> None:
        first = self.store.resolve_session(
            agent_id="main-agent",
            channel="console",
            target="chat-a",
        )
        second = self.store.resolve_session(
            agent_id="main-agent-2",
            channel="slack",
            target="chat-b",
        )
        third = self.store.resolve_session(
            agent_id="helper",
            channel="console",
            target="chat-c",
        )
        self.store.close_session(session_id=third.id, reason="test-close")

        by_agent = self.store.list_sessions(limit=10, agent_id="main-agent")
        self.assertEqual({item.id for item in by_agent}, {first.id, second.id})

        by_status = self.store.list_sessions(limit=10, status="closed")
        self.assertEqual([item.id for item in by_status], [third.id])

        combined = self.store.list_sessions(
            limit=10,
            agent_id="main-agent",
            channel="slack",
            status="active",
        )
        self.assertEqual([item.id for item in combined], [second.id])

    def test_list_sessions_supports_target_and_metadata_filters(self) -> None:
        focus_a = self.store.resolve_session(
            agent_id="focus-agent",
            channel="console",
            target="focus",
            session_id="focus-a",
            metadata={"working_dir": "/tmp/project-a"},
        )
        focus_b = self.store.resolve_session(
            agent_id="focus-agent",
            channel="console",
            target="focus",
            session_id="focus-b",
            metadata={"working_dir": "/tmp/project-b"},
        )
        self.store.resolve_session(
            agent_id="focus-agent",
            channel="console",
            target="tui",
            session_id="tui-a",
            metadata={"working_dir": "/tmp/project-a"},
        )

        by_target = self.store.list_sessions(limit=10, target="focus")
        self.assertEqual({item.id for item in by_target}, {focus_a.id, focus_b.id})

        by_metadata = self.store.list_sessions(
            limit=10,
            target="focus",
            metadata_filter={"working_dir": "/tmp/project-a"},
        )
        self.assertEqual([item.id for item in by_metadata], [focus_a.id])

    def test_list_sessions_agent_filter_includes_room_members_before_limit(
        self,
    ) -> None:
        room = self.store.create_room(
            channel="cli",
            target="focus",
            session_id="custom-room-id",
            metadata={"working_dir": "/workspace"},
        )
        self.store.add_participant(
            session_id=room.id,
            participant_type="agent",
            participant_id="active-agent",
            channel="cli",
            role="participant",
        )
        self.store.add_participant(
            session_id=room.id,
            participant_type="agent",
            participant_id="main-agent",
            channel="cli",
            role="participant",
        )
        self.assertEqual(self.store.get_active_agent(room.id), "active-agent")
        for index in range(205):
            self.store.resolve_session(
                agent_id=f"foreign-{index}",
                channel="cli",
                target="focus",
                session_id=f"foreign-session-{index}",
                metadata={"working_dir": "/workspace"},
            )

        sessions = self.store.list_sessions(
            limit=1,
            agent_id="main-agent",
            target="focus",
            metadata_filter={"working_dir": "/workspace"},
        )

        self.assertEqual([item.id for item in sessions], [room.id])

    def test_delete_session_removes_session_messages_events_and_context(self) -> None:
        session = self.store.resolve_session(
            agent_id="main",
            channel="console",
            target="cleanup",
        )
        self.store.append_message(session_id=session.id, role="inbound", body="hello")
        self.store.append_event(
            session_id=session.id,
            event_type="session.custom",
            payload={"ok": True},
        )
        self.store.ensure_session_context(session_id=session.id)

        deleted = self.store.delete_session(session.id)

        self.assertTrue(deleted)
        self.assertIsNone(self.store.get_session(session.id))
        self.assertEqual(self.store.list_messages(session_id=session.id, limit=10), [])
        self.assertEqual(self.store.list_events(session_id=session.id, limit=10), [])
        self.assertIsNone(self.store.get_session_context(session_id=session.id))

    def test_delete_session_returns_false_for_missing_session(self) -> None:
        self.assertFalse(self.store.delete_session("missing-session"))

    def test_mark_stale_sessions_marks_old_active_sessions_only(self) -> None:
        stale = self.store.resolve_session(
            agent_id="main",
            channel="console",
            target="stale-target",
        )
        fresh = self.store.resolve_session(
            agent_id="main",
            channel="console",
            target="fresh-target",
        )
        paused = self.store.resolve_session(
            agent_id="main",
            channel="console",
            target="paused-target",
        )
        self.store.update_session_lifecycle(
            session_id=stale.id,
            last_activity_at="2026-03-01T00:00:00+00:00",
        )
        self.store.update_session_lifecycle(
            session_id=fresh.id,
            last_activity_at="3026-03-01T00:00:00+00:00",
        )
        self.store.update_session_lifecycle(
            session_id=paused.id,
            status="paused",
            last_activity_at="2026-03-01T00:00:00+00:00",
        )

        marked = self.store.mark_stale_sessions(timeout_seconds=60)

        self.assertEqual(marked, 1)
        reloaded_stale = self.store.get_session(stale.id)
        reloaded_fresh = self.store.get_session(fresh.id)
        reloaded_paused = self.store.get_session(paused.id)
        assert reloaded_stale is not None
        assert reloaded_fresh is not None
        assert reloaded_paused is not None
        self.assertEqual(reloaded_stale.status, "stale")
        self.assertEqual(reloaded_fresh.status, "active")
        self.assertEqual(reloaded_paused.status, "paused")
        events = self.store.list_events(
            session_id=stale.id, limit=10, newest_first=True
        )
        self.assertTrue(any(event.event_type == "session.stale" for event in events))

    def test_list_recent_messages_returns_newest_window_in_chronological_order(
        self,
    ) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        first = self.store.append_message(
            session_id=session.id, role="inbound", body="m1"
        )
        second = self.store.append_message(
            session_id=session.id, role="outbound", body="m2"
        )
        third = self.store.append_message(
            session_id=session.id, role="inbound", body="m3"
        )

        recent = self.store.list_recent_messages(session_id=session.id, limit=2)
        self.assertEqual([item.id for item in recent], [second.id, third.id])
        self.assertLess(recent[0].rowid, recent[1].rowid)
        self.assertEqual(self.store.count_messages(session_id=session.id), 3)
        all_messages = self.store.list_messages_after_rowid(
            session_id=session.id, after_rowid=0, limit=10
        )
        self.assertEqual(
            [item.id for item in all_messages], [first.id, second.id, third.id]
        )

    def test_list_recent_messages_filters_by_conversation_id(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        first = self.store.append_message(
            session_id=session.id,
            conversation_id="c1",
            role="inbound",
            body="m1",
        )
        self.store.append_message(
            session_id=session.id,
            conversation_id="c2",
            role="outbound",
            body="m2",
        )
        third = self.store.append_message(
            session_id=session.id,
            conversation_id="c1",
            role="outbound",
            body="m3",
        )

        recent = self.store.list_recent_messages(
            session_id=session.id,
            limit=10,
            conversation_id="c1",
        )
        self.assertEqual([item.id for item in recent], [first.id, third.id])

    def test_latest_conversation_id_returns_newest_non_empty_for_session(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        other = self.store.resolve_session(
            agent_id="main", channel="console", target="other"
        )
        self.store.append_message(
            session_id=session.id,
            conversation_id="c1",
            role="inbound",
            body="first",
        )
        self.store.append_message(
            session_id=session.id,
            conversation_id="",
            role="outbound",
            body="blank-conversation",
        )
        self.store.append_message(
            session_id=other.id,
            conversation_id="other-conv",
            role="inbound",
            body="other-session",
        )
        self.store.append_message(
            session_id=session.id,
            conversation_id="c2",
            role="outbound",
            body="latest",
        )

        self.assertEqual(
            self.store.latest_conversation_id(session_id=session.id),
            "c2",
        )

    def test_latest_conversation_id_ignores_empty_conversation_values(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        self.store.append_message(
            session_id=session.id,
            conversation_id="",
            role="inbound",
            body="blank",
        )
        self.store.append_message(
            session_id=session.id,
            role="outbound",
            body="still blank",
        )

        self.assertIsNone(self.store.latest_conversation_id(session_id=session.id))

    def test_latest_conversation_id_returns_none_when_session_has_no_messages(
        self,
    ) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )

        self.assertIsNone(self.store.latest_conversation_id(session_id=session.id))
        self.assertIsNone(self.store.latest_conversation_id(session_id=""))

    def _append_tied_messages(self, session_id: str) -> list[MessageRecord]:
        specs = (
            ("inbound", "conv-a", "thread-a"),
            ("outbound", "conv-a", "thread-a"),
            ("inbound", "conv-b", "thread-b"),
            ("outbound", "conv-b", "thread-b"),
        )
        with (
            patch(MESSAGE_CLOCK, return_value=TIED_AT),
            patch(
                MESSAGE_UUID,
                side_effect=[SimpleNamespace(hex=value) for value in DESCENDING_IDS],
            ),
        ):
            return [
                self.store.append_message(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    thread_id=thread_id,
                    role=role,
                    body=f"{conversation_id}/{role}",
                )
                for role, conversation_id, thread_id in specs
            ]

    def test_tied_messages_keep_append_order_and_newest_append_reads_last(
        self,
    ) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        appended = self._append_tied_messages(session.id)
        ids = [item.id for item in appended]

        listed = self.store.list_messages(session_id=session.id)
        self.assertEqual([item.id for item in listed], ids)
        self.assertEqual([item.rowid for item in listed], [1, 2, 3, 4])
        self.assertEqual([item.rowid for item in appended], [1, 2, 3, 4])
        self.assertEqual(
            [
                item.id
                for item in self.store.list_messages(session_id=session.id, limit=2)
            ],
            ids[:2],
        )
        self.assertEqual(
            [
                item.id
                for item in self.store.list_recent_messages(
                    session_id=session.id, limit=1
                )
            ],
            ids[3:],
        )
        self.assertEqual(
            [
                item.id
                for item in self.store.list_recent_messages(
                    session_id=session.id, limit=3
                )
            ],
            ids[1:],
        )
        self.assertEqual(
            self.store.latest_conversation_id(session_id=session.id), "conv-b"
        )
        self.assertEqual(ids, list(DESCENDING_IDS))
        self.assertEqual({item.created_at for item in listed}, {TIED_AT})

    def test_tied_messages_keep_append_order_within_conversation_and_thread(
        self,
    ) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        a_in, a_out, b_in, b_out = self._append_tied_messages(session.id)

        self.assertEqual(
            [
                item.id
                for item in self.store.list_messages(
                    session_id=session.id, conversation_id="conv-a"
                )
            ],
            [a_in.id, a_out.id],
        )
        self.assertEqual(
            [
                item.id
                for item in self.store.list_recent_messages(
                    session_id=session.id, conversation_id="conv-a", limit=1
                )
            ],
            [a_out.id],
        )
        thread_b = self.store.list_recent_messages(
            session_id=session.id,
            conversation_id="conv-b",
            thread_id="thread-b",
            limit=1,
        )
        self.assertEqual([item.id for item in thread_b], [b_out.id])
        self.assertEqual(thread_b[0].rowid, 4)
        self.assertEqual(
            [
                item.id
                for item in self.store.list_messages_after_rowid(
                    session_id=session.id,
                    after_rowid=a_out.rowid,
                    thread_id="thread-b",
                )
            ],
            [b_in.id, b_out.id],
        )

    def test_tied_message_cursors_page_in_append_order(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        appended = self._append_tied_messages(session.id)
        ids = [item.id for item in appended]

        pages: list[list[str]] = []
        cursor = 0
        while True:
            page = self.store.list_messages_after_rowid(
                session_id=session.id, after_rowid=cursor, limit=2
            )
            if not page:
                break
            pages.append([item.id for item in page])
            cursor = page[-1].rowid
        self.assertEqual(pages, [ids[:2], ids[2:]])

        # A cursor recorded at append time still points at the same message
        # after later appends in the same clock tick.
        self.assertEqual(
            [
                item.id
                for item in self.store.list_messages_after_rowid(
                    session_id=session.id, after_rowid=appended[0].rowid
                )
            ],
            ids[1:],
        )
        self.assertEqual(
            [
                item.id
                for item in self.store.list_recent_messages(
                    session_id=session.id,
                    limit=2,
                    after_rowid=appended[1].rowid,
                    exclude_message_ids=(ids[3],),
                )
            ],
            [ids[2]],
        )

    def _append_seq_by_id(self, session_id: str) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT id, append_seq FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        return {str(row["id"]): int(row["append_seq"]) for row in rows}

    def test_append_seq_is_allocated_per_session_and_survives_restart(self) -> None:
        first = self.store.resolve_session(
            agent_id="main", channel="console", target="first"
        )
        second = self.store.resolve_session(
            agent_id="main", channel="console", target="second"
        )
        with patch(MESSAGE_CLOCK, return_value=TIED_AT):
            first_ids = [
                self.store.append_message(
                    session_id=first.id, role="inbound", body="f1"
                ).id,
                self.store.append_message(
                    session_id=first.id, role="outbound", body="f2"
                ).id,
            ]
            second_id = self.store.append_message(
                session_id=second.id, role="inbound", body="s1"
            ).id

            self.connection.close()
            self.connection = connect_database(self.database_path)
            self.store = SessionStore(self.connection)
            first_ids.append(
                self.store.append_message(
                    session_id=first.id, role="inbound", body="f3"
                ).id
            )

        self.assertEqual(
            self._append_seq_by_id(first.id),
            {first_ids[0]: 1, first_ids[1]: 2, first_ids[2]: 3},
        )
        self.assertEqual(self._append_seq_by_id(second.id), {second_id: 1})
        self.assertEqual(
            [item.id for item in self.store.list_messages(session_id=first.id)],
            first_ids,
        )

    def test_failed_append_rolls_back_session_write_and_append_seq(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        with patch(MESSAGE_CLOCK, return_value=TIED_AT):
            first = self.store.append_message(
                session_id=session.id, role="inbound", body="first"
            )
        before = self.store.get_session(session.id)
        assert before is not None

        with (
            patch(MESSAGE_CLOCK, return_value="2026-10-22T01:00:01+00:00"),
            patch(MESSAGE_UUID, return_value=SimpleNamespace(hex=first.id)),
        ):
            with self.assertRaises(sqlite3.IntegrityError):
                self.store.append_message(
                    session_id=session.id, role="outbound", body="duplicate id"
                )

        after = self.store.get_session(session.id)
        assert after is not None
        self.assertEqual(
            (after.updated_at, after.last_activity_at),
            (before.updated_at, before.last_activity_at),
        )
        self.assertEqual(self.store.count_messages(session_id=session.id), 1)
        with patch(MESSAGE_CLOCK, return_value=TIED_AT):
            second = self.store.append_message(
                session_id=session.id, role="outbound", body="second"
            )
        self.assertEqual(
            self._append_seq_by_id(session.id), {first.id: 1, second.id: 2}
        )
        self.assertEqual(
            [item.id for item in self.store.list_messages(session_id=session.id)],
            [first.id, second.id],
        )

    def test_concurrent_appends_to_one_session_allocate_distinct_append_order(
        self,
    ) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        peers = [build_runtime_storage(self.database_path) for _ in range(4)]
        appended: dict[int, list[MessageRecord]] = {index: [] for index in range(4)}
        errors: list[BaseException] = []
        start = threading.Barrier(len(peers))

        def _append(index: int) -> None:
            try:
                start.wait()
                for step in range(10):
                    appended[index].append(
                        peers[index].sessions.append_message(
                            session_id=session.id,
                            role="inbound",
                            body=f"peer-{index}-{step}",
                        )
                    )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        try:
            with patch(MESSAGE_CLOCK, return_value=TIED_AT):
                workers = [
                    threading.Thread(target=_append, args=(index,))
                    for index in range(len(peers))
                ]
                for worker in workers:
                    worker.start()
                for worker in workers:
                    worker.join()
        finally:
            for peer in peers:
                peer.close()

        self.assertEqual(errors, [])
        records = [item for items in appended.values() for item in items]
        self.assertEqual(sorted(item.rowid for item in records), list(range(1, 41)))
        self.assertEqual(
            sorted(self._append_seq_by_id(session.id).values()), list(range(1, 41))
        )
        listed = self.store.list_messages(session_id=session.id)
        self.assertEqual(
            [item.id for item in listed],
            [item.id for item in sorted(records, key=lambda item: item.rowid)],
        )
        for items in appended.values():
            self.assertEqual(
                [item.rowid for item in items], sorted(item.rowid for item in items)
            )

    def test_session_context_create_and_update(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        created = self.store.ensure_session_context(session_id=session.id)
        self.assertEqual(created.session_id, session.id)
        self.assertEqual(created.compacted_message_count, 0)
        self.assertEqual(created.summary_short, "")

        self.store.append_message(session_id=session.id, role="inbound", body="hello")
        outbound = self.store.append_message(
            session_id=session.id, role="outbound", body="world"
        )
        updated = self.store.update_session_context(
            session_id=session.id,
            pinned_context="Pinned",
            summary_short="- user: hello",
            rolling_summary="- user: hello",
            compacted_until_rowid=outbound.rowid,
            compacted_until_created_at=outbound.created_at,
            compacted_until_message_id=outbound.id,
            compacted_message_count=2,
            version=created.version + 1,
        )
        self.assertEqual(updated.pinned_context, "Pinned")
        self.assertEqual(updated.summary_short, "- user: hello")
        self.assertEqual(updated.compacted_message_count, 2)
        self.assertEqual(updated.compacted_until_rowid, outbound.rowid)

    def test_update_session_lifecycle_fields(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="lifecycle"
        )
        updated = self.store.update_session_lifecycle(
            session_id=session.id,
            status="paused",
            last_activity_at="2026-03-16T00:00:00Z",
            closed_at="2026-03-16T01:00:00Z",
            expires_at="2026-03-17T00:00:00Z",
        )

        self.assertEqual(updated.status, "paused")
        self.assertEqual(updated.last_activity_at, "2026-03-16T00:00:00Z")
        self.assertEqual(updated.closed_at, "2026-03-16T01:00:00Z")
        self.assertEqual(updated.expires_at, "2026-03-17T00:00:00Z")

        cleared = self.store.update_session_lifecycle(
            session_id=session.id,
            closed_at=None,
            expires_at=None,
        )
        self.assertIsNone(cleared.closed_at)
        self.assertIsNone(cleared.expires_at)

    def test_touch_session_activity_and_runtime_writes_update_last_activity(
        self,
    ) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="activity"
        )
        touched = self.store.touch_session_activity(
            session_id=session.id,
            last_activity_at="2026-03-16T02:00:00Z",
        )
        self.assertEqual(touched.last_activity_at, "2026-03-16T02:00:00Z")

        self.store.append_message(session_id=session.id, role="inbound", body="hello")
        after_message = self.store.get_session(session.id)
        assert after_message is not None
        self.assertNotEqual(after_message.last_activity_at, "2026-03-16T02:00:00Z")

        prior_activity = after_message.last_activity_at
        self.store.append_event(
            session_id=session.id, event_type="session.activity", payload={}
        )
        after_event = self.store.get_session(session.id)
        assert after_event is not None
        self.assertGreaterEqual(after_event.last_activity_at, prior_activity)

    def test_blank_lifecycle_fields_are_backward_compatible(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="legacy"
        )
        self.connection.execute(
            """
            UPDATE sessions
            SET status = '', last_activity_at = '', closed_at = '', expires_at = ''
            WHERE id = ?
            """,
            (session.id,),
        )
        self.connection.commit()

        reloaded = self.store.get_session(session.id)
        assert reloaded is not None
        self.assertEqual(reloaded.status, "active")
        self.assertEqual(reloaded.last_activity_at, reloaded.updated_at)
        self.assertIsNone(reloaded.closed_at)
        self.assertIsNone(reloaded.expires_at)

    def test_replace_and_list_pins_enforces_contract(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="pins"
        )
        updated_context = self.store.replace_pins(
            session_id=session.id,
            pins=[
                PinnedContextEntry(
                    pin_id="p1",
                    source="user",
                    text="Home city is Seattle",
                ),
                PinnedContextEntry(
                    pin_id="p2",
                    source="policy",
                    text="Never reveal API keys",
                ),
            ],
        )
        self.assertTrue(updated_context.pinned_context.startswith("{"))

        listed = self.store.list_pins(session_id=session.id)
        self.assertEqual([entry.source for entry in listed], ["user", "policy"])

        with self.assertRaises(ValueError):
            self.store.replace_pins(
                session_id=session.id,
                pins=[PinnedContextEntry(pin_id="bad", source="invalid", text="x")],
            )

    def test_add_remove_pin_operations_are_deduplicated_and_stable(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="pin-ops"
        )
        pins = self.store.add_pin(
            session_id=session.id,
            source="user",
            text="Favorite language is Python",
            pin_id="p1",
            created_at="2026-03-16T00:00:00Z",
        )
        self.assertEqual(len(pins), 1)

        pins = self.store.add_pin(
            session_id=session.id,
            source="user",
            text="Favorite language is Python",
            pin_id="p2",
            created_at="2026-03-16T00:01:00Z",
        )
        self.assertEqual(len(pins), 1)
        self.assertEqual(pins[0].pin_id, "p1")

        pins = self.store.add_pin(
            session_id=session.id,
            source="policy",
            text="Never expose secrets",
            pin_id="p3",
            created_at="2026-03-16T00:02:00Z",
        )
        self.assertEqual([item.pin_id for item in pins], ["p1", "p3"])

        pins = self.store.remove_pin(
            session_id=session.id,
            text="Favorite language is Python",
            source="user",
        )
        self.assertEqual([item.pin_id for item in pins], ["p3"])

        pins = self.store.remove_pin(session_id=session.id, pin_id="p3")
        self.assertEqual(pins, [])

    def test_add_pin_respects_policy_limits(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="pin-limits"
        )
        policy = PinnedContextPolicy(
            max_pins=1, max_chars_per_pin=50, max_total_chars=50
        )
        self.store.add_pin(
            session_id=session.id,
            source="user",
            text="first",
            policy=policy,
        )
        with self.assertRaises(ValueError):
            self.store.add_pin(
                session_id=session.id,
                source="policy",
                text="second",
                policy=policy,
            )

    def test_set_session_status_emits_status_changed_event(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="status-change"
        )
        updated = self.store.set_session_status(
            session_id=session.id,
            status="paused",
            reason="operator_pause",
        )
        self.assertEqual(updated.status, "paused")

        events = self.store.list_events(
            session_id=session.id,
            limit=10,
            newest_first=True,
            event_type_prefix="session.status.changed",
        )
        self.assertEqual(len(events), 1)
        payload = events[0].payload
        self.assertEqual(payload.get("previous_status"), "active")
        self.assertEqual(payload.get("status"), "paused")
        self.assertEqual(payload.get("reason"), "operator_pause")

    def test_close_session_emits_closed_event(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="close"
        )
        updated = self.store.close_session(session_id=session.id, reason="finished")
        self.assertEqual(updated.status, "closed")
        self.assertIsNotNone(updated.closed_at)

        closed_events = self.store.list_events(
            session_id=session.id,
            limit=10,
            event_type_prefix="session.closed",
        )
        self.assertEqual(len(closed_events), 1)
        self.assertEqual(closed_events[0].payload.get("reason"), "finished")

    def test_explicit_resume_reactivates_closed_session_and_keeps_history(self) -> None:
        session = self.store.resolve_session(
            agent_id="main",
            channel="console",
            target="resume-closed",
            session_id="resume-closed-session",
        )
        self.store.append_message(
            session_id=session.id,
            role="inbound",
            body="durable history",
        )
        self.store.close_session(session_id=session.id, reason="finished")

        resumed = self.store.resolve_session(
            agent_id="main",
            channel="console",
            target="resume-closed",
            session_id=session.id,
        )

        self.assertEqual(resumed.status, "active")
        self.assertIsNone(resumed.closed_at)
        self.assertEqual(
            [item.body for item in self.store.list_messages(session_id=session.id)],
            ["durable history"],
        )
        resumed_events = self.store.list_events(
            session_id=session.id,
            event_type_prefix="session.resumed",
        )
        self.assertEqual(len(resumed_events), 1)
        self.assertEqual(resumed_events[0].payload["previous_status"], "closed")

    def test_implicit_resolution_does_not_reopen_closed_session(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="implicit-closed"
        )
        self.store.close_session(session_id=session.id)

        with self.assertRaisesRegex(ValueError, "resume it explicitly"):
            self.store.resolve_session(
                agent_id="main", channel="console", target="implicit-closed"
            )

    def test_expire_session_emits_expired_event(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="expire"
        )
        updated = self.store.expire_session(
            session_id=session.id,
            expires_at="2026-03-17T00:00:00Z",
            reason="ttl",
        )
        self.assertEqual(updated.status, "closed")
        self.assertEqual(updated.expires_at, "2026-03-17T00:00:00Z")

        expired_events = self.store.list_events(
            session_id=session.id,
            limit=10,
            event_type_prefix="session.expired",
        )
        self.assertEqual(len(expired_events), 1)
        payload = expired_events[0].payload
        self.assertEqual(payload.get("expires_at"), "2026-03-17T00:00:00Z")
        self.assertEqual(payload.get("reason"), "ttl")

        with self.assertRaisesRegex(ValueError, "expired"):
            self.store.resolve_session(
                agent_id="main",
                channel="console",
                target="expire",
                session_id=session.id,
            )

    def test_set_session_status_rejects_invalid_status(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="bad-status"
        )
        with self.assertRaises(ValueError):
            self.store.set_session_status(session_id=session.id, status="invalid")

    def test_create_room_and_manage_participants(self) -> None:
        session = self.store.create_room(
            channel="cli",
            target="spec-review",
            metadata={"name": "Spec review"},
        )

        self.assertTrue(session.session_key.startswith("room:"))
        self.assertIsNone(session.active_agent_id)

        self.store.add_participant(
            session_id=session.id,
            participant_type="agent",
            participant_id="writer-agent",
            channel="cli",
            role="owner",
            display_name="Writer",
        )
        self.store.add_participant(
            session_id=session.id,
            participant_type="agent",
            participant_id="review-agent",
            channel="cli",
            role="participant",
            display_name="Reviewer",
        )

        participants = self.store.list_participants(session.id)
        self.assertEqual(
            [(item.participant_type, item.participant_id) for item in participants],
            [("agent", "writer-agent"), ("agent", "review-agent")],
        )
        self.assertEqual(self.store.get_active_agent(session.id), "writer-agent")

        updated = self.store.set_active_agent(
            session_id=session.id,
            agent_id="review-agent",
        )
        self.assertEqual(updated.active_agent_id, "review-agent")

        removed = self.store.remove_participant(
            session_id=session.id,
            participant_type="agent",
            participant_id="review-agent",
        )
        self.assertTrue(removed)
        self.assertEqual(self.store.get_active_agent(session.id), "writer-agent")

    def test_room_participant_rejects_unsupported_role(self) -> None:
        session = self.store.create_room(channel="cli", target="role-check")

        with self.assertRaisesRegex(ValueError, "Invalid participant role"):
            self.store.add_participant(
                session_id=session.id,
                participant_type="human",
                participant_id="alice",
                channel="cli",
                role="admin",
            )

        self.assertEqual(self.store.list_participants(session.id), [])

    def test_append_message_stores_participant_attribution(self) -> None:
        session = self.store.resolve_session(
            agent_id="main",
            channel="console",
            target="participant-meta",
        )
        message = self.store.append_message(
            session_id=session.id,
            role="outbound",
            body="hello",
            participant_id="main",
            participant_type="agent",
            display_name="Main",
        )

        self.assertEqual(message.metadata.get("participant_id"), "main")
        self.assertEqual(message.metadata.get("participant_type"), "agent")
        self.assertEqual(message.metadata.get("display_name"), "Main")

    def test_legacy_session_gets_lazy_synthetic_agent_participant(self) -> None:
        session = self.store.resolve_session(
            agent_id="legacy-agent",
            channel="console",
            target="legacy-room",
        )

        participants = self.store.list_participants(session.id)
        self.assertEqual(len(participants), 1)
        self.assertEqual(participants[0].participant_type, "agent")
        self.assertEqual(participants[0].participant_id, "legacy-agent")
        self.assertEqual(self.store.get_active_agent(session.id), "legacy-agent")

    def test_explicit_session_accepts_invited_agent_on_legacy_session(self) -> None:
        session = self.store.resolve_session(
            agent_id="agent-a",
            channel="console",
            target="shared-room",
            session_id="room-ish-session",
        )
        self.store.add_participant(
            session_id=session.id,
            participant_type="agent",
            participant_id="agent-b",
            channel="console",
            role="participant",
            display_name="Agent B",
        )
        self.store.set_active_agent(session_id=session.id, agent_id="agent-b")

        resolved = self.store.resolve_session(
            agent_id="agent-b",
            channel="console",
            target="shared-room",
            session_id=session.id,
        )
        self.assertEqual(resolved.id, session.id)
        self.assertEqual(resolved.active_agent_id, "agent-b")

    def test_tied_joins_list_in_join_order_and_fallback_selects_next_join(
        self,
    ) -> None:
        check_tied_join_order(self.store)

    def test_active_upsert_keeps_join_place_and_rejoin_renews_it(self) -> None:
        check_join_place_on_upsert_and_rejoin(self.store)

    def test_explicit_active_agent_is_kept_and_fallback_follows_join_order(
        self,
    ) -> None:
        check_explicit_active_agent(self.store)

    def test_legacy_owner_lists_before_agent_invited_on_same_tick(self) -> None:
        check_legacy_owner_join_order(self.store)

    def test_participant_joins_write_no_session_value(self) -> None:
        check_joins_write_no_session_value(self.store)

    def test_concurrent_joins_to_one_room_allocate_distinct_join_order(
        self,
    ) -> None:
        peers = [build_runtime_storage(self.database_path) for _ in range(4)]
        try:
            check_concurrent_joins(self.store, [peer.sessions for peer in peers])
        finally:
            for peer in peers:
                peer.close()

    def test_leave_and_rejoin_on_two_connections_wait_for_each_other(self) -> None:
        leaver, joiner = (build_runtime_storage(self.database_path) for _ in range(2))
        try:
            check_leave_and_rejoin_wait_for_each_other(
                self.store, leaver.sessions, joiner.sessions
            )
        finally:
            leaver.close()
            joiner.close()

    def _check_on_two_connections(self, check) -> None:
        first, second = (build_runtime_storage(self.database_path) for _ in range(2))
        try:
            check(self.store, first.sessions, second.sessions)
        finally:
            first.close()
            second.close()

    def test_join_keeps_an_active_agent_stored_during_it(self) -> None:
        self._check_on_two_connections(
            check_join_keeps_an_activation_committed_during_it
        )

    def test_selection_during_a_join_waits_and_then_applies(self) -> None:
        self._check_on_two_connections(check_selection_during_a_join_applies_after_it)

    def test_leave_during_a_join_waits_and_then_applies(self) -> None:
        self._check_on_two_connections(check_leave_during_a_join_applies_after_it)

    def test_joins_selection_and_leaves_on_two_connections(self) -> None:
        self._check_on_two_connections(
            check_joins_selection_and_leaves_on_two_connections
        )

    def test_legacy_owner_records_activate_no_agent(self) -> None:
        check_legacy_owner_records_activate_no_agent(self.store)

    def test_join_treats_a_blank_active_agent_as_none(self) -> None:
        check_blank_active_agent_counts_as_none(self.store)

    def test_leave_during_a_selection_waits_and_then_applies(self) -> None:
        self._check_on_two_connections(check_leave_during_a_selection_applies_after_it)

    def test_selection_during_a_leave_waits_and_is_refused(self) -> None:
        self._check_on_two_connections(check_selection_during_a_leave_is_refused)

    def test_legacy_session_keeps_its_selection_rules(self) -> None:
        check_legacy_session_selection(self.store)

    # --- RSSD-01 characterization tests ---

    def test_import_smoke_all_public_exports(self) -> None:
        self.assertTrue(SessionStore is not None)
        self.assertTrue(SessionRecord is not None)
        self.assertTrue(RoomParticipant is not None)
        self.assertTrue(MessageRecord is not None)
        self.assertTrue(EventRecord is not None)
        self.assertTrue(SessionContextRecord is not None)

    def test_record_store_constructor_path_behaves_same_as_connection(self) -> None:
        from openminion.modules.storage.record_store import RecordStoreSQLite

        def _exercise(store: SessionStore) -> dict:
            session = store.resolve_session(
                agent_id="parity-agent", channel="console", target="parity-target"
            )
            same = store.resolve_session(
                agent_id="Parity-Agent", channel="CONSOLE", target="Parity-Target"
            )
            msg = store.append_message(
                session_id=session.id, role="inbound", body="hello parity"
            )
            messages = store.list_messages(session_id=session.id, limit=10)
            evt = store.append_event(
                session_id=session.id,
                event_type="test.parity",
                payload={"via": "constructor"},
            )
            ctx = store.ensure_session_context(session_id=session.id)
            return {
                "session_channel": session.channel,
                "session_target": session.target,
                "session_status": session.status,
                "deterministic": session.id == same.id,
                "msg_body": msg.body,
                "msg_count": len(messages),
                "evt_type": evt.event_type,
                "ctx_version": ctx.version,
            }

        # Path A: raw sqlite3.Connection (used by setUp)
        conn_results = _exercise(self.store)

        # Path B: RecordStoreSQLite
        tmp = tempfile.TemporaryDirectory()
        try:
            db_path = Path(tmp.name) / "rs_test" / "openminion.db"
            migrate_database(db_path)
            record_store = RecordStoreSQLite(db_path, wal=True)
            try:
                rs_results = _exercise(SessionStore(record_store))
            finally:
                record_store.close()
        finally:
            tmp.cleanup()

        self.assertEqual(conn_results, rs_results)

    def test_expected_version_prevents_stale_update(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="version-test"
        )
        ctx = self.store.ensure_session_context(session_id=session.id)
        self.assertEqual(ctx.version, 1)

        updated = self.store.update_session_context(
            session_id=session.id,
            summary_short="first update",
            version=2,
            expected_version=1,
        )
        self.assertEqual(updated.summary_short, "first update")
        self.assertEqual(updated.version, 2)

        stale_result = self.store.update_session_context(
            session_id=session.id,
            summary_short="stale update - should not apply",
            version=3,
            expected_version=1,
        )
        self.assertEqual(stale_result.version, 2)
        self.assertEqual(stale_result.summary_short, "first update")

    def test_set_active_agent_rejects_non_participant(self) -> None:
        room = self.store.create_room(channel="cli", target="rejection-test")
        self.store.add_participant(
            session_id=room.id,
            participant_type="agent",
            participant_id="agent-a",
            channel="cli",
            role="owner",
            display_name="Agent A",
        )

        with self.assertRaises(ValueError):
            self.store.set_active_agent(
                session_id=room.id, agent_id="non-existent-agent"
            )
