from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


class _RecordingArtifactCtl:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def ref_add(self, owner_type: str, owner_id: str, ref_or_sha: str) -> None:
        self.calls.append((owner_type, owner_id, ref_or_sha))


class SQLiteSessionStoreV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "sessctl.db"
        self.artifactctl = _RecordingArtifactCtl()
        self.store = SQLiteSessionStore(self.db_path, artifactctl=self.artifactctl)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_turns_are_recorded_as_events(self) -> None:
        session_id = self.store.create_session(
            initial_agent_id="agent.main", profile_version="pv1"
        )
        self.store.append_turn(session_id, role="user", content="hello")
        self.store.append_turn(session_id, role="assistant", content="hi")

        events = self.store.get_events(session_id)
        event_types = [event["event_type"] for event in events]
        self.assertIn("turn.user", event_types)
        self.assertIn("turn.assistant", event_types)

        seqs = [event["seq"] for event in events]
        self.assertEqual(seqs, sorted(seqs))

    def test_append_turn_adds_artifact_edges_and_ignores_invalid_attachments(
        self,
    ) -> None:
        session_id = self.store.create_session(
            initial_agent_id="agent.main", profile_version="pv1"
        )
        valid_ref = f"artifact://sha256/{'d' * 64}"

        self.store.append_turn(
            session_id,
            role="user",
            content="hello",
            attachments=[valid_ref, "mem://skip", valid_ref, "unknown"],
        )

        self.assertEqual(
            self.artifactctl.calls,
            [("session", session_id, valid_ref)],
        )

    def test_bind_agent_updates_session_and_logs_events(self) -> None:
        session_id = self.store.create_session(
            initial_agent_id="agent.alpha", profile_version="pv1"
        )

        self.store.bind_agent(
            session_id,
            agent_id="agent.beta",
            profile_version="pv2",
            reason="handoff",
        )

        session = self.store.get_session(session_id)
        assert session is not None
        self.assertEqual(session["active_agent_id"], "agent.beta")
        self.assertEqual(session["active_profile_version"], "pv2")

        events = self.store.get_events(
            session_id, types=["agent.switched", "agent.bound"]
        )
        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(events[-1]["event_type"], "agent.bound")

    def test_slice_returns_compact_limited_view(self) -> None:
        session_id = self.store.create_session(
            initial_agent_id="agent.main", profile_version="pv1"
        )

        for idx in range(8):
            role = "user" if idx % 2 == 0 else "assistant"
            self.store.append_turn(session_id, role=role, content=f"msg-{idx}")

        self.store.append_event(
            session_id,
            event_type="tool.call.started",
            payload={"tool_id": "bash"},
            trace_id="trace-1",
            task_id="task-1",
        )
        self.store.append_event(
            session_id,
            event_type="tool.call.completed",
            payload={"tool_id": "bash", "status": "ok"},
            trace_id="trace-1",
            task_id="task-1",
        )
        self.store.append_event(
            session_id,
            event_type="task.opened",
            payload={"task_id": "task-1", "title": "demo", "status": "open"},
            task_id="task-1",
        )
        self.store.put_working_state(session_id, state_inline={"plan_cursor": 3})
        self.store.update_summary(
            session_id, summary_short="short", summary_long="long", based_on_seq=0
        )

        session_slice = self.store.get_slice(
            session_id,
            purpose="act",
            limits={
                "max_turns": 3,
                "max_tool_events": 1,
                "summary_variant": "auto",
                "include_open_tasks": True,
                "include_active_state": True,
            },
        )

        self.assertEqual(session_slice["session_id"], session_id)
        self.assertTrue(session_slice["slice_version"])
        self.assertLessEqual(len(session_slice["recent_turns"]), 3)
        self.assertLessEqual(len(session_slice["recent_tool_events"]), 1)
        self.assertTrue(session_slice["summary"])
        self.assertIn("active_profile_version", session_slice)

    def test_trace_and_task_ids_preserved(self) -> None:
        session_id = self.store.create_session(
            initial_agent_id="agent.main", profile_version="pv1"
        )

        self.store.append_event(
            session_id,
            event_type="llm.request.started",
            payload={"request_id": "r1", "purpose": "act"},
            trace_id="trace-xyz",
            task_id="task-xyz",
        )
        self.store.append_event(
            session_id,
            event_type="job.created",
            payload={"job_id": "task-xyz"},
            trace_id="trace-xyz",
            task_id="task-xyz",
        )

        events = self.store.get_events(session_id)
        llm_event = [
            item for item in events if item["event_type"] == "llm.request.started"
        ][0]
        job_event = [item for item in events if item["event_type"] == "job.created"][0]

        self.assertEqual(llm_event["trace_id"], "trace-xyz")
        self.assertEqual(llm_event["task_id"], "task-xyz")
        self.assertEqual(job_event["trace_id"], "trace-xyz")
        self.assertEqual(job_event["task_id"], "task-xyz")

    def test_recent_turns_include_system_messages(self) -> None:
        session_id = self.store.create_session(
            initial_agent_id="agent.main", profile_version="pv1"
        )
        self.store.append_turn(
            session_id, role="system", content="System instruction block"
        )
        self.store.append_turn(session_id, role="user", content="hello")
        self.store.append_turn(session_id, role="assistant", content="hi")

        turns = self.store.get_recent_turns(session_id, limit_messages=10)
        roles = [str(item.get("role", "")) for item in turns]
        self.assertEqual(roles, ["system", "user", "assistant"])

    def test_snapshot_resume_and_summary_threshold(self) -> None:
        session_id = self.store.create_session(
            initial_agent_id="agent.main", profile_version="pv1"
        )
        self.store.append_event(
            session_id,
            event_type="task.opened",
            payload={"task_id": "t1", "title": "todo", "status": "open"},
            task_id="t1",
        )
        self.store.put_working_state(
            session_id, state_inline={"cursor": 1, "note": "resume me"}
        )

        self.assertTrue(self.store.needs_summary_update(session_id, threshold_events=1))

        self.store.update_summary(
            session_id, summary_short="s", summary_long="l", based_on_seq=100
        )
        self.assertFalse(
            self.store.needs_summary_update(session_id, threshold_events=3)
        )

        snapshot_id = self.store.create_snapshot(session_id)
        self.assertTrue(snapshot_id)

        active_state = self.store.get_active_state(session_id)
        self.assertEqual(active_state.get("cursor"), 1)

        session_slice = self.store.get_slice(
            session_id,
            purpose="resume",
            limits={
                "max_turns": 5,
                "max_tool_events": 5,
                "summary_variant": "short",
                "include_open_tasks": True,
                "include_active_state": True,
            },
        )
        open_task_ids = [item["task_id"] for item in session_slice["open_tasks"]]
        self.assertIn("t1", open_task_ids)
