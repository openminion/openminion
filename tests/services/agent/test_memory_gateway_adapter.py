from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from unittest.mock import Mock

from openminion.modules.context.contracts import MemoryClient
from openminion.modules.context.memory_client import (
    SESSION_START_RECALL_TYPES,
    ContextMemoryClientAdapter,
)
from openminion.modules.memory.errors import MemoryQueryUnavailableError
from openminion.modules.memory.models import MemoryPatchResult, MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.retrieve.errors import RetrieveCtlError
from openminion.services.agent.memory.gateway_adapter import (
    DisabledMemoryGatewayAdapter,
    MemoryServiceGatewayAdapter,
)


def _make_adapter(agent_id: str = "test-agent") -> MemoryServiceGatewayAdapter:
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    return MemoryServiceGatewayAdapter(service, agent_id=agent_id)


class TestMemoryServiceGatewayAdapterEnabled(unittest.TestCase):
    def test_enabled_is_true(self) -> None:
        adapter = _make_adapter()
        self.assertTrue(adapter.enabled)

    def test_init_defaults_retrieve_ctl_none(self) -> None:
        adapter = _make_adapter()
        self.assertIsNone(getattr(adapter, "_retrieve_ctl", None))

    def test_init_stores_retrieve_ctl_reference(self) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        mock_ctl = Mock(name="retrieve_ctl")
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="test-agent",
            retrieve_ctl=mock_ctl,
        )
        self.assertIs(adapter._retrieve_ctl, mock_ctl)  # noqa: SLF001

    def test_derive_patch_id_deterministic(self) -> None:
        adapter = _make_adapter()
        pid1 = adapter.derive_patch_id(
            session_id="sess1", run_id="run1", request_id="req1", user_message="hello"
        )
        pid2 = adapter.derive_patch_id(
            session_id="sess1", run_id="run1", request_id="req1", user_message="hello"
        )
        self.assertEqual(pid1, pid2)
        self.assertIsInstance(pid1, str)
        self.assertRegex(pid1, r"^[0-9a-f]{32}$")

    def test_derive_patch_id_unique_per_session(self) -> None:
        adapter = _make_adapter()
        pid1 = adapter.derive_patch_id(
            session_id="sess1", run_id="run1", request_id="req1", user_message="msg"
        )
        pid2 = adapter.derive_patch_id(
            session_id="sess2", run_id="run1", request_id="req1", user_message="msg"
        )
        self.assertNotEqual(pid1, pid2)

    def test_record_turn_returns_patch_result(self) -> None:
        adapter = _make_adapter()
        result = adapter.record_turn(
            session_id="sess1",
            run_id="run1",
            request_id="req1",
            channel="test",
            target="user",
            user_message="remember: my name is Alice",
            assistant_message="Got it, Alice.",
        )
        self.assertIsInstance(result, MemoryPatchResult)
        self.assertGreaterEqual(result.facts_added, 1)
        self.assertEqual(result.todos_added, 0)
        self.assertEqual(result.todos_completed, 0)
        self.assertTrue(result.patch_id)

    def test_record_turn_extracts_fact_prefix(self) -> None:
        adapter = _make_adapter()
        result = adapter.record_turn(
            session_id="s1",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="fact: the sky is blue",
            assistant_message="",
        )
        self.assertEqual(result.facts_added, 1)

    def test_record_turn_extracts_todo(self) -> None:
        adapter = _make_adapter()
        result = adapter.record_turn(
            session_id="s1",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="todo: review the PR",
            assistant_message="",
        )
        self.assertEqual(result.todos_added, 1)
        self.assertEqual(result.facts_added, 0)

    def test_record_turn_extracts_multiple_directives(self) -> None:
        adapter = _make_adapter()
        result = adapter.record_turn(
            session_id="s1",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message=(
                "remember: I prefer dark mode\ntodo: update tests\ntodo: write docs"
            ),
            assistant_message="",
        )
        self.assertEqual(result.facts_added, 1)
        self.assertEqual(result.todos_added, 2)

    def test_record_turn_done_removes_task(self) -> None:
        adapter = _make_adapter()
        # Add a task first
        adapter.record_turn(
            session_id="s1",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="todo: finish the report",
            assistant_message="",
        )
        # Mark it done
        result = adapter.record_turn(
            session_id="s1",
            run_id="r2",
            request_id="req2",
            channel="c",
            target="t",
            user_message="done: finish the report",
            assistant_message="",
        )
        self.assertGreaterEqual(result.todos_completed, 1)

    def test_record_turn_no_directives(self) -> None:
        adapter = _make_adapter()
        result = adapter.record_turn(
            session_id="s1",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="Hello, how are you?",
            assistant_message="I'm fine!",
        )
        self.assertEqual(result.facts_added, 0)
        self.assertEqual(result.todos_added, 0)
        self.assertEqual(result.todos_completed, 0)

    def test_build_context_returns_string(self) -> None:
        adapter = _make_adapter()
        context = adapter.build_context(session_id="s1", user_message="")
        self.assertIsInstance(context, str)

    def test_build_context_with_metadata_returns_tuple(self) -> None:
        adapter = _make_adapter()
        content, meta = adapter.build_context_with_metadata(
            session_id="s1", user_message=""
        )
        self.assertIsInstance(content, str)
        self.assertIsInstance(meta, dict)
        self.assertIn("memory_envelope_truncated", meta)
        self.assertIn("memory_envelope_limit_chars", meta)

    def test_build_context_includes_recorded_facts(self) -> None:
        adapter = _make_adapter()
        adapter.record_turn(
            session_id="sess-ctx",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="fact: project is called Helios",
            assistant_message="",
        )
        context, _ = adapter.build_context_with_metadata(
            session_id="sess-ctx", user_message=""
        )
        self.assertIn("Helios", context)

    def test_build_context_includes_record_content_when_title_present(self) -> None:
        adapter = _make_adapter(agent_id="minimax-m2-7")
        now = datetime.now(timezone.utc).isoformat()
        adapter._service._store.put(  # noqa: SLF001
            MemoryRecord(
                id="mem_email_visible",
                created_at=now,
                updated_at=now,
                key="fact:user_email",
                source="user_said",
                confidence=0.7,
                scope="agent:minimax-m2-7",
                type="fact",
                title="User email address",
                content="value-visible@example.com",
            )
        )

        context, _ = adapter.build_context_with_metadata(
            session_id="fresh-session",
            user_message="",
        )

        self.assertIn("User email address", context)
        self.assertIn("value-visible@example.com", context)

    def test_contextctl_memory_contract_uses_existing_recall_pipeline(self) -> None:
        adapter = _make_adapter(agent_id="minimax-m2-7")
        now = datetime.now(timezone.utc).isoformat()
        adapter._service._store.put(  # noqa: SLF001
            MemoryRecord(
                id="contextctl-fact",
                created_at=now,
                updated_at=now,
                key="fact:project",
                source="user_said",
                confidence=0.8,
                scope="agent:minimax-m2-7",
                type="fact",
                title="Project name",
                content={"text": "The project is Helios."},
                tags=["project"],
            )
        )

        memory_client = ContextMemoryClientAdapter(adapter)
        facts = memory_client.query_facts(
            session_id="session-1",
            agent_id="minimax-m2-7",
            query="Helios",
            limit=5,
        )
        cards = memory_client.query_memory_cards(
            session_id="session-1",
            agent_id="minimax-m2-7",
            query="Helios",
            limit=5,
        )

        self.assertIsInstance(memory_client, MemoryClient)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].record_id, "contextctl-fact")
        self.assertEqual(facts[0].text, "The project is Helios.")
        self.assertEqual(facts[0].confidence, 0.8)
        self.assertEqual(facts[0].tags, ["project"])
        self.assertEqual([card.record_id for card in cards], ["contextctl-fact"])

    def test_contextctl_session_start_recall_lists_all_supported_types(self) -> None:
        expected_types = (
            "user_preference",
            "procedure",
            "tool_habit",
            "tool_outcome",
            "strategy_outcome",
            "meta_rule_preference",
            "plan_snapshot",
            "meta_insight",
            "correction",
            "session_summary",
            "project_convention",
            "declared_goal",
            "goal_revision",
        )
        self.assertEqual(SESSION_START_RECALL_TYPES, expected_types)
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="agent-a",
            project_id="project-a",
        )
        base_time = datetime(2026, 9, 12, tzinfo=timezone.utc)
        record_ids: list[str] = []
        scopes = ("agent:agent-a", "project:project-a", "global:system")
        for index, record_type in enumerate(expected_types):
            record_id = f"session-start-{index}"
            record_ids.append(record_id)
            timestamp = (base_time + timedelta(seconds=index)).isoformat()
            store.put(
                MemoryRecord(
                    id=record_id,
                    created_at=timestamp,
                    updated_at=timestamp,
                    scope=scopes[index % len(scopes)],
                    type=record_type,
                    title=record_type,
                    content={"text": f"durable {record_type}"},
                )
            )
        store.put(
            MemoryRecord(
                id="other-agent-memory",
                created_at=base_time.isoformat(),
                updated_at=base_time.isoformat(),
                scope="agent:other",
                type="user_preference",
                title="other",
                content={"text": "unrelated scope"},
            )
        )
        memory_client = ContextMemoryClientAdapter(adapter)

        recalled = memory_client.recall_session_start_memory(
            session_id="new-session",
            agent_id="agent-a",
            query="no overlap",
            turn_index=0,
            limit=len(record_ids),
        )
        limited = memory_client.recall_session_start_memory(
            session_id="new-session",
            agent_id="agent-a",
            query="different query",
            turn_index=0,
            limit=3,
        )
        later = memory_client.recall_session_start_memory(
            session_id="new-session",
            agent_id="agent-a",
            query="no overlap",
            turn_index=1,
            limit=len(record_ids),
        )

        self.assertEqual(
            [card.record_id for card in recalled],
            list(reversed(record_ids)),
        )
        self.assertEqual(
            {card.record_type for card in recalled},
            set(expected_types),
        )
        self.assertEqual(
            [card.record_id for card in limited],
            list(reversed(record_ids))[:3],
        )
        self.assertEqual(later, [])

    def test_contextctl_mid_session_recall_uses_typed_query(self) -> None:
        adapter = _make_adapter(agent_id="agent-a")
        adapter.recall_context = Mock(wraps=adapter.recall_context)
        memory_client = ContextMemoryClientAdapter(adapter)

        memory_client.recall_mid_session_memory(
            session_id="session-a",
            agent_id="agent-a",
            turn_index=2,
            latest_user_message="continue",
            intent_ids=["intent-a"],
            intent_statuses=["active"],
            active_skill_id="python",
            resolved_skill_ids=["python"],
            plan_cursor=1,
            plan_step_ids=["step-a"],
            recent_tool_families=["file"],
            limit=4,
        )

        self.assertEqual(
            adapter.recall_context.call_args.kwargs["query"],
            "continue intent-a active python cursor-1 step-a file",
        )

    def test_contextctl_recent_artifact_recall_uses_bounded_project_scopes(
        self,
    ) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="agent-a",
            project_id="project-a",
        )
        now = datetime.now(timezone.utc)

        def put_artifact(
            record_id: str,
            *,
            scope: str,
            source_session_id: str = "prior-session",
            updated_at: str | None = None,
            expires_at: str | None = None,
        ) -> None:
            timestamp = updated_at or now.isoformat()
            store.put(
                MemoryRecord(
                    id=record_id,
                    created_at=timestamp,
                    updated_at=timestamp,
                    expires_at=expires_at,
                    scope=scope,
                    type="artifact_digest",
                    title=record_id,
                    content={
                        "session_id": source_session_id,
                        "artifact_path": f"/workspace/{record_id}.py",
                    },
                )
            )

        put_artifact("eligible-agent", scope="agent:agent-a")
        put_artifact(
            "eligible-project",
            scope="project:project-a",
            updated_at=(now - timedelta(seconds=1)).isoformat(),
        )
        put_artifact(
            "current-session",
            scope="project:project-a",
            source_session_id="session-a",
        )
        put_artifact(
            "expired",
            scope="project:project-a",
            expires_at=(now - timedelta(seconds=1)).isoformat(),
        )
        put_artifact(
            "stale",
            scope="agent:agent-a",
            updated_at=(now - timedelta(days=15)).isoformat(),
        )
        put_artifact("global", scope="global:system")
        put_artifact("other-project", scope="project:other")
        adapter.list_records = Mock(wraps=adapter.list_records)
        memory_client = ContextMemoryClientAdapter(adapter)

        recalled = memory_client.recall_recent_session_artifacts(
            session_id="session-a",
            agent_id="agent-a",
            max_results=2,
            max_session_age=14,
        )

        self.assertEqual(
            {item.record_id for item in recalled},
            {"eligible-agent", "eligible-project"},
        )
        options = adapter.list_records.call_args.args[0]
        self.assertEqual(options.scopes, ["agent:agent-a", "project:project-a"])
        self.assertEqual(options.types, ["artifact_digest"])
        self.assertEqual(options.limit, 8)

    def test_contextctl_public_scope_and_recall_owners(self) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="agent-a",
            project_id="project-a",
        )
        now = datetime.now(timezone.utc).isoformat()
        store.put(
            MemoryRecord(
                id="public-recall-fact",
                created_at=now,
                updated_at=now,
                scope="project:project-a",
                type="fact",
                title="Public recall",
                content={"text": "The release marker is cobalt."},
                confidence=0.9,
            )
        )

        scopes = adapter.context_scopes(session_id="session-a")
        hits, _ = adapter.recall_context(
            session_id="session-a",
            query="cobalt",
            scopes=scopes,
        )

        self.assertEqual(
            scopes,
            [
                "session:session-a",
                "agent:agent-a",
                "project:project-a",
                "global:system",
            ],
        )
        self.assertEqual(
            adapter.context_scopes(include_global=False),
            [
                "agent:agent-a",
                "project:project-a",
            ],
        )
        self.assertEqual(
            [item["meta"]["record_id"] for item in hits],
            ["public-recall-fact"],
        )
        self.assertFalse(hasattr(adapter, "_long_term_scopes"))
        self.assertFalse(hasattr(adapter, "_recall_hits"))

    def test_contextctl_public_procedure_owner(self) -> None:
        service = Mock()
        procedure = object()
        service.get_procedure.return_value = procedure
        adapter = MemoryServiceGatewayAdapter(service, agent_id="agent-a")
        memory_client = ContextMemoryClientAdapter(adapter)

        self.assertIs(adapter.get_procedure(procedure_id="procedure-a"), procedure)
        self.assertIs(
            memory_client.get_procedure(procedure_id="procedure-a"),
            procedure,
        )
        self.assertEqual(service.get_procedure.call_count, 2)
        service.get_procedure.assert_called_with(procedure_id="procedure-a")

    def test_build_retrieval_context_returns_string(self) -> None:
        adapter = _make_adapter()
        content = adapter.build_retrieval_context(
            session_id="s1", user_message="what is the project name?"
        )
        self.assertIsInstance(content, str)

    def test_build_retrieval_context_with_metadata_returns_tuple(self) -> None:
        adapter = _make_adapter()
        content, meta = adapter.build_retrieval_context_with_metadata(
            session_id="s1", user_message="what do I like?"
        )
        self.assertIsInstance(content, str)
        self.assertIsInstance(meta, dict)

    def test_registers_close_callback_with_bound_write_session_summary(self) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        session_context = Mock(name="session_context")
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="callback-agent",
            session_context=session_context,
        )
        session_context.register_close_callback.assert_called_once()
        callback = session_context.register_close_callback.call_args.args[0]
        self.assertTrue(callable(callback))
        self.assertIs(getattr(callback, "__self__", None), adapter)
        self.assertEqual(getattr(callback, "__name__", ""), "write_session_summary")

    def test_trace_monkeypatch_receives_memory_turn_recorded_event(self) -> None:
        adapter = _make_adapter()
        adapter._trace = Mock(name="_trace")  # type: ignore[method-assign]
        adapter.record_turn(
            session_id="trace-sess",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="fact: trace hook fact",
            assistant_message="ok",
        )
        event_names = [call.args[0] for call in adapter._trace.call_args_list]  # type: ignore[attr-defined]
        self.assertIn("memory.turn.recorded", event_names)

    def test_first_turn_preamble_is_cached_per_session(self) -> None:
        class _FakeSessionContext:
            def get_turn_count(self, *, session_id: str) -> int:
                return 0

        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="preamble-agent",
            session_context=_FakeSessionContext(),
            session_handoff_max_summaries=3,
        )
        service.upsert_record(
            scope="agent:preamble-agent",
            record_type="session_summary",
            key="session_summary:prev",
            record_patch={
                "title": "Previous session",
                "content": {
                    "summary_text": "Decided to use pytest in the previous session.",
                    "decisions": ["Use pytest"],
                    "open_questions": [],
                    "corrections": [],
                    "topic_keywords": ["pytest"],
                    "turn_count": 4,
                },
                "tags": ["session_summary", "prev"],
                "entities": ["pytest"],
                "source": "validated",
                "confidence": 0.8,
            },
        )

        first_context, _ = adapter.build_context_with_metadata(
            session_id="fresh-session",
            user_message="",
        )
        second_context, _ = adapter.build_context_with_metadata(
            session_id="fresh-session",
            user_message="",
        )

        self.assertIn("Continuing from recent sessions", first_context)
        self.assertNotIn("Continuing from recent sessions", second_context)

    def test_build_context_keeps_structured_facts_after_historical_summaries(
        self,
    ) -> None:
        class _FakeSessionContext:
            def get_turn_count(self, *, session_id: str) -> int:
                return 0

            def register_close_callback(self, callback) -> None:
                del callback

        service = MemoryService(store=InMemoryMemoryStore())
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="priority-agent",
            session_context=_FakeSessionContext(),
            session_handoff_max_summaries=3,
        )
        now = datetime.now(timezone.utc).isoformat()
        old_summary = MemoryRecord(
            id="summary-old",
            scope="agent:priority-agent",
            type="session_summary",
            key="session_summary:s1",
            title="User provided email address (old@example.com)",
            content={"summary_text": "User provided old@example.com."},
            tags=["session_summary", "s1"],
            created_at=now,
            updated_at=now,
        )
        live_fact = MemoryRecord(
            id="fact-live",
            scope="agent:priority-agent",
            type="fact",
            key="fact:user_email",
            title="User email address",
            content="new@example.com",
            confidence=1.0,
            created_at=now,
            updated_at=now,
        )
        adapter._service.search = Mock(  # type: ignore[method-assign] # noqa: SLF001
            side_effect=lambda options: (
                [old_summary]
                if list(getattr(options, "types", []) or []) == ["session_summary"]
                else [old_summary, live_fact]
            )
        )
        adapter._service.list = Mock(return_value=[])  # type: ignore[method-assign] # noqa: SLF001

        context, _meta = adapter.build_context_with_metadata(
            session_id="fresh-session",
            user_message="What is my email? Answer with only the email address.",
        )

        self.assertIn("## Agent Memory", context)
        self.assertIn("copy remembered values verbatim", context)
        self.assertIn("[key=fact:user_email]", context)
        self.assertIn("new@example.com", context)
        self.assertIn("old@example.com", context)
        self.assertLess(
            context.index("## Continuing from recent sessions"),
            context.index("## Agent Memory"),
        )

    def test_record_turn_clears_last_retrieved_items_after_turn(
        self,
    ) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        retrieve_ctl = Mock(name="retrieve_ctl")
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="feedback-agent",
            retrieve_ctl=retrieve_ctl,
        )
        adapter._last_retrieved_items["feedback-session"] = [
            {
                "text": "preferred shell is zsh",
                "meta": {"unit_id": "unit-1"},
            }
        ]

        adapter.record_turn(
            session_id="feedback-session",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="what shell should I use?",
            assistant_message="You said the preferred shell is zsh.",
        )

        self.assertNotIn("feedback-session", adapter._last_retrieved_items)  # noqa: SLF001
        retrieve_ctl.set_feedback_scores.assert_not_called()

    def test_query_bridge_calls_retrieve_ctl(self) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        retrieve_ctl = Mock(name="retrieve_ctl")
        retrieve_ctl.retrieve.return_value = [
            {"text": "python 3.12"},
            {"text": "fastapi"},
        ]
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="query-agent",
            retrieve_ctl=retrieve_ctl,
        )
        adapter.record_turn(
            session_id="s-query",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="fact: local memory context",
            assistant_message="",
        )
        content, _meta = adapter.build_retrieval_context_with_metadata(
            session_id="s-query",
            user_message="what stack are we using?",
        )
        self.assertEqual(retrieve_ctl.retrieve.call_count, 2)
        first_call = retrieve_ctl.retrieve.call_args_list[0].kwargs
        second_call = retrieve_ctl.retrieve.call_args_list[1].kwargs
        assert first_call == {
            "query": "what stack are we using?",
            "purpose": "act",
            "scope": {"session_id": "s-query", "agent_id": "query-agent"},
            "k": 3,
            "strategy": "contextual",
            "filters": {
                "types": ["mem", "episode"],
                "scope_keys": ["session:s-query", "agent:query-agent"],
                "time_window_hours": 168,
                "tags": [],
                "risk_constraints": {},
            },
        }
        assert second_call == {
            "query": "what stack are we using?",
            "purpose": "act",
            "scope": {"session_id": "s-query", "agent_id": "query-agent"},
            "k": 3,
            "strategy": "auto",
            "filters": {
                "types": ["skill", "doc", "artifact"],
                "scope_keys": [],
                "tags": [],
                "risk_constraints": {},
            },
        }
        self.assertIn("python 3.12", content)
        self.assertIn("fastapi", content)

    def test_query_bridge_scope_keys_stable_without_project_context(self) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        retrieve_ctl = Mock(name="retrieve_ctl")
        retrieve_ctl.retrieve.return_value = []
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="query-agent",
            retrieve_ctl=retrieve_ctl,
        )
        adapter.build_retrieval_context_with_metadata(
            session_id="s-no-project",
            user_message="query",
        )
        self.assertEqual(retrieve_ctl.retrieve.call_count, 2)
        first_call_filters = retrieve_ctl.retrieve.call_args_list[0].kwargs["filters"]
        second_call_filters = retrieve_ctl.retrieve.call_args_list[1].kwargs["filters"]
        assert first_call_filters["scope_keys"] == [
            "session:s-no-project",
            "agent:query-agent",
        ]
        assert second_call_filters["scope_keys"] == []

    def test_query_bridge_scope_keys_include_project_context_when_present(self) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        retrieve_ctl = Mock(name="retrieve_ctl")
        retrieve_ctl.retrieve.return_value = []
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="query-agent",
            project_id="proj-42",
            retrieve_ctl=retrieve_ctl,
        )
        adapter.build_retrieval_context_with_metadata(
            session_id="s-project",
            user_message="query",
        )
        self.assertEqual(retrieve_ctl.retrieve.call_count, 2)
        first_call_filters = retrieve_ctl.retrieve.call_args_list[0].kwargs["filters"]
        second_call_filters = retrieve_ctl.retrieve.call_args_list[1].kwargs["filters"]
        assert first_call_filters["scope_keys"] == [
            "session:s-project",
            "agent:query-agent",
            "project:proj-42",
        ]
        assert second_call_filters["scope_keys"] == []

    def test_query_bridge_deduplication(self) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        retrieve_ctl = Mock(name="retrieve_ctl")
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="query-agent",
            retrieve_ctl=retrieve_ctl,
        )
        adapter.record_turn(
            session_id="s-dedup",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="fact: project uses python 312",
            assistant_message="",
        )
        memory_record = service.list(
            ListQueryOptions(scopes=["session:s-dedup"], limit=1)
        )[0]
        retrieve_ctl.retrieve.return_value = [
            {
                "text": "project uses python 312",
                "meta": {"memory_id": memory_record.id},
            },
            {"text": "project uses fastapi", "meta": {"unit_id": "fastapi"}},
        ]
        content, _meta = adapter.build_retrieval_context_with_metadata(
            session_id="s-dedup",
            user_message="what does the project use?",
        )
        self.assertEqual(content.count("project uses python 312"), 1)
        self.assertIn("project uses fastapi", content)

    def test_query_bridge_error_fallback(self) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        retrieve_ctl = Mock(name="retrieve_ctl")
        retrieve_ctl.retrieve.side_effect = RetrieveCtlError(
            "UPSTREAM_UNAVAILABLE", "retrieve down"
        )
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="query-agent",
            retrieve_ctl=retrieve_ctl,
        )
        adapter.record_turn(
            session_id="s-fallback",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="fact: fallback memory value",
            assistant_message="",
        )
        content, _meta = adapter.build_retrieval_context_with_metadata(
            session_id="s-fallback",
            user_message="fallback",
        )
        self.assertEqual(content, "")

    def test_retrieval_context_prefers_structured_facts_over_session_summaries(
        self,
    ) -> None:
        adapter = _make_adapter(agent_id="priority-agent")
        now = datetime.now(timezone.utc).isoformat()
        old_summary = MemoryRecord(
            id="summary-old",
            scope="agent:priority-agent",
            type="session_summary",
            key="session_summary:s1",
            title="User provided email address (old@example.com)",
            content={"summary_text": "User provided old@example.com."},
            tags=["session_summary", "s1"],
            created_at=now,
            updated_at=now,
        )
        new_summary = MemoryRecord(
            id="summary-new",
            scope="agent:priority-agent",
            type="session_summary",
            key="session_summary:s2",
            title="Assistant confirmed new email address",
            content={"summary_text": "User updated email to new@example.com."},
            tags=["session_summary", "s2"],
            created_at=now,
            updated_at=now,
        )
        live_fact = MemoryRecord(
            id="fact-live",
            scope="agent:priority-agent",
            type="fact",
            key="fact:user_email",
            title="User email address",
            content="new@example.com",
            confidence=1.0,
            created_at=now,
            updated_at=now,
        )
        adapter._service.search_semantic = Mock(  # type: ignore[method-assign]
            return_value=[old_summary, new_summary, live_fact]
        )

        content, _meta = adapter.build_retrieval_context_with_metadata(
            session_id="s3",
            user_message="What is my email? Answer with only the email address.",
        )

        self.assertIn("User email address", content)
        self.assertIn("old@example.com", content)
        self.assertLess(
            content.find("User email address"),
            content.find("old@example.com"),
        )

    def test_query_bridge_no_ctl(self) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="query-agent",
            retrieve_ctl=None,
        )
        adapter.record_turn(
            session_id="s-no-ctl",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="fact: memory only result",
            assistant_message="",
        )
        content, _meta = adapter.build_retrieval_context_with_metadata(
            session_id="s-no-ctl",
            user_message="memory only",
        )
        self.assertIn("memory only result", content)

    def test_query_bridge_trace_event_on_retrieve_error(self) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        retrieve_ctl = Mock(name="retrieve_ctl")
        retrieve_ctl.retrieve.side_effect = RetrieveCtlError(
            "UPSTREAM_UNAVAILABLE", "retrieve boom"
        )
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="query-agent",
            retrieve_ctl=retrieve_ctl,
        )
        adapter._trace = Mock(name="_trace")  # type: ignore[method-assign]
        adapter.record_turn(
            session_id="s-trace-err",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="fact: trace error fact",
            assistant_message="",
        )
        adapter.build_retrieval_context_with_metadata(
            session_id="s-trace-err",
            user_message="trace error fact",
        )
        event_names = [call.args[0] for call in adapter._trace.call_args_list]  # type: ignore[attr-defined]
        self.assertIn("memory.retrieval.retrieve_ctl_error", event_names)

    def test_query_bridge_trace_event_dual_query_counts(self) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        retrieve_ctl = Mock(name="retrieve_ctl")
        retrieve_ctl.retrieve.return_value = [
            {"text": "secondary retrieve hit", "meta": {"unit_id": "secondary"}},
        ]
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="query-agent",
            retrieve_ctl=retrieve_ctl,
        )
        adapter._trace = Mock(name="_trace")  # type: ignore[method-assign]
        adapter.record_turn(
            session_id="s-trace-dual",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="fact: primary memory hit",
            assistant_message="",
        )
        adapter.build_retrieval_context_with_metadata(
            session_id="s-trace-dual",
            user_message="primary memory hit",
        )
        dual_query_calls = [
            call
            for call in adapter._trace.call_args_list  # type: ignore[attr-defined]
            if call.args and call.args[0] == "memory.retrieval.dual_query"
        ]
        self.assertTrue(dual_query_calls)
        payload = dual_query_calls[-1].args[1]
        self.assertEqual(payload.get("memory_hits"), 1)
        self.assertEqual(payload.get("retrieve_hits"), 1)
        self.assertEqual(payload.get("merged_hits"), 2)
        self.assertEqual(payload.get("retrieve_ctl_available"), "true")
        self.assertEqual(payload.get("conversational_hits"), 1)
        self.assertEqual(payload.get("knowledge_hits"), 1)

    def test_build_retrieval_context_with_metadata_honors_max_chars_without_900_cap(
        self,
    ) -> None:
        adapter = _make_adapter()
        long_tail = "x" * 220
        for i in range(12):
            adapter.record_turn(
                session_id="s-cap",
                run_id=f"r{i}",
                request_id=f"req{i}",
                channel="c",
                target="t",
                user_message=f"fact: sharedtoken cap-test fact {i} {long_tail}",
                assistant_message="",
            )
        content, meta = adapter.build_retrieval_context_with_metadata(
            session_id="s-cap",
            user_message="sharedtoken",
            max_chars=2000,
        )
        self.assertGreater(
            len(content),
            900,
            "retrieval context should exceed the legacy 900-char cap when max_chars=2000",
        )
        self.assertEqual(meta.get("memory_envelope_limit_chars"), "2000")

    def test_generation_increments(self) -> None:
        adapter = _make_adapter()
        r1 = adapter.record_turn(
            session_id="s1",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="fact: a",
            assistant_message="",
        )
        r2 = adapter.record_turn(
            session_id="s1",
            run_id="r2",
            request_id="req2",
            channel="c",
            target="t",
            user_message="fact: b",
            assistant_message="",
        )
        self.assertGreater(r2.generation, r1.generation)

    def test_remember_explicit_prefix_promotes_to_agent_scope(self) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        adapter = MemoryServiceGatewayAdapter(service, agent_id="promo-agent")
        adapter.record_turn(
            session_id="s1",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="remember: I love Python",
            assistant_message="",
        )
        from openminion.modules.memory.storage.base import ListQueryOptions

        agent_records = service.list(
            ListQueryOptions(scopes=["agent:promo-agent"], limit=20)
        )
        self.assertTrue(
            any("Python" in str(getattr(r, "content", "") or "") for r in agent_records)
        )

    def test_explicit_email_remember_promotes_to_keyed_agent_fact(self) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        adapter = MemoryServiceGatewayAdapter(service, agent_id="promo-agent")

        adapter.record_turn(
            session_id="s-email",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="remember: my work email is alpha@example.com",
            assistant_message="",
        )

        live = service.find_record_by_normalized_key(
            scope="agent:promo-agent",
            record_type="fact",
            normalized_key="fact:user_email",
        )
        self.assertIsNotNone(live)
        self.assertEqual(getattr(live, "title", None), "User email address")
        self.assertEqual(getattr(live, "content", None), "alpha@example.com")

    def test_correction_prefix_supersedes_prior_keyed_email_fact(self) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        adapter = MemoryServiceGatewayAdapter(service, agent_id="promo-agent")

        adapter.record_turn(
            session_id="s-email",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="remember: my work email is alpha@example.com",
            assistant_message="",
        )
        adapter.record_turn(
            session_id="s-email",
            run_id="r2",
            request_id="req2",
            channel="c",
            target="t",
            user_message=(
                "Correction: my work email is beta@example.com. Remember this instead."
            ),
            assistant_message="",
        )

        live = service.find_record_by_normalized_key(
            scope="agent:promo-agent",
            record_type="fact",
            normalized_key="fact:user_email",
        )
        self.assertIsNotNone(live)
        self.assertEqual(getattr(live, "content", None), "beta@example.com")

        history = list(service._store._records._records.values())  # noqa: SLF001
        email_history = [
            record
            for record in history
            if str(getattr(record, "key", "") or "").strip() == "fact:user_email"
        ]
        self.assertEqual(len(email_history), 2)
        stale_rows = [
            record
            for record in email_history
            if getattr(record, "superseded_by_id", None)
        ]
        self.assertEqual(len(stale_rows), 1)
        self.assertEqual(getattr(stale_rows[0], "content", None), "alpha@example.com")

    def test_ingestion_bridge_fires_on_remember(self) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        retrieve_ctl = Mock(name="retrieve_ctl")
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="ingest-agent",
            retrieve_ctl=retrieve_ctl,
        )
        adapter.record_turn(
            session_id="s-ingest",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="remember: ingestion bridge fact",
            assistant_message="",
        )
        retrieve_ctl.ingest_memory.assert_called_once()

    def test_ingestion_bridge_silent_on_plain_fact(self) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        retrieve_ctl = Mock(name="retrieve_ctl")
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="ingest-agent",
            retrieve_ctl=retrieve_ctl,
        )
        adapter.record_turn(
            session_id="s-ingest",
            run_id="r2",
            request_id="req2",
            channel="c",
            target="t",
            user_message="fact: plain fact only",
            assistant_message="",
        )
        retrieve_ctl.ingest_memory.assert_not_called()

    def test_ingestion_bridge_error_does_not_propagate(self) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        retrieve_ctl = Mock(name="retrieve_ctl")
        retrieve_ctl.ingest_memory.side_effect = RuntimeError("ingest failed")
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="ingest-agent",
            retrieve_ctl=retrieve_ctl,
        )
        result = adapter.record_turn(
            session_id="s-ingest",
            run_id="r3",
            request_id="req3",
            channel="c",
            target="t",
            user_message="remember: ingestion error path",
            assistant_message="",
        )
        self.assertGreaterEqual(result.facts_added, 1)

    def test_ingestion_bridge_trace_event_on_success(self) -> None:
        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        retrieve_ctl = Mock(name="retrieve_ctl")
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="ingest-agent",
            retrieve_ctl=retrieve_ctl,
        )
        adapter._trace = Mock(name="_trace")  # type: ignore[method-assign]
        adapter.record_turn(
            session_id="s-ingest",
            run_id="r4",
            request_id="req4",
            channel="c",
            target="t",
            user_message="remember: ingestion trace event",
            assistant_message="",
        )
        trace_event_names = [call.args[0] for call in adapter._trace.call_args_list]  # type: ignore[attr-defined]
        self.assertIn("memory.ingest_memory.called", trace_event_names)


class TestDisabledMemoryGatewayAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = DisabledMemoryGatewayAdapter(agent_id="test-agent")

    def test_enabled_is_false(self) -> None:
        self.assertFalse(self.adapter.enabled)

    def test_derive_patch_id_returns_empty(self) -> None:
        pid = self.adapter.derive_patch_id(
            session_id="s", run_id="r", request_id="req", user_message="hi"
        )
        self.assertEqual(pid, "")

    def test_record_turn_returns_zeros(self) -> None:
        result = self.adapter.record_turn(
            session_id="s",
            run_id="r",
            request_id="req",
            channel="c",
            target="t",
            user_message="fact: something important",
            assistant_message="ok",
        )
        self.assertIsInstance(result, MemoryPatchResult)
        self.assertEqual(result.facts_added, 0)
        self.assertEqual(result.todos_added, 0)
        self.assertEqual(result.todos_completed, 0)

    def test_build_context_returns_empty(self) -> None:
        content = self.adapter.build_context(session_id="s", user_message="hello")
        self.assertEqual(content, "")

    def test_build_context_with_metadata_returns_empty_tuple(self) -> None:
        content, meta = self.adapter.build_context_with_metadata(
            session_id="s", user_message=""
        )
        self.assertEqual(content, "")
        self.assertIsInstance(meta, dict)

    def test_build_retrieval_context_returns_empty(self) -> None:
        content = self.adapter.build_retrieval_context(
            session_id="s", user_message="query"
        )
        self.assertEqual(content, "")

    def test_build_retrieval_context_with_metadata_returns_empty_tuple(self) -> None:
        content, meta = self.adapter.build_retrieval_context_with_metadata(
            session_id="s", user_message="query"
        )
        self.assertEqual(content, "")
        self.assertIsInstance(meta, dict)

    def test_contextctl_public_operations_are_neutral_but_listing_stays_closed(
        self,
    ) -> None:
        self.assertEqual(self.adapter.context_scopes(session_id="s"), [])
        self.assertEqual(
            self.adapter.recall_context(
                session_id="s",
                query="q",
                scopes=["agent:test-agent"],
            ),
            ([], None),
        )
        self.assertIsNone(self.adapter.get_procedure(procedure_id="p"))
        with self.assertRaises(MemoryQueryUnavailableError):
            self.adapter.list_records(ListQueryOptions(scopes=["agent:test-agent"]))


class TestDebugSnapshot(unittest.TestCase):
    def test_export_debug_snapshot_creates_files(self) -> None:
        adapter = _make_adapter()
        adapter.record_turn(
            session_id="snap-sess",
            run_id="r1",
            request_id="req1",
            channel="c",
            target="t",
            user_message="fact: debug test fact",
            assistant_message="ok",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = adapter.export_debug_snapshot(
                Path(tmpdir), session_id="snap-sess"
            )
            files = {f.name for f in out_path.iterdir()}
            required = {
                "session_records.json",
                "agent_records.json",
                "global_records.json",
                "capsule_preview.md",
                "retrieval_preview.md",
                "snapshot.json",
                "README.txt",
            }
            self.assertEqual(required, files, f"Missing files: {required - files}")
