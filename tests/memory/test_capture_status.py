from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

from openminion.cli.interactive.runtime.controls import RuntimeControlsMixin
from openminion.modules.memory.runtime.capture_status import (
    project_capture_processing,
    summarize_capture_processing,
    summarize_recall_processing,
)
from openminion.modules.memory.gateway_turn import capture_evidence_id
from openminion.modules.session.storage.store import SQLiteSessionStore
from openminion.modules.telemetry.events.catalog import (
    EVENT_TYPES,
    MEMORY_CAPTURE_STATUS,
    MEMORY_RECALL_STATUS,
)
from openminion.services.agent.memory.gateway_adapter import (
    DisabledMemoryGatewayAdapter,
)
from openminion.services.gateway.memory import record_memory_turn


def test_capture_projection_keeps_pending_until_terminal_event() -> None:
    events = [
        {
            "event_id": "event-1",
            "event_type": "memory.write.started",
            "created_at": "2026-08-24T00:00:00Z",
            "payload": {
                "capture_evidence_id": "capture:v1:a",
                "patch_id": "patch-a",
                "capture_state": "pending",
            },
        },
        {
            "event_id": "event-2",
            "event_type": "memory.write.started",
            "created_at": "2026-08-24T00:00:01Z",
            "payload": {
                "capture_evidence_id": "capture:v1:b",
                "patch_id": "patch-b",
                "capture_state": "pending",
            },
        },
        {
            "event_id": "event-3",
            "event_type": "memory.write.completed",
            "created_at": "2026-08-24T00:00:02Z",
            "payload": {
                "capture_evidence_id": "capture:v1:b",
                "patch_id": "patch-b",
                "changed": "false",
                "capture_reason": "no_output",
            },
        },
    ]

    projected = project_capture_processing(events)

    assert projected["capture:v1:a"].disposition == "pending"
    assert projected["capture:v1:b"].disposition == "succeeded_no_output"
    assert projected["capture:v1:b"].reason == "no_output"

    summary = summarize_capture_processing(projected)
    assert summary.pending == 1
    assert summary.succeeded_no_output == 1
    assert summary.oldest_pending_at == "2026-08-24T00:00:00Z"


def test_capture_projection_reports_new_terminal_intent_counts() -> None:
    events = [
        {
            "event_id": "event-pending",
            "event_type": "turn.outcome",
            "created_at": "2026-08-30T00:00:00Z",
            "payload": {
                "capture_id": "capture:pending",
                "capture_state": "pending",
            },
        },
        {
            "event_id": "event-terminal",
            "event_type": "turn.outcome",
            "created_at": "2026-08-30T00:00:01Z",
            "payload": {
                "capture_id": "capture:terminal",
                "capture_state": "pending",
            },
        },
        {
            "event_id": "result-terminal",
            "event_type": "memory.capture.result",
            "created_at": "2026-08-30T00:00:02Z",
            "payload": {
                "capture_id": "capture:terminal",
                "disposition": "succeeded_no_output",
                "result_hash": "result-1",
            },
        },
        {
            "event_id": "event-disabled",
            "event_type": "turn.outcome",
            "created_at": "2026-08-30T00:00:03Z",
            "payload": {
                "capture_id": "capture:disabled",
                "capture_state": "excluded",
                "capture_reason": "memory_disabled",
            },
        },
    ]

    summary = summarize_capture_processing(project_capture_processing(events))

    assert summary.eligible == 2
    assert summary.pending == 1
    assert summary.terminal == 2
    assert summary.rejected == 1
    assert summary.oldest_pending_at == "2026-08-30T00:00:00Z"


def test_capture_projection_marks_conflicting_terminal_results() -> None:
    events = [
        {
            "event_id": "result-1",
            "event_type": "memory.capture.result",
            "payload": {
                "capture_id": "capture:conflict",
                "disposition": "succeeded",
                "result_hash": "hash-1",
            },
        },
        {
            "event_id": "result-2",
            "event_type": "memory.capture.result",
            "payload": {
                "capture_id": "capture:conflict",
                "disposition": "rejected",
                "result_hash": "hash-2",
            },
        },
    ]

    summary = summarize_capture_processing(project_capture_processing(events))

    assert summary.integrity_errors == 1
    assert summary.terminal == 1


def test_recall_status_reports_only_normalized_content_free_facts() -> None:
    events = [
        {
            "event_type": "memory.retrieval.built",
            "payload": {
                "memory_recall_mode": "sophiagraph",
                "memory_recall_capabilities": "keyword,graph,vector",
                "memory_envelope_included_items": "3",
                "memory_recall_threshold_drops": "2",
                "evidence_duplicate_omissions": "1",
                "query": "private query",
                "provider": "private provider",
                "model": "private model",
                "url": "https://private.invalid",
            },
        },
        {
            "event_type": "knowledge_graph.query.completed",
            "payload": {
                "knowledge_graph_results": "2",
                "knowledge_graph_omitted": "1",
                "path": "/private/path",
                "exception": "private exception",
                "secret": "private secret",
            },
        },
    ]

    summary = summarize_recall_processing(
        events,
        enabled=True,
        mode="legacy",
        capabilities=(),
    )

    assert summary.health == "healthy"
    assert summary.mode == "sophiagraph"
    assert summary.capabilities == ("keyword", "graph", "vector")
    assert summary.score_domain == "hybrid-semantic-v1"
    assert summary.selected_memory == 3
    assert summary.selected_knowledge == 2
    assert summary.omission_reasons == (
        ("duplicate", 1),
        ("relevance", 3),
    )
    rendered = str(summary)
    for sensitive in (
        "private query",
        "private provider",
        "private model",
        "https://private.invalid",
        "/private/path",
        "private exception",
        "private secret",
    ):
        assert sensitive not in rendered


def test_recall_status_distinguishes_disabled_unsupported_and_degraded() -> None:
    disabled = summarize_recall_processing(
        [], enabled=False, mode="legacy", supported=True
    )
    unsupported = summarize_recall_processing(
        [], enabled=True, mode="sophiagraph", supported=False
    )
    degraded = summarize_recall_processing(
        [{"event_type": "memory.context.failed", "payload": {}}],
        enabled=True,
        mode="shadow",
        supported=True,
    )
    recovered = summarize_recall_processing(
        [
            {"event_type": "memory.context.failed", "payload": {}},
            {"event_type": "memory.retrieval.built", "payload": {}},
        ],
        enabled=True,
        mode="shadow",
        supported=True,
    )

    assert disabled.health == "disabled"
    assert unsupported.health == "unsupported"
    assert degraded.health == "degraded"
    assert recovered.health == "healthy"


def test_capture_and_recall_status_events_are_registered() -> None:
    assert MEMORY_CAPTURE_STATUS in EVENT_TYPES
    assert MEMORY_RECALL_STATUS in EVENT_TYPES


def test_runtime_memory_report_uses_content_free_capture_and_recall_status() -> None:
    events = [
        {
            "event_type": "turn.outcome",
            "created_at": "2026-08-30T00:00:00Z",
            "payload": {
                "capture_id": "capture:pending",
                "capture_state": "pending",
                "transcript": "private transcript",
            },
        },
        {
            "event_type": "memory.retrieval.built",
            "payload": {
                "memory_recall_mode": "shadow",
                "memory_recall_capabilities": "keyword,graph,vector",
                "memory_envelope_included_items": "2",
                "evidence_budget_omissions": "1",
                "query": "private query",
            },
        },
    ]

    class _Sessions:
        def list_events(self, **kwargs):
            prefix = kwargs["event_type_prefix"]
            return [event for event in events if event["event_type"].startswith(prefix)]

    class _Controls(RuntimeControlsMixin):
        is_bound = True
        session_id = "session-1"
        _rt = SimpleNamespace(sessions=_Sessions())

        def list_memory_records(self):
            return []

        def list_memory_candidates(self):
            return []

        def _memory_query_provider(self):
            return SimpleNamespace(
                enabled=True,
                _precision_options=SimpleNamespace(mode="shadow"),
                _recall_adapter=SimpleNamespace(
                    capabilities=SimpleNamespace(
                        keyword=True,
                        graph=True,
                        recency=False,
                        trust=False,
                        vector=True,
                        rerank=False,
                    )
                ),
            )

    body = _Controls().memory_report()

    assert "capture     1 eligible · 1 pending · 0 terminal" in body
    assert "recall      healthy · mode shadow" in body
    assert "selected    memory 2 · knowledge 0" in body
    assert "omissions   budget 1" in body
    assert "private transcript" not in body
    assert "private query" not in body


def test_disabled_memory_emits_terminal_content_free_rejection() -> None:
    events: list[dict[str, object]] = []

    def _emit(**kwargs: object) -> None:
        events.append(dict(kwargs))

    record_memory_turn(
        agent_memory=DisabledMemoryGatewayAdapter(agent_id="alpha"),
        logger=logging.getLogger("openminion.tests.capture"),
        agent_id="alpha",
        memory_capsule_strategy="frozen_session",
        memory_capsule_cache={},
        session_id="session-1",
        run_id="run-1",
        request_id="request-1",
        channel="console",
        target="local",
        user_message="private transcript body",
        assistant_message="private response body",
        conversation_id="conversation-1",
        thread_id="thread-1",
        attach_id="",
        emit_memory_event=_emit,
        outbound_metadata={},
    )

    assert [event["event_type"] for event in events] == ["memory.write.rejected"]
    payload = events[0]["payload"]
    assert isinstance(payload, dict)
    assert payload["capture_state"] == "rejected"
    assert payload["capture_reason"] == "memory_disabled"
    assert "private transcript body" not in str(payload)
    assert "private response body" not in str(payload)


def test_backend_none_emits_distinct_terminal_rejection() -> None:
    events: list[dict[str, object]] = []
    adapter = DisabledMemoryGatewayAdapter(agent_id="alpha")
    adapter.disabled_reason = "backend_none"

    record_memory_turn(
        agent_memory=adapter,
        logger=logging.getLogger("openminion.tests.capture"),
        agent_id="alpha",
        memory_capsule_strategy="frozen_session",
        memory_capsule_cache={},
        session_id="session-1",
        run_id="run-1",
        request_id="request-1",
        channel="console",
        target="local",
        user_message="body",
        assistant_message="reply",
        conversation_id="conversation-1",
        thread_id="thread-1",
        attach_id="",
        emit_memory_event=lambda **event: events.append(dict(event)),
        outbound_metadata={},
    )

    payload = events[0]["payload"]
    assert isinstance(payload, dict)
    assert payload["capture_reason"] == "backend_none"


def test_capture_projection_survives_session_store_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "sessions.db"
    sessions = SQLiteSessionStore(db_path)
    session_id = sessions.create_session(
        initial_agent_id="alpha",
        profile_version="v1",
    )
    sessions.append_event(
        session_id,
        event_type="memory.write.started",
        payload={
            "capture_evidence_id": "capture:v1:restart",
            "capture_state": "pending",
            "patch_id": "patch-restart",
        },
    )

    reopened = SQLiteSessionStore(db_path)
    projected = project_capture_processing(
        reopened.get_events(
            session_id,
            types=["memory.write.started", "memory.write.completed"],
        )
    )

    assert projected["capture:v1:restart"].disposition == "pending"
    assert projected["capture:v1:restart"].patch_id == "patch-restart"


def test_capture_identity_is_stable_for_replayed_turn() -> None:
    first = capture_evidence_id(
        session_id="session-1",
        request_id="request-1",
    )
    replay = capture_evidence_id(
        session_id="session-1",
        request_id="request-1",
    )

    assert first == replay
    assert first.startswith("capture:v1:")
    assert first != capture_evidence_id(
        session_id="session-1",
        request_id="request-2",
    )
