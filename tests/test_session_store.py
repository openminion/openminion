import tempfile
import unittest
from pathlib import Path

from openminion.modules.storage.runtime import SessionStore as RuntimeSessionStore
from openminion.modules.storage.runtime.migrations import migrate_database
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
from openminion.modules.storage.runtime.sqlite import connect_database


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

    def test_explicit_session_id_override_wins(self) -> None:
        session = self.store.resolve_session(
            agent_id="main",
            channel="console",
            target="team-a",
            session_id="manual-session-1",
        )
        resolved_again = self.store.resolve_session(
            agent_id="main",
            channel="console",
            target="team-b",
            session_id="manual-session-1",
        )

        self.assertEqual(session.id, "manual-session-1")
        self.assertEqual(resolved_again.id, "manual-session-1")
        self.assertEqual(session.id, resolved_again.id)
        self.assertIn("agent:main|", session.session_key)

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
