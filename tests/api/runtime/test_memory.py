from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.api.core import bootstrap as bootstrap_module
from openminion.api.core.infrastructure import RuntimePaths
from openminion.modules.memory.errors import MemoryQueryUnavailableError, StoreReadError
from openminion.modules.memory.interfaces import MemoryNamespaceQueryInterface
from openminion.modules.memory.storage.base import ListQueryOptions, SearchQueryOptions
from openminion.services.agent.memory.gateway_adapter import (
    DisabledMemoryGatewayAdapter,
    MemoryServiceGatewayAdapter,
)
from openminion.modules.memory.smoke import EphemeralMemorySmokeProvider


class _QueryService:
    def __init__(self, error: Exception | None = None) -> None:
        self.list_options = None
        self.search_options = None
        self.error = error

    def list(self, options):
        self.list_options = options
        if self.error is not None:
            raise self.error
        return ["listed"]

    def search(self, options):
        self.search_options = options
        if self.error is not None:
            raise self.error
        return ["searched"]


class _RuntimeCapture:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)
        self._agent_runtime_modes = {}
        self._agent_runtime_fallback_reasons = {}

    def _bind_runtime_handle(self, agent, runtime) -> None:
        assert runtime is self

    def _runtime_cache_key(self, *, agent_name, overrides) -> str:
        return f"{agent_name}:{id(overrides)}"


def test_gateway_adapter_implements_query_protocol() -> None:
    service = _QueryService()
    adapter = MemoryServiceGatewayAdapter.__new__(MemoryServiceGatewayAdapter)
    adapter._service = service
    list_options = ListQueryOptions(scopes=["agent:a"])
    search_options = SearchQueryOptions(query="needle", scopes=["agent:a"])

    assert isinstance(adapter, MemoryNamespaceQueryInterface)
    assert adapter.list_records(list_options) == ["listed"]
    assert adapter.search_records(search_options) == ["searched"]
    assert service.list_options is list_options
    assert service.search_options is search_options


def test_gateway_adapter_normalizes_provider_query_failures() -> None:
    service = _QueryService(RuntimeError("query failed"))
    adapter = MemoryServiceGatewayAdapter.__new__(MemoryServiceGatewayAdapter)
    adapter._service = service

    with pytest.raises(StoreReadError, match="memory list query failed"):
        adapter.list_records(ListQueryOptions(scopes=["agent:a"]))
    with pytest.raises(StoreReadError, match="memory search query failed"):
        adapter.search_records(SearchQueryOptions(query="x", scopes=["agent:a"]))


@pytest.mark.parametrize(
    "adapter",
    [
        DisabledMemoryGatewayAdapter(agent_id="a"),
        EphemeralMemorySmokeProvider(agent_id="a"),
    ],
)
def test_non_durable_adapters_report_query_unavailability(adapter) -> None:
    assert isinstance(adapter, MemoryNamespaceQueryInterface)
    with pytest.raises(MemoryQueryUnavailableError):
        adapter.list_records(ListQueryOptions(scopes=["agent:a"]))
    with pytest.raises(MemoryQueryUnavailableError):
        adapter.search_records(SearchQueryOptions(query="x", scopes=["agent:a"]))


def test_runtime_exposes_same_memory_adapter(monkeypatch) -> None:
    memory_adapter = object()
    default_agent = SimpleNamespace(name="alpha")
    runtime_storage = SimpleNamespace(
        connection=object(),
        sessions=object(),
        idempotency=object(),
    )
    infrastructure = {
        "runtime_storage": runtime_storage,
        "telemetry_service": object(),
        "channels": object(),
        "plugins": object(),
        "logger": object(),
        "provider": object(),
        "llm_runtime": object(),
        "tools": object(),
        "agent_security_policy": object(),
        "self_improvement": object(),
        "agent_memory": memory_adapter,
        "action_policy": object(),
        "retrieve_ctl": object(),
        "knowledge_graphs": object(),
        "sandbox_runner": object(),
        "authored_tools": object(),
        "default_agent": default_agent,
    }
    monkeypatch.setattr(
        bootstrap_module, "build_runtime_manager", lambda runtime: object()
    )

    runtime = bootstrap_module.finalize_runtime_instance(
        cls=_RuntimeCapture,
        base_config=SimpleNamespace(runtime=SimpleNamespace(tool_workspace_root=None)),
        manager=object(),
        paths=RuntimePaths(
            home=Path("/tmp/home"),
            data=Path("/tmp/data"),
            config=Path("/tmp/config.json"),
            storage=Path("/tmp/storage.db"),
            memory=Path("/tmp/memory"),
        ),
        infrastructure=infrastructure,
        agent=object(),
        gateway=SimpleNamespace(_agent_memory=memory_adapter),
        runtime_mode="brain",
        fallback_reason="",
        effective_run_profile_overrides=object(),
    )

    assert runtime.memory_queries is memory_adapter
    assert runtime.gateway._agent_memory is memory_adapter
