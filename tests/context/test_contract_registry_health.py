from __future__ import annotations

from openminion.modules.context.contracts import (
    CONTRACT_REGISTRY,
    POST_RESET_BASELINE_VERSION,
    check_contract_registry_health,
)


def test_post_reset_baseline_is_v1() -> None:
    assert POST_RESET_BASELINE_VERSION == "v1"


def test_contract_registry_lists_all_four_domains() -> None:
    expected_domains = {"context", "session", "memory", "brain"}
    assert set(CONTRACT_REGISTRY) == expected_domains


def test_health_check_aligned_on_live_registry() -> None:
    result = check_contract_registry_health()
    assert result["aligned"] is True, (
        f"contract registry must be aligned at baseline v1: {result}"
    )
    assert result["expected_version"] == "v1"
    assert result["mismatches"] == {}
    # Registry copy is safe to mutate in test space without affecting global.
    assert result["registry"] is not CONTRACT_REGISTRY


def test_health_check_negative_path_reports_specific_mismatch() -> None:
    stale_registry = {
        "context": "v1",
        "session": "v0",  # <- mismatch
        "memory": "v1",
        "brain": "v1",
    }
    result = check_contract_registry_health(stale_registry)
    assert result["aligned"] is False
    assert result["mismatches"] == {"session": "v0"}
    # Other domains do NOT appear in mismatches.
    assert set(result["mismatches"]) == {"session"}


def test_health_check_negative_multiple_mismatches() -> None:
    stale = {
        "context": "v1",
        "session": "v0",
        "memory": "v2",
        "brain": "v1",
    }
    result = check_contract_registry_health(stale)
    assert result["aligned"] is False
    assert result["mismatches"] == {"session": "v0", "memory": "v2"}


def test_health_check_with_custom_expected_version() -> None:
    result = check_contract_registry_health(expected_version="v2")
    assert result["aligned"] is False
    # Every entry mismatches when expected_version is bumped.
    assert set(result["mismatches"]) == set(CONTRACT_REGISTRY)
