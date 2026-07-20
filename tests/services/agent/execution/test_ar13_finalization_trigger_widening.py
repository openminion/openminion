from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openminion.services.agent.execution.finalization import (
    requires_typed_finalization_contract_for_results,
)


@dataclass
class _FakeResult:
    tool_name: str
    ok: bool = True
    data: dict[str, Any] | None = None


def test_three_plus_results_still_triggers() -> None:
    results = [
        _FakeResult("file.read"),
        _FakeResult("file.read"),
        _FakeResult("file.read"),
    ]
    assert requires_typed_finalization_contract_for_results(results) is True


def test_any_non_ok_still_triggers() -> None:
    results = [_FakeResult("file.read", ok=False)]
    assert requires_typed_finalization_contract_for_results(results) is True


def test_single_ok_read_only_result_does_not_trigger() -> None:
    results = [_FakeResult("file.read", ok=True)]
    assert requires_typed_finalization_contract_for_results(results) is False

    results = [_FakeResult("time.now", ok=True)]
    assert requires_typed_finalization_contract_for_results(results) is False


def test_typed_side_effect_radii_trigger_finalization() -> None:
    for radius in ("local_mutation", "remote_mutation", "code_execution"):
        results = [
            _FakeResult(
                "custom.tool",
                data={"tool_blast_radius": radius},
            )
        ]
        assert requires_typed_finalization_contract_for_results(results) is True


def test_name_prefix_without_typed_metadata_does_not_trigger() -> None:
    for tool_name in ("file.write", "exec.run", "git.commit", "memory.write"):
        assert (
            requires_typed_finalization_contract_for_results(
                [_FakeResult(tool_name)]
            )
            is False
        )


def test_structured_tool_min_scope_is_not_a_parallel_side_effect_owner() -> None:
    results = [
        _FakeResult("custom.tool", ok=True, data={"tool_min_scope": "WRITE_SAFE"})
    ]
    assert requires_typed_finalization_contract_for_results(results) is False


def test_typed_read_only_overrides_mutation_looking_name() -> None:
    results = [
        _FakeResult(
            "file.write",
            ok=True,
            data={"tool_blast_radius": "read_only"},
        )
    ]
    assert requires_typed_finalization_contract_for_results(results) is False


def test_malformed_typed_radius_fails_safe() -> None:
    results = [
        _FakeResult("custom.tool", data={"tool_blast_radius": "unexpected"})
    ]
    assert requires_typed_finalization_contract_for_results(results) is True


def test_empty_results_list_returns_false() -> None:
    assert requires_typed_finalization_contract_for_results([]) is False
    assert requires_typed_finalization_contract_for_results(None) is False  # type: ignore[arg-type]


def test_mixed_read_and_write_triggers_on_write() -> None:
    results = [
        _FakeResult("file.read", data={"tool_blast_radius": "read_only"}),
        _FakeResult("file.write", data={"tool_blast_radius": "local_mutation"}),
    ]
    assert requires_typed_finalization_contract_for_results(results) is True


def test_two_reads_still_does_not_trigger() -> None:
    results = [
        _FakeResult("file.read", data={"tool_blast_radius": "read_only"}),
        _FakeResult("file.list_dir", data={"tool_blast_radius": "read_only"}),
    ]
    assert requires_typed_finalization_contract_for_results(results) is False
