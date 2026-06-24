import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from openminion.services.context.session import (
    SessionCompactionResult,
    SessionContextService,
)
from openminion.modules.brain.constants import (
    RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    SESSION_EVENT_POLICY_CONFIRMATION_PROMPT,
)
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.pinned_context import PinnedContextEntry
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database


class SessionContextServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database_path = Path(self._tmp.name) / "state" / "openminion.db"
        migrate_database(self.database_path)
        self.connection = connect_database(self.database_path)
        self.store = SessionStore(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self._tmp.cleanup()

    def test_compaction_keeps_recent_window_and_injects_system_context(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        self.store.append_message(session_id=session.id, role="inbound", body="u1")
        self.store.append_message(session_id=session.id, role="outbound", body="a1")
        self.store.append_message(session_id=session.id, role="inbound", body="u2")
        self.store.append_message(session_id=session.id, role="outbound", body="a2")
        recent_user = self.store.append_message(
            session_id=session.id, role="inbound", body="u3"
        )
        recent_assistant = self.store.append_message(
            session_id=session.id, role="outbound", body="a3"
        )

        service = SessionContextService(
            self.store,
            keep_recent_messages=2,
            max_compact_per_turn=50,
        )
        result = service.compact_session(session_id=session.id)
        self.assertEqual(result.compacted_count, 4)
        self.assertGreater(result.compacted_until_rowid, 0)

        context = self.store.get_session_context(session_id=session.id)
        self.assertIsNotNone(context)
        assert context is not None
        self.assertIn("- user: u1", context.rolling_summary)
        self.assertIn("- assistant: a2", context.rolling_summary)
        self.assertEqual(context.compacted_message_count, 4)

        history = service.build_history(
            session_id=session.id,
            channel="console",
            target="chat",
            recent_limit=2,
        )
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0].metadata.get("role"), "system")
        self.assertIn("Rolling summary:", history[0].body)
        self.assertEqual(history[1].id, recent_user.id)
        self.assertEqual(history[2].id, recent_assistant.id)

        second_result = service.compact_session(session_id=session.id)
        self.assertEqual(second_result.compacted_count, 0)

    def test_build_history_without_summary_has_no_system_context_message(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        first = self.store.append_message(
            session_id=session.id, role="inbound", body="hello"
        )
        second = self.store.append_message(
            session_id=session.id, role="outbound", body="world"
        )

        service = SessionContextService(self.store, keep_recent_messages=20)
        history = service.build_history(
            session_id=session.id,
            channel="console",
            target="chat",
            recent_limit=5,
        )
        self.assertEqual([item.id for item in history], [first.id, second.id])

    def test_build_history_renders_structured_pinned_context(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        self.store.replace_pins(
            session_id=session.id,
            pins=[
                PinnedContextEntry(pin_id="p1", source="user", text="Call me Alex"),
                PinnedContextEntry(
                    pin_id="p2", source="policy", text="Keep replies concise"
                ),
            ],
        )
        self.store.append_message(session_id=session.id, role="inbound", body="hello")

        service = SessionContextService(self.store, keep_recent_messages=20)
        history = service.build_history(
            session_id=session.id,
            channel="console",
            target="chat",
            recent_limit=5,
        )
        self.assertGreaterEqual(len(history), 2)
        self.assertEqual(history[0].metadata.get("role"), "system")
        self.assertIn("Pinned context:", history[0].body)
        self.assertIn("- [user] Call me Alex", history[0].body)
        self.assertIn("- [policy] Keep replies concise", history[0].body)

    def test_service_pin_operations_delegate_to_store_contract(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        service = SessionContextService(self.store, keep_recent_messages=20)

        pins = service.add_pin(
            session_id=session.id,
            source="operator",
            text="Always include step-by-step output",
            pin_id="op1",
        )
        self.assertEqual(len(pins), 1)
        self.assertEqual(pins[0].source, "operator")

        pins = service.list_pins(session_id=session.id)
        self.assertEqual([item.pin_id for item in pins], ["op1"])

        pins = service.remove_pin(session_id=session.id, pin_id="op1")
        self.assertEqual(pins, [])

    def test_compaction_archives_full_chunk_and_exposes_reference(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        self.store.append_message(session_id=session.id, role="inbound", body="u1")
        self.store.append_message(session_id=session.id, role="outbound", body="a1")
        self.store.append_message(session_id=session.id, role="inbound", body="u2")
        self.store.append_message(session_id=session.id, role="outbound", body="a2")
        self.store.append_message(session_id=session.id, role="inbound", body="u3")
        self.store.append_message(session_id=session.id, role="outbound", body="a3")

        archive_root = Path(self._tmp.name) / "session-context-archive"
        service = SessionContextService(
            self.store,
            keep_recent_messages=2,
            max_compact_per_turn=50,
            archive_enabled=True,
            archive_root=archive_root,
            archive_ref_limit=3,
        )
        result = service.compact_session(session_id=session.id)
        self.assertEqual(result.compacted_count, 4)
        self.assertTrue(result.archive_relative_path)

        archive_path = archive_root / result.archive_relative_path
        self.assertTrue(archive_path.exists())
        content = archive_path.read_text(encoding="utf-8")
        self.assertIn('"rowid":', content)
        self.assertIn('"body": "u1"', content)
        self.assertIn('"body": "a2"', content)

        events = self.store.list_events(
            session_id=session.id,
            limit=5,
            newest_first=True,
            event_type_prefix="session.compaction.archive",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0].payload.get("relative_path"), result.archive_relative_path
        )

        history = service.build_history(
            session_id=session.id,
            channel="console",
            target="chat",
            recent_limit=2,
        )
        self.assertEqual(history[0].metadata.get("role"), "system")
        self.assertIn("Compaction archive refs", history[0].body)
        self.assertIn(result.archive_relative_path, history[0].body)
        self.assertNotIn("[archive_ref]", history[0].body)

    def test_compaction_summary_deduplicates_repeated_lines(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        self.store.append_message(
            session_id=session.id, role="inbound", body="repeat me"
        )
        self.store.append_message(
            session_id=session.id, role="inbound", body="repeat me"
        )
        self.store.append_message(session_id=session.id, role="outbound", body="ok")

        service = SessionContextService(
            self.store,
            keep_recent_messages=1,
            max_compact_per_turn=50,
            archive_enabled=False,
        )
        service.compact_session(session_id=session.id)
        context = self.store.get_session_context(session_id=session.id)
        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context.rolling_summary.count("- user: repeat me"), 1)

    def test_build_history_scopes_to_conversation_without_system_summary(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        first = self.store.append_message(
            session_id=session.id,
            conversation_id="c1",
            role="inbound",
            body="hello",
        )
        second = self.store.append_message(
            session_id=session.id,
            conversation_id="c1",
            role="outbound",
            body="world",
        )
        self.store.append_message(
            session_id=session.id,
            conversation_id="c2",
            role="inbound",
            body="other",
        )

        service = SessionContextService(self.store, keep_recent_messages=20)
        history = service.build_history(
            session_id=session.id,
            channel="console",
            target="chat",
            recent_limit=5,
            conversation_id="c1",
        )
        self.assertEqual([item.id for item in history], [first.id, second.id])
        self.assertNotEqual(history[0].metadata.get("role"), "system")

    def test_latest_conversation_lookup_does_not_widen_conversation_scoped_history(
        self,
    ) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        first = self.store.append_message(
            session_id=session.id,
            conversation_id="c1",
            role="inbound",
            body="older conversation",
        )
        second = self.store.append_message(
            session_id=session.id,
            conversation_id="c1",
            role="outbound",
            body="older reply",
        )
        self.store.append_message(
            session_id=session.id,
            conversation_id="c2",
            role="inbound",
            body="latest conversation",
        )

        self.assertEqual(self.store.latest_conversation_id(session_id=session.id), "c2")

        service = SessionContextService(self.store, keep_recent_messages=20)
        history = service.build_history(
            session_id=session.id,
            channel="console",
            target="chat",
            recent_limit=5,
            conversation_id="c1",
        )

        self.assertEqual([item.id for item in history], [first.id, second.id])
        self.assertEqual(
            [item.body for item in history], ["older conversation", "older reply"]
        )

    def test_constructor_accepts_optional_retrieve_ctl(self) -> None:
        ctl = MagicMock()
        service = SessionContextService(
            self.store, keep_recent_messages=20, retrieve_ctl=ctl
        )
        self.assertIs(service._retrieve_ctl, ctl)

    def test_episode_ingestion_fires_on_compact(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        self.store.append_message(session_id=session.id, role="inbound", body="u1")
        self.store.append_message(session_id=session.id, role="outbound", body="a1")
        self.store.append_message(session_id=session.id, role="inbound", body="u2")
        self.store.append_message(session_id=session.id, role="outbound", body="a2")
        self.store.append_message(session_id=session.id, role="inbound", body="u3")
        self.store.append_message(session_id=session.id, role="outbound", body="a3")

        ctl = MagicMock()
        service = SessionContextService(
            self.store,
            keep_recent_messages=2,
            max_compact_per_turn=50,
            retrieve_ctl=ctl,
        )

        result = service.compact_session(session_id=session.id)
        self.assertEqual(result.compacted_count, 4)
        # Adjacent user->assistant compacted messages are ingested as turn-pair units.
        self.assertEqual(ctl.ingest_source.call_count, 2)
        first_kwargs = ctl.ingest_source.call_args_list[0].kwargs
        self.assertEqual(first_kwargs.get("source_type"), "episode")
        self.assertEqual(first_kwargs.get("scope"), f"session:{session.id}")
        self.assertIn("rowid:", str(first_kwargs.get("source_ref", "")))
        self.assertIn("-", str(first_kwargs.get("source_ref", "")))
        self.assertIn("turn-pair", list(first_kwargs.get("tags", [])))

    def test_episode_ingestion_skipped_when_no_ctl(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        self.store.append_message(session_id=session.id, role="inbound", body="u1")
        self.store.append_message(session_id=session.id, role="outbound", body="a1")
        self.store.append_message(session_id=session.id, role="inbound", body="u2")
        self.store.append_message(session_id=session.id, role="outbound", body="a2")
        self.store.append_message(session_id=session.id, role="inbound", body="u3")
        self.store.append_message(session_id=session.id, role="outbound", body="a3")

        service = SessionContextService(
            self.store,
            keep_recent_messages=2,
            max_compact_per_turn=50,
            retrieve_ctl=None,
        )
        result = service.compact_session(session_id=session.id)
        self.assertEqual(result.compacted_count, 4)

    def test_episode_ingestion_error_does_not_block_compact(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        self.store.append_message(session_id=session.id, role="inbound", body="u1")
        self.store.append_message(session_id=session.id, role="outbound", body="a1")
        self.store.append_message(session_id=session.id, role="inbound", body="u2")
        self.store.append_message(session_id=session.id, role="outbound", body="a2")
        self.store.append_message(session_id=session.id, role="inbound", body="u3")
        self.store.append_message(session_id=session.id, role="outbound", body="a3")

        ctl = MagicMock()
        ctl.ingest_source.side_effect = RuntimeError("ingest failure")
        service = SessionContextService(
            self.store,
            keep_recent_messages=2,
            max_compact_per_turn=50,
            retrieve_ctl=ctl,
        )
        result = service.compact_session(session_id=session.id)
        self.assertEqual(result.compacted_count, 4)

    def test_build_history_with_token_budget_emits_budget_event(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        for idx in range(6):
            role = "inbound" if idx % 2 == 0 else "outbound"
            self.store.append_message(
                session_id=session.id,
                role=role,
                body=f"message-{idx}-" + ("x" * 80),
            )

        service = SessionContextService(
            self.store,
            keep_recent_messages=20,
            token_budget=10,
            chars_per_token=4.0,
        )
        _ = service.build_history(
            session_id=session.id,
            channel="console",
            target="chat",
            recent_limit=20,
        )

        events = self.store.list_events(
            session_id=session.id,
            limit=10,
            newest_first=True,
            event_type_prefix="session.context.budget",
        )
        self.assertEqual(len(events), 1)
        payload = events[0].payload
        self.assertEqual(payload.get("max_tokens"), 10)
        self.assertIn("messages_before_trim", payload)
        self.assertIn("messages_after_trim", payload)
        self.assertIn("trimmed_count", payload)
        self.assertIn("overflow", payload)

    def test_compaction_deferred_summary_enrichment_fail_open(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        self.store.append_message(session_id=session.id, role="inbound", body="u1")
        self.store.append_message(session_id=session.id, role="outbound", body="a1")
        self.store.append_message(session_id=session.id, role="inbound", body="u2")
        self.store.append_message(session_id=session.id, role="outbound", body="a2")
        self.store.append_message(session_id=session.id, role="inbound", body="u3")
        self.store.append_message(session_id=session.id, role="outbound", body="a3")

        def _raise_enrichment(_: str) -> str:
            raise RuntimeError("summary enrichment failed")

        service = SessionContextService(
            self.store,
            keep_recent_messages=2,
            max_compact_per_turn=50,
            summary_enrichment_enabled=True,
            summary_enricher=_raise_enrichment,
            summary_enrichment_defer=lambda task: task(),
        )
        result = service.compact_session(session_id=session.id)
        self.assertEqual(result.compacted_count, 4)

        context = self.store.get_session_context(session_id=session.id)
        self.assertIsNotNone(context)
        assert context is not None
        self.assertIn("- user: u1", context.rolling_summary)

        enriched_events = self.store.list_events(
            session_id=session.id,
            limit=10,
            newest_first=True,
            event_type_prefix="session.summary.enriched",
        )
        self.assertEqual(enriched_events, [])

    def test_compaction_deferred_summary_enrichment_success(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        self.store.append_message(session_id=session.id, role="inbound", body="u1")
        self.store.append_message(session_id=session.id, role="outbound", body="a1")
        self.store.append_message(session_id=session.id, role="inbound", body="u2")
        self.store.append_message(session_id=session.id, role="outbound", body="a2")
        self.store.append_message(session_id=session.id, role="inbound", body="u3")
        self.store.append_message(session_id=session.id, role="outbound", body="a3")

        service = SessionContextService(
            self.store,
            keep_recent_messages=2,
            max_compact_per_turn=50,
            summary_enrichment_enabled=True,
            summary_enricher=lambda summary: summary + "\n- assistant: enriched",
            summary_enrichment_defer=lambda task: task(),
        )
        result = service.compact_session(session_id=session.id)
        self.assertEqual(result.compacted_count, 4)

        context = self.store.get_session_context(session_id=session.id)
        self.assertIsNotNone(context)
        assert context is not None
        self.assertIn("enriched", context.rolling_summary)

        enriched_events = self.store.list_events(
            session_id=session.id,
            limit=10,
            newest_first=True,
            event_type_prefix="session.summary.enriched",
        )
        self.assertEqual(len(enriched_events), 1)
        self.assertEqual(enriched_events[0].payload.get("mode"), "deferred")

    def test_summary_checkpoint_excludes_policy_confirmation_projection(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="chat"
        )
        self.store.append_message(session_id=session.id, role="inbound", body="u1")
        self.store.append_message(
            session_id=session.id,
            role="outbound",
            body=(
                "Policy confirmation required.\n"
                "file.write (path=report.py)\n"
                "Reply exactly yes to confirm or exactly no to cancel."
            ),
            metadata={"respond_kind": RESPOND_KIND_POLICY_CONFIRMATION_PROMPT},
        )
        self.store.append_message(
            session_id=session.id,
            role="event",
            body="Policy confirmation required.",
            metadata={"event_type": SESSION_EVENT_POLICY_CONFIRMATION_PROMPT},
        )
        self.store.append_message(session_id=session.id, role="outbound", body="a1")

        service = SessionContextService(self.store, keep_recent_messages=20)
        summary, logical_total = service.build_summary_checkpoint(session_id=session.id)

        self.assertEqual(logical_total, 4)
        self.assertIn("- user: u1", summary)
        self.assertIn("- assistant: a1", summary)
        self.assertNotIn("Policy confirmation required", summary)
        self.assertNotIn("Reply exactly yes", summary)

    def test_async_compact_session_wrapper_delegates_to_sync_method(self) -> None:
        service = SessionContextService(self.store, keep_recent_messages=20)
        expected = SessionCompactionResult(
            session_id="sess-async",
            compacted_count=1,
            compacted_until_rowid=4,
            summary_updated=True,
        )

        async def _run() -> SessionCompactionResult:
            with patch.object(
                service, "compact_session", return_value=expected
            ) as mocked:
                result = await service.acompact_session(session_id="sess-async")
                mocked.assert_called_once_with(session_id="sess-async")
                return result

        result = asyncio.run(_run())
        self.assertEqual(result, expected)

    def test_async_build_history_wrapper_preserves_failure_behavior(self) -> None:
        service = SessionContextService(self.store, keep_recent_messages=20)

        async def _run() -> None:
            with patch.object(
                service,
                "build_history",
                side_effect=RuntimeError("history boom"),
            ) as mocked:
                with self.assertRaisesRegex(RuntimeError, "history boom"):
                    await service.abuild_history(
                        session_id="sess-async",
                        channel="console",
                        target="chat",
                        recent_limit=5,
                    )
                mocked.assert_called_once_with(
                    session_id="sess-async",
                    channel="console",
                    target="chat",
                    recent_limit=5,
                    conversation_id=None,
                    thread_id=None,
                )

        asyncio.run(_run())
