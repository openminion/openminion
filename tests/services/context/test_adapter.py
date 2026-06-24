from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openminion.modules.context.schemas import SessionSlice
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database
from openminion.services.context.adapter import (
    ContextCtlGatewayAdapter,
    ContextCtlMessage,
)
from openminion.services.context.adapter import _RuntimeMappedSessionClient


def _logger():
    import logging

    return logging.getLogger("openminion.tests.context-adapter")


def _adapter(**kwargs) -> ContextCtlGatewayAdapter:
    return ContextCtlGatewayAdapter(logger=_logger(), **kwargs)


class AdapterConstructionTests(unittest.TestCase):
    def test_default_adapter_is_enabled(self) -> None:
        adapter = _adapter()
        self.assertTrue(adapter.is_enabled)
        self.assertFalse(adapter.is_dual_render)

    def test_from_env_reads_dual_render_flag(self) -> None:
        with patch.dict(os.environ, {"CONTEXTCTL_DUAL_RENDER": "true"}):
            adapter = ContextCtlGatewayAdapter.from_env(logger=_logger())
        self.assertTrue(adapter.is_enabled)
        self.assertTrue(adapter.is_dual_render)

    def test_from_env_reads_bool_aliases_via_shared_env_parser(self) -> None:
        with patch.dict(os.environ, {"CONTEXTCTL_DUAL_RENDER": "1"}):
            adapter = ContextCtlGatewayAdapter.from_env(logger=_logger())
        self.assertTrue(adapter.is_dual_render)


class SelectHistoryTests(unittest.TestCase):
    def test_no_ctx_messages_returns_history(self) -> None:
        adapter = _adapter()
        history = [object(), object()]
        result = adapter.select_history(
            history=history,
            session_id="s1",
            agent_id="a1",
            query="hello",
            contextctl_messages=None,
        )
        self.assertIs(result, history)

    def test_ctx_messages_are_used(self) -> None:
        adapter = _adapter()
        history = [object()]
        ctxctl_msgs = [
            ContextCtlMessage(role="system", content="identity context"),
            ContextCtlMessage(role="user", content="hello"),
        ]
        result = adapter.select_history(
            history=history,
            session_id="s1",
            agent_id="a1",
            query="hello",
            contextctl_messages=ctxctl_msgs,
        )
        self.assertEqual(len(result), 2)


class DualRenderTests(unittest.TestCase):
    def test_dual_render_logs_parity(self) -> None:
        adapter = _adapter(contextctl_dual_render=True)

        logged_calls: list[tuple] = []

        class _CapturingLogger:
            def info(self, msg, *args, **kwargs):
                logged_calls.append(("info", msg, args))

            def warning(self, msg, *args, **kwargs):
                pass

            def error(self, msg, *args, **kwargs):
                pass

            def debug(self, msg, *args, **kwargs):
                pass

        adapter._log = _CapturingLogger()  # type: ignore[assignment]

        ctxctl_msgs = [
            ContextCtlMessage(role="system", content="ctx"),
            ContextCtlMessage(role="user", content="hello"),
        ]
        adapter.select_history(
            history=[object(), object(), object()],
            session_id="s1",
            agent_id="a1",
            query="hello",
            contextctl_messages=ctxctl_msgs,
        )
        self.assertTrue(any("dual_render" in msg for _, msg, _ in logged_calls))


class BuildContextCtlMessagesTests(unittest.TestCase):
    def test_returns_messages_when_context_build_succeeds(self) -> None:
        adapter = _adapter()
        fake_msgs = [
            ContextCtlMessage(role="system", content="hi"),
            ContextCtlMessage(role="user", content="q"),
        ]
        with patch.object(adapter, "_call_ctxctl", return_value=fake_msgs):
            result = adapter.build_ctxctl_messages(
                session_id="s1", agent_id="a1", query="q"
            )
        self.assertEqual(len(result or []), 2)

    def test_returns_none_on_context_build_exception(self) -> None:
        adapter = _adapter()
        with patch.object(
            adapter, "_call_ctxctl", side_effect=RuntimeError("context build failed")
        ):
            result = adapter.build_ctxctl_messages(
                session_id="s1", agent_id="a1", query="hello"
            )
        self.assertIsNone(result)

    def test_uses_injected_session_client_for_ctxctl_build(self) -> None:
        class _MappedSessionClient:
            contract_version = "v1"

            def get_slice(
                self, *, session_id: str, purpose: str, limits: dict
            ) -> SessionSlice:
                del purpose, limits
                return SessionSlice(
                    session_id=session_id,
                    slice_version="mapped:v1",
                    last_event_id="1",
                    summary_short="mapped summary",
                    summary_long="mapped summary long",
                    recent_turns=[],
                    open_tasks=[],
                    active_state={},
                    recent_tool_events=[],
                    prompt_context_id=None,
                    checkpoint_id=None,
                    seed_bundle_id=None,
                    archive_refs=[],
                )

        adapter = _adapter(session_client=_MappedSessionClient())
        result = adapter.build_ctxctl_messages(
            session_id="s1", agent_id="a1", query="hello"
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(any("mapped summary" in item.content for item in result))


class RuntimeMappedSessionClientTests(unittest.TestCase):
    def test_maps_runtime_session_store_into_session_slice(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "openminion.db"
            migrate_database(db_path)
            connection = connect_database(db_path)
            try:
                store = SessionStore(connection)
                session = store.resolve_session(
                    agent_id="main", channel="console", target="chat"
                )
                store.append_message(
                    session_id=session.id, role="inbound", body="hello"
                )
                store.append_message(
                    session_id=session.id, role="outbound", body="world"
                )
                context = store.ensure_session_context(session_id=session.id)
                store.update_session_context(
                    session_id=session.id,
                    summary_short="short summary line",
                    rolling_summary="short summary line\nsecond line",
                    version=context.version + 1,
                )
                store.append_event(
                    session_id=session.id,
                    event_type="session.compaction.archive",
                    payload={"relative_path": "archives/chunk-1.jsonl"},
                )
                store.append_event(
                    session_id=session.id,
                    event_type="tool.call",
                    payload={
                        "tool_name": "web.search",
                        "summary": "searched docs",
                        "artifact_refs": ["artifact://a1"],
                    },
                )
            finally:
                connection.close()

            client = _RuntimeMappedSessionClient(db_path)
            slice_v15 = client.get_slice(
                session_id=session.id,
                purpose="act",
                limits={"recent_turn_limit": 5, "tool_events_limit": 3},
            )
            self.assertEqual(slice_v15.slice_version, "runtime-map:v1")
            self.assertEqual(slice_v15.summary_short, "short summary line")
            self.assertEqual(slice_v15.summary_long, "short summary line\nsecond line")
            self.assertEqual(
                [turn.role for turn in slice_v15.recent_turns], ["user", "assistant"]
            )
            self.assertIn("archives/chunk-1.jsonl", slice_v15.archive_refs)
            self.assertEqual(len(slice_v15.recent_tool_events), 1)
            self.assertEqual(slice_v15.recent_tool_events[0].tool_name, "web.search")


class CGWE06MemoryBridgeParityTests(unittest.TestCase):
    class _RecordingMemoryClient:
        contract_version = "v1"

        def __init__(self) -> None:
            self.calls: list[str] = []

        def query_facts(self, **kwargs):
            self.calls.append("query_facts")
            return []

        def query_memory_cards(self, **kwargs):
            self.calls.append("query_memory_cards")
            return []

        def recall_session_start_memory(self, **kwargs):
            self.calls.append("recall_session_start_memory")
            return []

        def recall_mid_session_memory(self, **kwargs):
            self.calls.append("recall_mid_session_memory")
            return []

        def recall_recent_session_artifacts(self, **kwargs):
            self.calls.append("recall_recent_session_artifacts")
            return []

        def get_procedure(self, **kwargs):
            self.calls.append("get_procedure")
            return None

    def test_constructor_accepts_memory_client(self) -> None:
        memory = self._RecordingMemoryClient()
        adapter = _adapter(memory_client=memory)
        # Internal: confirm the adapter holds the injected client; the
        # downstream `_call_ctxctl` path will pass it to ContextCtlService
        # in place of `_NullMemoryClient`.
        self.assertIs(adapter._memory_client, memory)

    def test_from_env_threads_memory_client(self) -> None:
        memory = self._RecordingMemoryClient()
        adapter = ContextCtlGatewayAdapter.from_env(
            agent_id="a1", memory_client=memory, logger=_logger()
        )
        self.assertIs(adapter._memory_client, memory)

    def test_default_memory_client_is_none_until_injected(self) -> None:
        adapter = _adapter()
        self.assertIsNone(adapter._memory_client)


class CGWE05PreEnableHardeningTests(unittest.TestCase):
    def _seed(self, db_path: Path) -> str:
        migrate_database(db_path)
        connection = connect_database(db_path)
        try:
            store = SessionStore(connection)
            session = store.resolve_session(
                agent_id="main", channel="console", target="chat"
            )
            store.append_message(session_id=session.id, role="inbound", body="hello")
        finally:
            connection.close()
        return session.id

    def test_migration_runs_at_most_once_per_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "openminion.db"
            session_id = self._seed(db_path)
            client = _RuntimeMappedSessionClient(db_path)
            try:
                with patch(
                    "openminion.modules.storage.runtime.migrations.migrate_database",
                    wraps=migrate_database,
                ) as migrate_spy:
                    for _ in range(5):
                        slice_v15 = client.get_slice(
                            session_id=session_id,
                            purpose="act",
                            limits={"recent_turn_limit": 5, "tool_events_limit": 3},
                        )
                        self.assertEqual(slice_v15.slice_version, "runtime-map:v1")
                self.assertEqual(
                    migrate_spy.call_count,
                    1,
                    "migrate_database must run exactly once across 5 get_slice "
                    "calls — pre-CGWE-05 it ran 5 times (per-call churn)",
                )
            finally:
                client.close()

    def test_connection_reused_across_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "openminion.db"
            session_id = self._seed(db_path)
            client = _RuntimeMappedSessionClient(db_path)
            try:
                # Force the first call so connection is opened.
                client.get_slice(
                    session_id=session_id,
                    purpose="act",
                    limits={"recent_turn_limit": 5, "tool_events_limit": 3},
                )
                first_conn = client._connection
                self.assertIsNotNone(first_conn)
                # Subsequent calls must reuse the same connection object.
                for _ in range(3):
                    client.get_slice(
                        session_id=session_id,
                        purpose="act",
                        limits={"recent_turn_limit": 5, "tool_events_limit": 3},
                    )
                    self.assertIs(client._connection, first_conn)
            finally:
                client.close()

    def test_close_releases_connection_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "openminion.db"
            session_id = self._seed(db_path)
            client = _RuntimeMappedSessionClient(db_path)
            client.get_slice(
                session_id=session_id,
                purpose="act",
                limits={"recent_turn_limit": 5, "tool_events_limit": 3},
            )
            self.assertIsNotNone(client._connection)
            client.close()
            self.assertIsNone(client._connection)
            # Idempotent — no error on second close.
            client.close()
