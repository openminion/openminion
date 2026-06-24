from __future__ import annotations

from dataclasses import dataclass

from openminion.services.agent.execution.finalization import (
    requires_typed_finalization_contract_for_results,
)


@dataclass
class _FakeResult:
    tool_name: str
    ok: bool = True


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


def test_single_ok_file_write_triggers() -> None:
    results = [_FakeResult("file.write", ok=True)]
    assert requires_typed_finalization_contract_for_results(results) is True


def test_single_ok_exec_run_triggers() -> None:
    results = [_FakeResult("exec.run", ok=True)]
    assert requires_typed_finalization_contract_for_results(results) is True


def test_single_ok_git_commit_triggers() -> None:
    results = [_FakeResult("git.commit", ok=True)]
    assert requires_typed_finalization_contract_for_results(results) is True


def test_single_ok_memory_write_triggers() -> None:
    results = [_FakeResult("memory.write", ok=True)]
    assert requires_typed_finalization_contract_for_results(results) is True


def test_single_ok_browser_click_triggers() -> None:
    results = [_FakeResult("browser.click", ok=True)]
    assert requires_typed_finalization_contract_for_results(results) is True


def test_case_insensitive_matching() -> None:
    results = [_FakeResult("FILE.WRITE", ok=True)]
    assert requires_typed_finalization_contract_for_results(results) is True


def test_empty_results_list_returns_false() -> None:
    assert requires_typed_finalization_contract_for_results([]) is False
    assert requires_typed_finalization_contract_for_results(None) is False  # type: ignore[arg-type]


def test_mixed_read_and_write_triggers_on_write() -> None:
    results = [
        _FakeResult("file.read", ok=True),
        _FakeResult("file.write", ok=True),
    ]
    assert requires_typed_finalization_contract_for_results(results) is True


def test_two_reads_still_does_not_trigger() -> None:
    results = [
        _FakeResult("file.read", ok=True),
        _FakeResult("file.list_dir", ok=True),
    ]
    assert requires_typed_finalization_contract_for_results(results) is False
