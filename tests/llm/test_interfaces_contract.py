from __future__ import annotations

import pytest

from openminion.modules.llm.runtime.client import LLMCTL
from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.interfaces import (
    LLM_RESPONSE_INTERFACE_VERSION,
    ensure_llm_response_compatibility,
)
from openminion.modules.llm.providers.plugins import ProviderRegistry
from openminion.modules.llm.providers import stub_provider


class _BadContractProvider:
    name = "bad_contract"
    contract_version = "v0"

    def complete(self, request, config):
        raise NotImplementedError

    def stream(self, request, config):
        raise NotImplementedError

    def list_models(self, config):
        return []

    def healthcheck(self, config):
        return {"ok": True}


def test_llmctl_contract_version_matches_interface_version() -> None:
    assert LLMCTL.contract_version == LLM_RESPONSE_INTERFACE_VERSION


def test_ensure_llm_response_compatibility_strict_mode_raises() -> None:
    with pytest.raises(LLMCtlError) as exc:
        ensure_llm_response_compatibility(
            _BadContractProvider(), component_name="bad-provider", strict=True
        )
    assert "contract mismatch" in str(exc.value).lower()


def test_ensure_llm_response_compatibility_non_strict_mode_warns() -> None:
    with pytest.warns(RuntimeWarning):
        ok = ensure_llm_response_compatibility(
            _BadContractProvider(), component_name="bad-provider", strict=False
        )
    assert ok is False


def test_provider_registry_accepts_builtin_provider_contract() -> None:
    registry = ProviderRegistry()
    registry.add(stub_provider())
    assert "stub" in registry.list()


def test_provider_registry_rejects_contract_mismatch_in_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_STRICT_LLM_RESPONSE_CONTRACTS", "1")
    registry = ProviderRegistry()
    with pytest.raises(LLMCtlError):
        registry.add(_BadContractProvider())


def test_provider_registry_warns_but_allows_mismatch_in_non_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_STRICT_LLM_RESPONSE_CONTRACTS", "0")
    registry = ProviderRegistry()
    with pytest.warns(RuntimeWarning):
        registry.add(_BadContractProvider())
    assert "bad_contract" in registry.list()
