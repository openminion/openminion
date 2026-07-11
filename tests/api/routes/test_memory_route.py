from __future__ import annotations

from http import HTTPStatus
from types import SimpleNamespace
from typing import Iterator

import pytest

from openminion.api.routes.contracts import APIRouteContext, RouteResult
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
from openminion.modules.memory.errors import MemoryQueryUnavailableError, StoreReadError
from openminion.modules.memory.models import MemoryNamespace, MemoryRecord


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


class _MemoryQueries:
    def __init__(self, records=None, error=None) -> None:
        self.records = list(records or [])
        self.error = error
        self.list_options = None
        self.search_options = None

    def list_records(self, options):
        self.list_options = options
        if self.error is not None:
            raise self.error
        return list(self.records)

    def search_records(self, options):
        self.search_options = options
        if self.error is not None:
            raise self.error
        return list(self.records)


def _query_ctx(memory_queries) -> APIRouteContext:
    return APIRouteContext(
        config_path=None,
        runtime=SimpleNamespace(memory_queries=memory_queries),
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="query-request",
    )


def _query_request(
    context: APIRouteContext,
    body: dict,
    *,
    search: bool = False,
) -> RouteResult | None:
    return handle_request(
        context,
        method_name="POST",
        path="/memory/records/search" if search else "/memory/records/list",
        body=body,
        query=None,
    )


def _record() -> MemoryRecord:
    return MemoryRecord(
        id="typed-a",
        scope="agent:agent-a",
        type="fact",
        content="deployment convention",
        namespace=MemoryNamespace(user_id="user-a", agent_id="agent-a"),
        created_at="2026-07-10T00:00:00Z",
        updated_at="2026-07-10T00:00:00Z",
    )


class TestMemoryRecordQueries:
    def test_list_uses_one_typed_namespace_and_stable_envelope(self) -> None:
        queries = _MemoryQueries([_record()])
        result = _query_request(
            _query_ctx(queries),
            {
                "namespace": {"user_id": "user-a", "agent_id": "agent-a"},
                "scope": "agent:agent-a",
                "types": ["fact"],
                "limit": 10,
                "offset": 0,
            },
        )

        assert result is not None
        assert result.status == HTTPStatus.OK
        assert result.payload["count"] == 1
        assert result.payload["records"][0]["id"] == "typed-a"
        assert result.payload["namespace"] == {
            "user_id": "user-a",
            "agent_id": "agent-a",
        }
        assert result.payload["legacy_scope_only"] is False
        assert len(queries.list_options.namespaces) == 1

    def test_search_passes_query_and_namespace(self) -> None:
        queries = _MemoryQueries([_record()])
        result = _query_request(
            _query_ctx(queries),
            {
                "query": "deployment",
                "namespace": {"project_id": "project-a"},
                "limit": 5,
            },
            search=True,
        )

        assert result is not None
        assert result.status == HTTPStatus.OK
        assert queries.search_options.query == "deployment"
        assert queries.search_options.namespaces[0].project_id == "project-a"

    def test_scope_only_request_uses_legacy_bridge(self) -> None:
        queries = _MemoryQueries([])
        result = _query_request(
            _query_ctx(queries),
            {"scope": "session:s1"},
        )

        assert result is not None
        assert result.status == HTTPStatus.OK
        assert result.payload["legacy_scope_only"] is True
        assert result.payload["namespace"] == {"session_id": "s1"}

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"namespace": {}},
            {"namespace": {"display_name": "Alice"}},
            {"scope": "invalid"},
            {"scope": "agent:a", "namespace": {"agent_id": "b"}},
            {"namespace": {"user_id": "a"}, "limit": 0},
            {"namespace": {"user_id": "a"}, "offset": -1},
            {"namespace": {"user_id": "a"}, "include_invalidated": "yes"},
            {"namespace": {"user_id": "a"}, "unexpected": True},
        ],
    )
    def test_invalid_list_requests_return_normalized_400(self, body) -> None:
        result = _query_request(
            _query_ctx(_MemoryQueries()),
            body,
        )

        assert result is not None
        assert result.status == HTTPStatus.BAD_REQUEST
        assert result.payload["error"]["code"] == "invalid_request"

    def test_missing_search_query_returns_400(self) -> None:
        result = _query_request(
            _query_ctx(_MemoryQueries()),
            {"namespace": {"user_id": "a"}},
            search=True,
        )

        assert result is not None
        assert result.status == HTTPStatus.BAD_REQUEST

    @pytest.mark.parametrize(
        "context",
        [
            APIRouteContext(None, None, None, None, "request"),
            _query_ctx(None),
        ],
    )
    def test_missing_runtime_or_provider_returns_503(self, context) -> None:
        result = _query_request(
            context,
            {"namespace": {"user_id": "a"}},
        )

        assert result is not None
        assert result.status == HTTPStatus.SERVICE_UNAVAILABLE
        assert result.payload["error"]["code"] == "memory_unavailable"

    def test_typed_provider_unavailability_returns_503(self) -> None:
        queries = _MemoryQueries(error=MemoryQueryUnavailableError("disabled"))
        result = _query_request(
            _query_ctx(queries),
            {"namespace": {"user_id": "a"}},
        )

        assert result is not None
        assert result.status == HTTPStatus.SERVICE_UNAVAILABLE
        assert result.payload["error"]["code"] == "memory_unavailable"

    def test_typed_provider_failure_returns_normalized_500(self) -> None:
        queries = _MemoryQueries(error=StoreReadError("query failed"))
        result = _query_request(
            _query_ctx(queries),
            {"namespace": {"user_id": "a"}},
        )

        assert result is not None
        assert result.status == HTTPStatus.INTERNAL_SERVER_ERROR
        assert result.payload["error"]["code"] == "memory_query_failed"
