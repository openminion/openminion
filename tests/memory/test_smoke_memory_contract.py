from __future__ import annotations

from openminion.modules.memory.contracts.smoke import ensure_memory_smoke_contract
from openminion.services.agent.memory.capsule import normalize_memory_provider
from openminion.modules.memory.runtime.providers import (
    create_memory_provider_registry,
    get_memory_provider,
)
from openminion.modules.memory.smoke import EphemeralMemorySmokeProvider


def test_smoke_contract_accepts_module_owned_smoke_provider() -> None:
    provider = EphemeralMemorySmokeProvider(agent_id="main")
    result = ensure_memory_smoke_contract(provider, strict=False)
    assert result.ok is True
    assert result.errors == []


def test_legacy_hello_world_provider_name_normalizes_to_smoke() -> None:
    assert normalize_memory_provider("memory_v2_smoke") == "memory_v2_smoke"
    assert normalize_memory_provider("memory_v2_hello_world") == "memory_v2_smoke"


def test_memory_provider_registry_uses_module_owned_smoke_provider() -> None:
    registry = create_memory_provider_registry()

    smoke = get_memory_provider(registry, "smoke", {"agent_id": "main"})
    legacy = get_memory_provider(registry, "hello_world", {"agent_id": "main"})

    assert isinstance(smoke, EphemeralMemorySmokeProvider)
    assert isinstance(legacy, EphemeralMemorySmokeProvider)


def test_smoke_contract_rejects_incompatible_shape() -> None:
    class _Broken:
        def build_context(self):  # noqa: ANN001
            return ""

    result = ensure_memory_smoke_contract(_Broken(), strict=False)
    assert result.ok is False
    assert "missing member: build_retrieval_context" in result.errors


def test_smoke_provider_does_not_extract_user_facts() -> None:
    provider = EphemeralMemorySmokeProvider(agent_id="main")
    result = provider.record_turn(
        session_id="s",
        run_id="r",
        request_id="q",
        channel="console",
        target="local",
        user_message="remember: project codename is Orion",
        assistant_message="ok",
    )

    assert result.facts_added == 0
    text = provider.build_context(session_id="s", user_message="what do you know?")
    assert "project codename is Orion" not in text
    assert "ephemeral-memory-smoke provider is active" in text

