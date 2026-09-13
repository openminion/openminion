from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock

from openminion.base.types import Message
from openminion.modules.context.knowledge.constants import LAYER_THIRD_BRAIN
from openminion.modules.context.knowledge.models import (
    GraphContextItem,
    GraphQueryResult,
)
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from openminion.services.gateway.context import (
    _maybe_apply_contextctl_call_site,
    build_turn_context,
)
from openminion.services.gateway.types import TurnContext


def _make_turn_context(history: list[Message] | None = None) -> TurnContext:
    if history is None:
        history = [
            Message(
                channel="console", target="t", body="prior", metadata={"role": "user"}
            )
        ]
    return TurnContext(history=history, prior_transcript_available=True)


def _logger():
    return logging.getLogger("openminion.tests.cgwe07-guard")


# Guard-off branch: deterministic fallback


def test_guard_off_keeps_existing_history_unchanged():
    turn_ctx = _make_turn_context()
    original_history = list(turn_ctx.history)

    selected = _maybe_apply_contextctl_call_site(
        turn_context=turn_ctx,
        agent_id="a1",
        logger=_logger(),
        session_id="s1",
        user_message="hello",
        contextctl_adapter=None,
    )

    assert turn_ctx.history == original_history
    assert selected is False


def test_guard_explicitly_false_keeps_history_unchanged():
    turn_ctx = _make_turn_context()
    original_history = list(turn_ctx.history)
    adapter = MagicMock()
    adapter.is_enabled = False

    selected = _maybe_apply_contextctl_call_site(
        turn_context=turn_ctx,
        agent_id="a1",
        logger=_logger(),
        session_id="s1",
        user_message="hello",
        contextctl_adapter=adapter,
    )

    assert turn_ctx.history == original_history
    assert selected is False
    adapter.build_ctxctl_messages.assert_not_called()


# Guard-on branch: adapter wired


def test_guard_on_delegates_to_injected_adapter():
    turn_ctx = _make_turn_context()
    original_history = list(turn_ctx.history)

    fake_adapter = MagicMock()
    fake_adapter.is_enabled = True
    fake_messages = ["fake-ctxctl-message"]
    fake_adapter.build_ctxctl_messages.return_value = fake_messages
    fake_adapter.select_history.return_value = ["delegated-history"]

    selected = _maybe_apply_contextctl_call_site(
        turn_context=turn_ctx,
        agent_id="a1",
        logger=_logger(),
        session_id="s1",
        user_message="hello",
        contextctl_adapter=fake_adapter,
    )
    fake_adapter.build_ctxctl_messages.assert_called_once_with(
        session_id="s1", agent_id="a1", query="hello"
    )
    fake_adapter.select_history.assert_called_once_with(
        history=original_history,
        session_id="s1",
        agent_id="a1",
        query="hello",
        contextctl_messages=fake_messages,
    )
    assert turn_ctx.history == ["delegated-history"]
    assert selected is True


def test_guard_on_emits_content_free_selection_event():
    fake_adapter = MagicMock()
    fake_adapter.is_enabled = True
    fake_adapter.build_ctxctl_messages.return_value = ["contextctl-message"]
    fake_adapter.select_history.return_value = [
        Message(channel="contextctl", target="target", body="selected")
    ]
    events: list[tuple[str, dict[str, str]]] = []

    class _Memory:
        calls = 0

        def build_context_with_metadata(self, **kwargs):
            del kwargs
            self.calls += 1
            return "", {}

    memory = _Memory()

    def emit_event(
        *,
        session_id,
        event_type,
        conversation_id,
        thread_id,
        attach_id,
        payload,
    ):
        assert (session_id, conversation_id, thread_id, attach_id) == (
            "session",
            "conversation",
            "thread",
            "attach",
        )
        events.append((event_type, payload))

    turn_context = build_turn_context(
        history=[],
        agent_id="agent",
        agent_memory=memory,
        logger=_logger(),
        emit_memory_event=emit_event,
        session_id="session",
        run_id="run",
        request_id="request",
        channel="console",
        target="target",
        user_message="hello",
        conversation_id="conversation",
        thread_id="thread",
        attach_id="attach",
        memory_capsule_strategy="always",
        memory_capsule_cache={},
        memory_dynamic_retrieval_enabled=False,
        contextctl_adapter=fake_adapter,
    )

    assert turn_context.history[0].body == "selected"
    assert memory.calls == 0
    assert (
        "context.contextctl.selected",
        {"run_id": "run", "request_id": "request", "history_count": "1"},
    ) in events


def test_guard_failure_builds_legacy_context_once():
    class _Memory:
        calls = 0

        def build_context_with_metadata(self, **kwargs):
            del kwargs
            self.calls += 1
            return "legacy memory", {}

    memory = _Memory()
    adapter = MagicMock()
    adapter.is_enabled = True
    adapter.build_ctxctl_messages.return_value = None

    turn_context = build_turn_context(
        history=[],
        agent_id="agent",
        agent_memory=memory,
        logger=_logger(),
        emit_memory_event=lambda **kwargs: None,
        session_id="session",
        run_id="run",
        request_id="request",
        channel="console",
        target="target",
        user_message="hello",
        conversation_id="conversation",
        thread_id="thread",
        attach_id="attach",
        memory_capsule_strategy="always",
        memory_capsule_cache={},
        memory_dynamic_retrieval_enabled=False,
        contextctl_adapter=adapter,
    )

    assert memory.calls == 1
    assert any("legacy memory" in message.body for message in turn_context.history)


def test_guard_failure_credits_legacy_sqlite_selection_once(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "fallback-memory.db")
    service = MemoryService(store=store)
    now = datetime.now(timezone.utc).isoformat()
    record_id = store.put(
        MemoryRecord(
            id="fallback-selected",
            scope="agent:agent",
            type="fact",
            title="Fallback marker",
            content={"text": "fallback marker"},
            created_at=now,
            updated_at=now,
        )
    )
    memory = MemoryServiceGatewayAdapter(service, agent_id="agent")
    adapter = MagicMock(is_enabled=True)
    adapter.build_ctxctl_messages.return_value = None

    build_turn_context(
        history=[],
        agent_id="agent",
        agent_memory=memory,
        logger=_logger(),
        emit_memory_event=lambda **kwargs: None,
        session_id="session",
        run_id="run",
        request_id="request",
        channel="console",
        target="target",
        user_message="fallback marker",
        conversation_id="conversation",
        thread_id="thread",
        attach_id="attach",
        memory_capsule_strategy="always",
        memory_capsule_cache={},
        memory_dynamic_retrieval_enabled=False,
        contextctl_adapter=adapter,
    )

    selected = store.get(record_id)
    assert selected is not None
    assert selected.access_count == 1
    service.close()


def test_guard_on_with_none_messages_falls_back_to_existing_history():
    turn_ctx = _make_turn_context()
    original_history = list(turn_ctx.history)

    fake_adapter = MagicMock()
    fake_adapter.is_enabled = True
    fake_adapter.build_ctxctl_messages.return_value = None

    selected = _maybe_apply_contextctl_call_site(
        turn_context=turn_ctx,
        agent_id="a1",
        logger=_logger(),
        session_id="s1",
        user_message="hello",
        contextctl_adapter=fake_adapter,
    )

    assert turn_ctx.history == original_history
    assert selected is False
    fake_adapter.select_history.assert_not_called()


def test_guard_on_with_disabled_adapter_falls_back():
    turn_ctx = _make_turn_context()
    original_history = list(turn_ctx.history)

    fake_adapter = MagicMock()
    fake_adapter.is_enabled = False

    selected = _maybe_apply_contextctl_call_site(
        turn_context=turn_ctx,
        agent_id="a1",
        logger=_logger(),
        session_id="s1",
        user_message="hello",
        contextctl_adapter=fake_adapter,
    )

    assert turn_ctx.history == original_history
    assert selected is False
    fake_adapter.build_ctxctl_messages.assert_not_called()


def test_guard_on_with_adapter_exception_falls_back_deterministically():
    turn_ctx = _make_turn_context()
    original_history = list(turn_ctx.history)

    fake_adapter = MagicMock()
    fake_adapter.is_enabled = True
    fake_adapter.build_ctxctl_messages.side_effect = RuntimeError(
        "synthetic context build failure"
    )
    selected = _maybe_apply_contextctl_call_site(
        turn_context=turn_ctx,
        agent_id="a1",
        logger=_logger(),
        session_id="s1",
        user_message="hello",
        contextctl_adapter=fake_adapter,
    )

    assert turn_ctx.history == original_history
    assert selected is False


def test_memory_failure_still_allows_independent_graph_context():
    class _FailingMemory:
        def build_context_with_metadata(self, **kwargs):
            del kwargs
            raise RuntimeError("synthetic memory failure")

    class _Source:
        name = "fixture-graph"

    class _KnowledgeGraphs:
        def list_sources(self, **kwargs):
            del kwargs
            return (_Source(),)

        def query(self, request, **kwargs):
            del request, kwargs
            return (
                GraphQueryResult(
                    provider="fixture-graph",
                    layer=LAYER_THIRD_BRAIN,
                    items=(
                        GraphContextItem(
                            provider="fixture-graph",
                            source_graph_id="graph",
                            node_or_edge_id="node-1",
                            snippet="graph context survived memory failure",
                        ),
                    ),
                ),
            )

    events: list[tuple[str, dict[str, str]]] = []

    turn_context = build_turn_context(
        history=[],
        agent_id="agent",
        agent_memory=_FailingMemory(),
        logger=_logger(),
        emit_memory_event=lambda **kwargs: events.append(
            (kwargs["event_type"], kwargs["payload"])
        ),
        session_id="session",
        run_id="run",
        request_id="request",
        channel="console",
        target="target",
        user_message="hello",
        conversation_id="conversation",
        thread_id="thread",
        attach_id="attach",
        memory_capsule_strategy="always",
        memory_capsule_cache={},
        memory_dynamic_retrieval_enabled=False,
        knowledge_graphs=_KnowledgeGraphs(),
    )

    assert turn_context.memory_context_meta["memory_context_reason_code"]
    assert (
        "graph context survived memory failure" in turn_context.knowledge_graph_context
    )
    assert any(
        "graph context survived memory failure" in message.body
        for message in turn_context.history
    )
    assert [event_type for event_type, _payload in events] == [
        "memory.context.failed",
        "knowledge_graph.source.resolved",
        "knowledge_graph.query.started",
        "knowledge_graph.query.completed",
    ]
