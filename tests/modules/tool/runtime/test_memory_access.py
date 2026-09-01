from __future__ import annotations

from types import SimpleNamespace

import pytest

from openminion.modules.brain.adapters.memory.runtime import MemctlAdapter
from openminion.modules.memory.storage.base import SearchQueryOptions
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.context import (
    RuntimeContext,
    resolve_memory_service,
)
from openminion.modules.tool.runtime.memory import MemoryAccessContext
from openminion.modules.tool.runtime.policy import Policy


class _Store:
    def __init__(self) -> None:
        self.records: dict[str, object] = {}

    def get(self, record_id: str):
        return self.records.get(record_id)


class _Service:
    def __init__(self) -> None:
        self._store = _Store()
        self.writes: list[dict[str, object]] = []
        self.searches: list[tuple[object, str | None]] = []
        self.deletes: list[tuple[str, str | None]] = []

    def write_record(self, **kwargs) -> str:
        self.writes.append(kwargs)
        return "record-1"

    def search(self, options, *, agent_id=None):
        self.searches.append((options, agent_id))
        return []

    def delete_record(self, record_id: str, *, reason=None) -> bool:
        self.deletes.append((record_id, reason))
        return self._store.records.pop(record_id, None) is not None


def _runtime_context(tmp_path, memctl: MemctlAdapter) -> RuntimeContext:
    return RuntimeContext(
        policy=Policy(raw={}),
        workspace=tmp_path,
        run_root=tmp_path,
        scope="WRITE_SAFE",
        confirm=False,
        memory_service=memctl,
        agent_id="alpha",
        session_id="session-1",
        capture_id="capture-1",
        tool_call_id="call-1",
    )


def test_memory_access_context_allows_only_exact_agent_and_session_scopes() -> None:
    access = MemoryAccessContext(agent_id="alpha", session_id="session-1")

    assert access.require_scope("agent:alpha") == "agent:alpha"
    assert access.require_scope("session:session-1") == "session:session-1"

    for denied in ("agent:beta", "session:other", "project:repo", "global:default"):
        with pytest.raises(ToolRuntimeError) as excinfo:
            access.require_scope(denied)
        assert excinfo.value.code == "POLICY_DENIED"


def test_bound_memctl_translates_capture_identity_deterministically(tmp_path) -> None:
    service = _Service()
    memctl = MemctlAdapter(service, agent_id="alpha")
    context = _runtime_context(tmp_path, memctl)
    bound = resolve_memory_service(context)

    assert bound is not None
    first_id = bound.write_record(
        scope="session:session-1",
        record_type="fact",
        title="Preference",
        content="sqlite",
    )
    second_id = bound.write_record(
        scope="session:session-1",
        record_type="fact",
        title="Preference",
        content="sqlite",
    )

    assert first_id == second_id == "record-1"
    assert service.writes[0]["idempotency_key"] == service.writes[1]["idempotency_key"]
    assert service.writes[0]["capture_id"] == "capture-1"
    assert service.writes[0]["tool_call_id"] == "call-1"
    assert service.writes[0]["agent_id"] == "alpha"
    assert service.writes[0]["session_id"] == "session-1"


def test_bound_memctl_enforces_search_and_delete_scope(tmp_path) -> None:
    service = _Service()
    service._store.records["session-record"] = SimpleNamespace(
        scope="session:session-1"
    )
    service._store.records["project-record"] = SimpleNamespace(scope="project:repo")
    bound = resolve_memory_service(
        _runtime_context(tmp_path, MemctlAdapter(service, agent_id="alpha"))
    )
    assert bound is not None

    assert (
        bound.search(
            SearchQueryOptions(
                query="sqlite",
                scopes=["agent:alpha", "session:session-1"],
                limit=5,
            )
        )
        == []
    )
    assert service.searches[0][1] == "alpha"
    assert bound.delete_record("session-record", reason="obsolete") is True

    with pytest.raises(ToolRuntimeError) as excinfo:
        bound.search(SearchQueryOptions(query="x", scopes=["global:default"], limit=1))
    assert excinfo.value.code == "POLICY_DENIED"

    assert bound.delete_record("project-record") is False
    assert bound.delete_record("missing-record") is False
    assert service.deletes == [
        ("session-record", "obsolete"),
        ("missing-record", None),
    ]


def test_capture_bound_write_requires_canonical_tool_call_id(tmp_path) -> None:
    service = _Service()
    context = _runtime_context(tmp_path, MemctlAdapter(service, agent_id="alpha"))
    context.tool_call_id = ""
    bound = resolve_memory_service(context)
    assert bound is not None

    with pytest.raises(ToolRuntimeError) as excinfo:
        bound.write_record(
            scope="agent:alpha",
            record_type="fact",
            title="Preference",
            content="sqlite",
        )

    assert excinfo.value.code == "INVALID_ARGUMENT"
    assert excinfo.value.details["reason_code"] == "memory_tool_call_id_missing"
    assert service.writes == []
