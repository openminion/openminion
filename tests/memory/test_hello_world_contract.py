from __future__ import annotations

from openminion.modules.memory.contracts.hello_world import (
    ensure_memory_hello_world_contract,
)
from openminion.services.agent.memory.hello_world import (
    HelloWorldMemoryService,
)


def test_hello_world_contract_accepts_minimal_provider() -> None:
    provider = HelloWorldMemoryService(agent_id="main")
    result = ensure_memory_hello_world_contract(provider, strict=False)
    assert result.ok is True
    assert result.errors == []


def test_hello_world_contract_rejects_incompatible_shape() -> None:
    class _Broken:
        def build_context(self):  # noqa: ANN001
            return ""

    result = ensure_memory_hello_world_contract(_Broken(), strict=False)
    assert result.ok is False
    assert "missing member: build_retrieval_context" in result.errors
