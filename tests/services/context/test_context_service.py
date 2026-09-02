import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from openminion.services.context.session import (
    SessionCompactionResult,
    SessionContextService,
)
from openminion.base.errors import error_info_from_exception
from openminion.modules.context.budget import ContextBudgetOverflowError
from openminion.modules.brain.constants import (
    RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    SESSION_EVENT_POLICY_CONFIRMATION_PROMPT,
)
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.pinned_context import PinnedContextEntry
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.session_store.turn_leases import (
    RuntimeSessionTurnFenceError,
)
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

    def test_history_exclusions_overfetch_without_reintroducing_compacted_rows(
        self,
    ) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="room"
        )
        records = [
            self.store.append_message(
                session_id=session.id,
                role="inbound" if index % 2 == 0 else "outbound",
                body=f"message-{index}",
            )
            for index in range(6)
        ]
        service = SessionContextService(
            self.store,
            keep_recent_messages=2,
            max_compact_per_turn=50,
        )
        service.compact_session(session_id=session.id)

        history = service.build_history(
            session_id=session.id,
            channel="console",
            target="room",
            recent_limit=2,
            exclude_history_message_ids=(records[-1].id,),
        )

        self.assertEqual(history[0].metadata.get("role"), "system")
        self.assertEqual([item.id for item in history[1:]], [records[-2].id])

    def test_token_budget_avoids_compacting_tiny_messages_within_safety_bound(
        self,
    ) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="tiny"
        )
        for body in ("u1", "a1", "u2", "a2"):
            self.store.append_message(
                session_id=session.id,
                role="inbound" if body.startswith("u") else "outbound",
                body=body,
            )
        service = SessionContextService(
            self.store,
            keep_recent_messages=2,
            token_budget=1000,
            chars_per_token=4.0,
        )

        result = service.compact_session(session_id=session.id)

        self.assertEqual(result.compacted_count, 0)
        self.assertEqual(result.reason, "within_budget")
        self.assertEqual(result.budget_source, "runtime_cap")

    def test_token_pressure_compacts_large_messages_but_keeps_recent_tail(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="large"
        )
        for idx in range(4):
            self.store.append_message(
                session_id=session.id,
                role="inbound" if idx % 2 == 0 else "outbound",
                body=f"message-{idx}-" + ("x" * 400),
            )
        service = SessionContextService(
            self.store,
            keep_recent_messages=2,
            token_budget=100,
            chars_per_token=1.0,
        )

        result = service.compact_session(session_id=session.id)

        self.assertEqual(result.compacted_count, 2)
        self.assertEqual(result.reason, "token_pressure")
        event = self.store.list_events(
            session_id=session.id,
            event_type_prefix="session.context.compaction",
        )[0]
        self.assertEqual(event.payload["reason"], "token_pressure")
        self.assertEqual(event.payload["keep_recent_messages"], 2)

    def test_manual_compaction_uses_explicit_reason_with_token_headroom(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="manual"
        )
        for body in ("u1", "a1", "u2"):
            self.store.append_message(
                session_id=session.id,
                role="inbound" if body.startswith("u") else "outbound",
                body=body,
            )
        service = SessionContextService(
            self.store,
            keep_recent_messages=2,
            token_budget=1000,
        )

        result = service.compact_session(session_id=session.id, force=True)

        self.assertEqual(result.compacted_count, 1)
        self.assertEqual(result.reason, "manual")

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

    def test_build_history_preserves_only_bounded_continuity_metadata(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="metadata"
        )
        self.store.append_message(
            session_id=session.id,
            role="inbound",
            body="hello",
            metadata={
                "trace_id": "trace-1",
                "run_id": "run-1",
                "tool_output": "must-not-cross",
                "secret": "must-not-cross",
            },
        )

        history = SessionContextService(self.store).build_history(
            session_id=session.id,
            channel="console",
            target="metadata",
            recent_limit=5,
        )

        self.assertEqual(history[0].metadata["trace_id"], "trace-1")
        self.assertEqual(history[0].metadata["run_id"], "run-1")
        self.assertNotIn("tool_output", history[0].metadata)
        self.assertNotIn("secret", history[0].metadata)

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
        self.assertEqual(payload.get("budget_source"), "runtime_cap")
        self.assertIn("selected_recent_count", payload)
        self.assertIn("selected_recent_tokens", payload)

    def test_build_history_without_token_cap_reports_count_fallback(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="fallback"
        )
        self.store.append_message(
            session_id=session.id,
            role="inbound",
            body="short message",
        )
        service = SessionContextService(self.store, token_budget=0)

        history = service.build_history(
            session_id=session.id,
            channel="console",
            target="fallback",
            recent_limit=20,
        )

        self.assertEqual([item.body for item in history], ["short message"])
        events = self.store.list_events(
            session_id=session.id,
            event_type_prefix="session.context.budget",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["budget_source"], "count_fallback")
        self.assertEqual(events[0].payload["selected_recent_count"], 1)

    def test_required_context_overflow_uses_shared_error_facts(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="overflow"
        )
        self.store.replace_pins(
            session_id=session.id,
            pins=[PinnedContextEntry(pin_id="p1", source="policy", text="x" * 300)],
        )
        service = SessionContextService(
            self.store,
            token_budget=10,
            chars_per_token=1.0,
        )

        with self.assertRaises(ContextBudgetOverflowError) as raised:
            service.build_history(
                session_id=session.id,
                channel="console",
                target="overflow",
                recent_limit=5,
            )

        facts = error_info_from_exception(raised.exception)
        self.assertEqual(facts.code, "CONTEXT_BUDGET_OVERFLOW")
        self.assertEqual(facts.details["max_tokens"], 10)
        event = self.store.list_events(
            session_id=session.id,
            event_type_prefix="session.context.budget",
        )[0]
        self.assertTrue(event.payload["overflow"])

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
        next_lease = self.store.acquire_session_turn_lease(
            session.id,
            owner="after-enrichment",
            request_id="after-enrichment",
        )
        self.assertTrue(
            self.store.release_session_turn_lease(
                session.id,
                owner=next_lease.owner,
                fence_token=next_lease.fence_token,
            )
        )

    def test_deferred_summary_enrichment_busy_skip_keeps_active_lease(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="summary-busy"
        )
        for role, body in (
            ("inbound", "u1"),
            ("outbound", "a1"),
            ("inbound", "u2"),
            ("outbound", "a2"),
        ):
            self.store.append_message(session_id=session.id, role=role, body=body)
        active = self.store.acquire_session_turn_lease(
            session.id,
            owner="foreground",
            request_id="foreground",
        )
        service = SessionContextService(
            self.store,
            keep_recent_messages=1,
            summary_enrichment_enabled=True,
            summary_enricher=lambda summary: summary + "\n- enriched",
            summary_enrichment_defer=lambda task: task(),
        )

        service.compact_session(session_id=session.id)

        context = self.store.get_session_context(session_id=session.id)
        assert context is not None
        self.assertNotIn("enriched", context.rolling_summary)
        self.assertTrue(
            self.store.renew_session_turn_lease(
                session.id,
                owner=active.owner,
                fence_token=active.fence_token,
            )
        )

    def test_deferred_summary_enrichment_releases_lease_after_write_failure(
        self,
    ) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="summary-write-failure"
        )
        for role, body in (
            ("inbound", "u1"),
            ("outbound", "a1"),
            ("inbound", "u2"),
            ("outbound", "a2"),
        ):
            self.store.append_message(session_id=session.id, role=role, body=body)
        deferred: list[object] = []
        service = SessionContextService(
            self.store,
            keep_recent_messages=1,
            summary_enrichment_enabled=True,
            summary_enricher=lambda summary: summary + "\n- enriched",
            summary_enrichment_defer=deferred.append,
        )
        service.compact_session(session_id=session.id)
        self.assertEqual(len(deferred), 1)

        with patch.object(
            self.store,
            "update_session_context",
            side_effect=RuntimeError("write failed"),
        ):
            deferred[0]()

        next_lease = self.store.acquire_session_turn_lease(
            session.id,
            owner="after-failure",
            request_id="after-failure",
        )
        self.assertGreater(next_lease.fence_token, 0)

    def test_compaction_rejects_stale_turn_fence(self) -> None:
        session = self.store.resolve_session(
            agent_id="main", channel="console", target="stale-compaction"
        )
        for role, body in (
            ("inbound", "u1"),
            ("outbound", "a1"),
            ("inbound", "u2"),
            ("outbound", "a2"),
        ):
            self.store.append_message(session_id=session.id, role=role, body=body)
        stale = self.store.acquire_session_turn_lease(
            session.id,
            owner="stale",
            request_id="stale",
        )
        self.store.release_session_turn_lease(
            session.id,
            owner=stale.owner,
            fence_token=stale.fence_token,
        )
        self.store.acquire_session_turn_lease(
            session.id,
            owner="current",
            request_id="current",
        )
        service = SessionContextService(self.store, keep_recent_messages=1)

        with self.assertRaises(RuntimeSessionTurnFenceError):
            service.compact_session(
                session_id=session.id,
                session_turn_fence_token=stale.fence_token,
            )

        self.assertIsNone(self.store.get_session_context(session_id=session.id))

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
                    exclude_history_message_ids=(),
                    conversation_id=None,
                    thread_id=None,
                )

        asyncio.run(_run())
