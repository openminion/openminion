from __future__ import annotations

from http import HTTPStatus
from typing import Iterator

import pytest

from openminion.api.routes.base import APIRouteContext
from openminion.api.routes.memory import handle_request
from openminion.modules.memory.contracts.provenance import (
    MemoryProvenanceEntry,
    TurnProvenanceTrace,
)
from openminion.modules.memory.runtime.provenance import (
    MemoryProvenanceRecorder,
    default_provenance_recorder,
    set_default_provenance_recorder,
)


@pytest.fixture
def fresh_recorder() -> Iterator[MemoryProvenanceRecorder]:

    original = default_provenance_recorder()
    new_recorder = MemoryProvenanceRecorder()
    set_default_provenance_recorder(new_recorder)
    try:
        yield new_recorder
    finally:
        set_default_provenance_recorder(original)


@pytest.fixture
def ctx() -> APIRouteContext:
    return APIRouteContext(
        config_path=None,
        runtime=None,
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="test-request",
    )


def _seed_trace(recorder, session_id: str, turn_id: str) -> TurnProvenanceTrace:
    trace = TurnProvenanceTrace(
        session_id=session_id,
        turn_id=turn_id,
        recorded_at="2026-05-18T00:00:00Z",
        entries=(
            MemoryProvenanceEntry(
                memory_id="m1",
                source="tool_output",
                written_at="2026-05-18T00:00:00Z",
                retrieval_score=0.9,
            ),
            MemoryProvenanceEntry(
                memory_id="m2",
                source="user_said",
                written_at="2026-05-18T00:00:00Z",
                retrieval_score=0.5,
            ),
        ),
        retrieval_cutoff=0.3,
        query="who is the user",
    )
    recorder.record_turn_trace(trace)
    return trace


class TestGetTurnTrace:
    def test_returns_404_when_no_trace(self, ctx, fresh_recorder):
        result = handle_request(
            ctx,
            method_name="GET",
            path="/memory/provenance",
            body=None,
            query="session_id=s1&turn_id=t1",
        )
        assert result is not None
        assert result.status == HTTPStatus.NOT_FOUND

    def test_returns_400_when_session_id_missing(self, ctx, fresh_recorder):
        result = handle_request(
            ctx,
            method_name="GET",
            path="/memory/provenance",
            body=None,
            query="turn_id=t1",
        )
        assert result is not None
        assert result.status == HTTPStatus.BAD_REQUEST

    def test_returns_400_when_turn_id_missing(self, ctx, fresh_recorder):
        result = handle_request(
            ctx,
            method_name="GET",
            path="/memory/provenance",
            body=None,
            query="session_id=s1",
        )
        assert result is not None
        assert result.status == HTTPStatus.BAD_REQUEST

    def test_returns_trace_when_present(self, ctx, fresh_recorder):
        _seed_trace(fresh_recorder, "s1", "t1")
        result = handle_request(
            ctx,
            method_name="GET",
            path="/memory/provenance",
            body=None,
            query="session_id=s1&turn_id=t1",
        )
        assert result is not None
        assert result.status == HTTPStatus.OK
        assert result.payload["session_id"] == "s1"
        assert result.payload["turn_id"] == "t1"
        assert len(result.payload["entries"]) == 2
        assert {e["memory_id"] for e in result.payload["entries"]} == {"m1", "m2"}


class TestGetByMemory:
    def test_returns_empty_list_for_unknown_memory(self, ctx, fresh_recorder):
        result = handle_request(
            ctx,
            method_name="GET",
            path="/memory/provenance/by-memory",
            body=None,
            query="memory_id=never-cited",
        )
        assert result is not None
        assert result.status == HTTPStatus.OK
        assert result.payload["memory_id"] == "never-cited"
        assert result.payload["trace_count"] == 0
        assert result.payload["traces"] == []

    def test_returns_400_when_memory_id_missing(self, ctx, fresh_recorder):
        result = handle_request(
            ctx,
            method_name="GET",
            path="/memory/provenance/by-memory",
            body=None,
            query="",
        )
        assert result is not None
        assert result.status == HTTPStatus.BAD_REQUEST

    def test_returns_matching_traces(self, ctx, fresh_recorder):
        _seed_trace(fresh_recorder, "s1", "t1")
        _seed_trace(fresh_recorder, "s2", "t1")
        result = handle_request(
            ctx,
            method_name="GET",
            path="/memory/provenance/by-memory",
            body=None,
            query="memory_id=m1",
        )
        assert result is not None
        assert result.status == HTTPStatus.OK
        assert result.payload["trace_count"] == 2


class TestRouterFallthrough:
    def test_non_memory_path_returns_none(self, ctx, fresh_recorder):
        assert (
            handle_request(
                ctx,
                method_name="GET",
                path="/sessions/x/messages",
                body=None,
                query="",
            )
            is None
        )

    def test_post_returns_none(self, ctx, fresh_recorder):
        assert (
            handle_request(
                ctx,
                method_name="POST",
                path="/memory/provenance",
                body={},
                query="session_id=s&turn_id=t",
            )
            is None
        )
