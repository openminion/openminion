from __future__ import annotations

import importlib
import unittest

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


def _make_store(tmp_path: str) -> SQLiteSessionStore:
    import tempfile

    db = tempfile.mktemp(
        suffix=".db", prefix="sessctl-integration-", dir=tmp_path if tmp_path else None
    )
    return SQLiteSessionStore(db)


class SessctlSessionClientTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        self._store = _make_store(self._tmpdir.name)
        self._sid = self._store.create_session(
            initial_agent_id="agent.main",
            profile_version="pv1",
            title="Integration test session",
        )

    def tearDown(self) -> None:
        self._store.close()
        self._tmpdir.cleanup()

    def _make_client(self):
        from openminion.modules.session.runtime.session_client import (
            SessctlSessionClient,
        )

        return SessctlSessionClient(self._store)

    def test_get_slice_returns_session_slice(self) -> None:
        self._store.append_turn(self._sid, "user", "Hello sessctl")
        self._store.append_turn(self._sid, "assistant", "Hello back")

        client = self._make_client()

        try:
            from openminion.modules.context.schemas import SessionSlice  # type: ignore[import]
        except ImportError:
            self.skipTest("openminion-context not on PYTHONPATH")

        result = client.get_slice(
            session_id=self._sid,
            purpose="act",
            limits={"max_turns": 4, "max_tool_events": 4},
        )
        self.assertIsInstance(result, SessionSlice)
        self.assertEqual(result.session_id, self._sid)
        self.assertIsNotNone(result.slice_version)
        self.assertGreater(len(result.slice_version), 0)

    def test_get_slice_includes_recent_turns(self) -> None:
        self._store.append_turn(self._sid, "user", "Message A")
        self._store.append_turn(self._sid, "assistant", "Reply A")

        client = self._make_client()
        try:
            from openminion.modules.context.schemas import SessionSlice  # type: ignore[import]
        except ImportError:
            self.skipTest("openminion-context not on PYTHONPATH")

        result = client.get_slice(
            session_id=self._sid,
            purpose="act",
            limits={"max_turns": 8, "max_tool_events": 4},
        )
        self.assertIsInstance(result, SessionSlice)
        self.assertGreaterEqual(len(result.recent_turns), 2)
        roles = {t.role for t in result.recent_turns}
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)

    def test_get_slice_stable_version_unchanged(self) -> None:
        client = self._make_client()
        try:
            importlib.import_module("openminion.modules.context.schemas")
        except ImportError:
            self.skipTest("openminion-context not on PYTHONPATH")

        r1 = client.get_slice(
            session_id=self._sid, purpose="act", limits={"max_turns": 4}
        )
        r2 = client.get_slice(
            session_id=self._sid, purpose="act", limits={"max_turns": 4}
        )
        self.assertEqual(r1.slice_version, r2.slice_version)

    def test_get_slice_version_changes_after_write(self) -> None:
        client = self._make_client()
        try:
            importlib.import_module("openminion.modules.context.schemas")
        except ImportError:
            self.skipTest("openminion-context not on PYTHONPATH")

        r1 = client.get_slice(
            session_id=self._sid, purpose="act", limits={"max_turns": 4}
        )
        self._store.append_turn(self._sid, "user", "New message — changes version")
        r2 = client.get_slice(
            session_id=self._sid, purpose="act", limits={"max_turns": 4}
        )
        self.assertNotEqual(r1.slice_version, r2.slice_version)


class CanonicalEventLoggerTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        self._store = _make_store(self._tmpdir.name)
        self._sid = self._store.create_session(
            initial_agent_id="agent.main", profile_version="pv1"
        )

    def tearDown(self) -> None:
        self._store.close()
        self._tmpdir.cleanup()

    def _make_logger(self):
        from openminion.modules.brain.diagnostics.events import (
            CanonicalEventLogger,
        )

        return CanonicalEventLogger(
            session_api=self._store,
            session_id=self._sid,
            agent_id="agent.main",
        )

    def test_emit_llm_event_uses_agent_actor_type(self) -> None:
        try:
            logger = self._make_logger()
        except ImportError:
            self.skipTest("openminion-brain not on PYTHONPATH")

        eid = logger.emit(
            "llm.request.started", {"purpose": "act"}, trace_id="t1", status="started"
        )
        self.assertIsNotNone(eid)

        events = self._store.get_events(
            self._sid, after_seq=-1, types=["llm.request.started"]
        )
        llm_events = [e for e in events if e["event_type"] == "llm.request.started"]
        self.assertGreater(len(llm_events), 0)
        # actor_type should be "agent" for llm.* events
        last_event = llm_events[-1]
        self.assertEqual(last_event.get("actor_type"), "agent")

    def test_emit_tool_event_uses_tool_actor_type(self) -> None:
        try:
            logger = self._make_logger()
        except ImportError:
            self.skipTest("openminion-brain not on PYTHONPATH")

        logger.emit(
            "tool.execute.started",
            {"tool_name": "search"},
            trace_id="t2",
            status="started",
        )
        events = self._store.get_events(
            self._sid, after_seq=-1, types=["tool.execute.started"]
        )
        tool_events = [e for e in events if e["event_type"] == "tool.execute.started"]
        self.assertGreater(len(tool_events), 0)
        self.assertEqual(tool_events[-1].get("actor_type"), "tool")

    def test_emit_preserves_trace_id(self) -> None:
        try:
            logger = self._make_logger()
        except ImportError:
            self.skipTest("openminion-brain not on PYTHONPATH")

        trace_id = "trace-integration-test-001"
        logger.emit("task.created", {"task_id": "tid-1"}, trace_id=trace_id)
        events = self._store.get_events(self._sid, after_seq=-1, types=["task.created"])
        task_events = [e for e in events if e["event_type"] == "task.created"]
        self.assertGreater(len(task_events), 0)
        self.assertEqual(task_events[-1].get("trace_id"), trace_id)

    def test_emit_turn_event_uses_user_actor_type(self) -> None:
        from openminion.modules.brain.diagnostics.events import (
            CanonicalEventLogger,
        )

        logger = CanonicalEventLogger(
            session_api=self._store,
            session_id=self._sid,
            agent_id="agent.main",
        )
        logger.emit("turn.user", {"text": "hi"})
        events = self._store.get_events(self._sid, after_seq=-1, types=["turn.user"])
        turn_events = [e for e in events if e["event_type"] == "turn.user"]
        self.assertGreater(len(turn_events), 0)
        self.assertEqual(turn_events[-1].get("actor_type"), "user")


class SessctlStandaloneTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        self._store = _make_store(self._tmpdir.name)
        self._sid = self._store.create_session(initial_agent_id="agent.main")

    def tearDown(self) -> None:
        self._store.close()
        self._tmpdir.cleanup()

    def test_append_and_get_events(self) -> None:
        self._store.append_turn(self._sid, "user", "hello")
        events = self._store.get_events(self._sid, after_seq=-1)
        self.assertIsInstance(events, list)
        self.assertGreater(len(events), 0)

    def test_slice_version_is_stable(self) -> None:
        s1 = self._store.get_slice(self._sid, "act", {"max_turns": 4})
        s2 = self._store.get_slice(self._sid, "act", {"max_turns": 4})
        self.assertEqual(s1["slice_version"], s2["slice_version"])

    def test_slice_version_changes_on_write(self) -> None:
        s1 = self._store.get_slice(self._sid, "act", {"max_turns": 4})
        self._store.append_turn(self._sid, "user", "new turn changes slice")
        s2 = self._store.get_slice(self._sid, "act", {"max_turns": 4})
        self.assertNotEqual(s1["slice_version"], s2["slice_version"])

    def test_bind_agent_emits_canonical_events(self) -> None:
        self._store.bind_agent(
            self._sid, "agent.beta", profile_version="pv2", reason="switch"
        )
        events = self._store.get_events(
            self._sid, after_seq=-1, types=["agent.bound", "agent.switched"]
        )
        event_types = {e["event_type"] for e in events}
        self.assertIn("agent.bound", event_types)
