from __future__ import annotations

from dataclasses import dataclass

from openminion.modules.brain.runtime.verification.probe import (
    VERIFICATION_FAILED_REASON,
    apply_verification_to_judgment,
    evaluate_verification,
    is_verification_failed,
)
from openminion.modules.brain.schemas.closure import (
    ClosureJudgment,
    VerificationFact,
)


def _exec_run_result(*, argv: list[str], exit_code: int = 0, ok: bool = True) -> dict:
    return {
        "tool_name": "exec.run",
        "ok": ok,
        "data": {
            "argv": list(argv),
            "exit_code": exit_code,
            "tool_blast_radius": "code_execution",
        },
    }


def _file_write_result(*, path: str, ok: bool = True) -> dict:
    return {
        "tool_name": "file.write",
        "ok": ok,
        "data": {"path": path, "tool_blast_radius": "local_mutation"},
    }


def _git_status_result(*, ok: bool = True) -> dict:
    return {
        "tool_name": "git.status",
        "ok": ok,
        "data": {"argv": ["git", "status"], "tool_blast_radius": "read_only"},
    }


@dataclass
class _StubBudgets:
    tool_calls: int = 10
    tokens: int = 1000
    time_ms: int = 60_000


@dataclass
class _StubState:
    budgets_remaining: _StubBudgets


def _state(**overrides) -> _StubState:
    return _StubState(budgets_remaining=_StubBudgets(**overrides))


def test_returns_unavailable_for_none_input() -> None:
    fact = evaluate_verification(tool_results=None)
    assert isinstance(fact, VerificationFact)
    assert fact.signal == "unavailable"
    assert fact.exit_code is None
    assert fact.ok is True
    assert fact.probed_tool == ""


def test_returns_unavailable_for_empty_list() -> None:
    fact = evaluate_verification(tool_results=[])
    assert fact.signal == "unavailable"
    assert fact.ok is True


def test_returns_unavailable_for_non_dict_entries() -> None:
    fact = evaluate_verification(tool_results=["broken", 123])  # type: ignore[list-item]
    assert fact.signal == "unavailable"


def test_pure_qa_turn_returns_unavailable() -> None:
    results = [
        {
            "tool_name": "web.search",
            "ok": True,
            "data": {"query": "weather"},
        }
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "unavailable"


def test_failed_verifiable_tool_does_not_fire_gate() -> None:
    results = [_exec_run_result(argv=["echo", "hello"], exit_code=1, ok=False)]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "unavailable"


def test_file_write_alone_fires_turn_shape_gate_but_no_probe_found() -> None:
    results = [_file_write_result(path="foo.py")]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "unavailable"
    assert fact.ok is True


def test_mutation_looking_prefix_without_typed_metadata_stays_pure() -> None:
    fact = evaluate_verification(
        tool_results=[
            {
                "tool_name": "file.write",
                "ok": True,
                "data": {"path": "foo.py"},
            }
        ]
    )
    assert fact.signal == "unavailable"
    assert fact.ok is True
    assert fact.probed_tool == ""


def test_remote_mutation_without_probe_records_unavailable() -> None:
    fact = evaluate_verification(
        tool_results=[
            {
                "tool_name": "deploy.release",
                "ok": True,
                "data": {"tool_blast_radius": "remote_mutation"},
            }
        ]
    )
    assert fact.signal == "unavailable"
    assert fact.ok is True


def test_malformed_typed_radius_records_blocked_unavailable_fact() -> None:
    fact = evaluate_verification(
        tool_results=[
            {
                "tool_name": "custom.mutate",
                "ok": True,
                "data": {"tool_blast_radius": "unknown"},
            }
        ]
    )
    assert fact.signal == "unavailable"
    assert fact.ok is False
    assert fact.probed_tool == "custom.mutate"
    assert is_verification_failed(fact) is True


def test_detects_pytest_invocation() -> None:
    results = [
        _file_write_result(path="src/foo.py"),
        _exec_run_result(argv=["pytest", "-q"], exit_code=0),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "tests"
    assert fact.exit_code == 0
    assert fact.ok is True
    assert fact.probed_tool == "exec.run"


def test_detects_python_module_pytest() -> None:
    results = [
        _file_write_result(path="src/foo.py"),
        _exec_run_result(argv=["python", "-m", "pytest", "tests/"], exit_code=0),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "tests"


def test_detects_make_test() -> None:
    results = [
        _file_write_result(path="Makefile"),
        _exec_run_result(argv=["make", "test"], exit_code=0),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "tests"


def test_detects_npm_test() -> None:
    results = [
        _file_write_result(path="src/x.ts"),
        _exec_run_result(argv=["npm", "test"], exit_code=0),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "tests"


def test_detects_cargo_test() -> None:
    results = [
        _file_write_result(path="src/lib.rs"),
        _exec_run_result(argv=["cargo", "test"], exit_code=0),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "tests"


def test_detects_go_test() -> None:
    results = [
        _file_write_result(path="main.go"),
        _exec_run_result(argv=["go", "test", "./..."], exit_code=0),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "tests"


def test_tests_failure_records_ok_false() -> None:
    results = [
        _file_write_result(path="src/foo.py"),
        _exec_run_result(argv=["pytest"], exit_code=1),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "tests"
    assert fact.exit_code == 1
    assert fact.ok is False


def test_tests_takes_priority_over_build() -> None:
    results = [
        _file_write_result(path="src/foo.py"),
        _exec_run_result(argv=["make", "build"], exit_code=0),
        _exec_run_result(argv=["pytest", "-q"], exit_code=0),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "tests"


def test_last_test_invocation_wins_when_multiple_ran() -> None:
    results = [
        _file_write_result(path="src/foo.py"),
        _exec_run_result(argv=["pytest", "tests/a.py"], exit_code=0),
        _exec_run_result(argv=["pytest", "tests/b.py"], exit_code=1),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "tests"
    assert fact.exit_code == 1


def test_detects_mypy() -> None:
    results = [
        _file_write_result(path="src/foo.py"),
        _exec_run_result(argv=["mypy", "src/"], exit_code=0),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "types"
    assert fact.exit_code == 0


def test_detects_pyright() -> None:
    results = [
        _file_write_result(path="src/foo.py"),
        _exec_run_result(argv=["pyright"], exit_code=0),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "types"


def test_detects_ruff_check() -> None:
    results = [
        _file_write_result(path="src/foo.py"),
        _exec_run_result(argv=["ruff", "check", "."], exit_code=0),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "types"


def test_detects_tsc() -> None:
    results = [
        _file_write_result(path="src/x.ts"),
        _exec_run_result(argv=["tsc", "--noEmit"], exit_code=0),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "types"


def test_types_fails_with_exit_code_one() -> None:
    results = [
        _file_write_result(path="src/foo.py"),
        _exec_run_result(argv=["mypy", "src/"], exit_code=1),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "types"
    assert fact.ok is False


def test_detects_make_build() -> None:
    results = [
        _file_write_result(path="Makefile"),
        _exec_run_result(argv=["make", "build"], exit_code=0),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "build"


def test_detects_npm_build() -> None:
    results = [
        _file_write_result(path="package.json"),
        _exec_run_result(argv=["npm", "run", "build"], exit_code=0),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "build"


def test_detects_cargo_build() -> None:
    results = [
        _file_write_result(path="Cargo.toml"),
        _exec_run_result(argv=["cargo", "build"], exit_code=0),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "build"


def test_bare_make_falls_through_to_build() -> None:
    results = [
        _file_write_result(path="Makefile"),
        _exec_run_result(argv=["make"], exit_code=0),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "build"


def test_bare_make_does_not_overshadow_make_test() -> None:
    results = [
        _file_write_result(path="Makefile"),
        _exec_run_result(argv=["make", "test"], exit_code=0),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "tests"


def test_user_supplied_signal_when_no_in_turn_probe() -> None:
    results = [_file_write_result(path="foo.py")]
    fact = evaluate_verification(tool_results=results, user_verification_exit_code=0)
    assert fact.signal == "user"
    assert fact.exit_code == 0
    assert fact.ok is True
    assert fact.probed_tool == "user"


def test_user_supplied_failure() -> None:
    results = [_file_write_result(path="foo.py")]
    fact = evaluate_verification(tool_results=results, user_verification_exit_code=42)
    assert fact.signal == "user"
    assert fact.exit_code == 42
    assert fact.ok is False


def test_in_turn_probe_takes_priority_over_user_signal() -> None:
    results = [
        _file_write_result(path="foo.py"),
        _exec_run_result(argv=["pytest"], exit_code=0),
    ]
    fact = evaluate_verification(tool_results=results, user_verification_exit_code=1)
    assert fact.signal == "tests"


# Unavailable fall-through


def test_unavailable_when_side_effect_but_no_probe() -> None:
    # Wrote a file, ran some other exec command, but no verification.
    results = [
        _file_write_result(path="foo.py"),
        _exec_run_result(argv=["cat", "foo.py"], exit_code=0),
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "unavailable"
    assert fact.ok is True


def test_typed_read_only_git_status_does_not_create_verification_target() -> None:
    results = [_git_status_result()]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "unavailable"


# Command-text extraction robustness


def test_detects_pytest_with_data_command_string() -> None:
    # Producer puts a plain command string instead of argv list.
    results = [
        _file_write_result(path="foo.py"),
        {
            "tool_name": "exec.run",
            "ok": True,
            "data": {"command": "pytest tests/", "exit_code": 0},
        },
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "tests"


def test_detects_pytest_from_top_level_command() -> None:
    # Some producers flatten command onto envelope.
    results = [
        _file_write_result(path="foo.py"),
        {
            "tool_name": "exec.run",
            "ok": True,
            "command": "pytest -q",
            "data": {"exit_code": 0},
        },
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "tests"


def test_detects_pytest_from_content_fallback() -> None:
    # Last-resort: content summary mentions pytest.
    results = [
        _file_write_result(path="foo.py"),
        {
            "tool_name": "exec.run",
            "ok": True,
            "content": "ran pytest in tests/",
            "data": {"exit_code": 0},
        },
    ]
    fact = evaluate_verification(tool_results=results)
    assert fact.signal == "tests"


# is_verification_failed predicate


def test_is_failed_predicate_true_for_failed_probe() -> None:
    fact = VerificationFact(
        signal="tests", exit_code=1, ok=False, probed_tool="exec.run"
    )
    assert is_verification_failed(fact) is True


def test_is_failed_predicate_false_for_passed_probe() -> None:
    fact = VerificationFact(
        signal="tests", exit_code=0, ok=True, probed_tool="exec.run"
    )
    assert is_verification_failed(fact) is False


def test_is_failed_predicate_false_for_unavailable() -> None:
    fact = VerificationFact(signal="unavailable", exit_code=None, ok=True)
    assert is_verification_failed(fact) is False


def test_is_failed_predicate_true_for_malformed_unavailable() -> None:
    fact = VerificationFact(signal="unavailable", exit_code=None, ok=False)
    assert is_verification_failed(fact) is True


def test_is_failed_predicate_false_for_none() -> None:
    assert is_verification_failed(None) is False


# Override helper


def _close_judgment(*, reason: str = "ok") -> ClosureJudgment:
    return ClosureJudgment(
        satisfied=True,
        reason=reason,
        next_action="close",
        final_answer="done.",
    )


def test_override_attaches_passing_fact_without_disposition_change() -> None:
    judgment = _close_judgment()
    fact = VerificationFact(
        signal="tests", exit_code=0, ok=True, probed_tool="exec.run"
    )
    apply_verification_to_judgment(judgment, fact, state=_state())
    assert judgment.verification is fact
    assert judgment.satisfied is True
    assert judgment.next_action == "close"
    assert judgment.final_answer == "done."
    assert VERIFICATION_FAILED_REASON not in judgment.reason


def test_override_attaches_failure_and_flips_close_to_continue_with_budget() -> None:
    judgment = _close_judgment()
    fact = VerificationFact(
        signal="tests", exit_code=1, ok=False, probed_tool="exec.run"
    )
    apply_verification_to_judgment(judgment, fact, state=_state())
    assert judgment.verification is fact
    assert judgment.satisfied is False
    assert judgment.next_action == "continue"
    assert judgment.final_answer is None
    assert VERIFICATION_FAILED_REASON in judgment.reason


def test_override_records_failure_reason_but_finalizes_without_budget() -> None:
    judgment = _close_judgment()
    fact = VerificationFact(
        signal="tests", exit_code=1, ok=False, probed_tool="exec.run"
    )
    apply_verification_to_judgment(judgment, fact, state=_state(tool_calls=0))
    assert judgment.verification is fact
    # No disposition override.
    assert judgment.next_action == "close"
    assert judgment.final_answer == "done."
    # But reason carries the suffix.
    assert VERIFICATION_FAILED_REASON in judgment.reason


def test_override_does_not_fire_when_judge_already_continue() -> None:
    judgment = ClosureJudgment(
        satisfied=False, reason="judge", next_action="continue", final_answer=None
    )
    fact = VerificationFact(
        signal="tests", exit_code=1, ok=False, probed_tool="exec.run"
    )
    apply_verification_to_judgment(judgment, fact, state=_state())
    assert judgment.next_action == "continue"
    assert VERIFICATION_FAILED_REASON not in judgment.reason


def test_override_does_not_fire_for_unavailable_signal() -> None:
    # Even with side effects but no probe found, unavailable doesn't override.
    judgment = _close_judgment()
    fact = VerificationFact(signal="unavailable", exit_code=None, ok=True)
    apply_verification_to_judgment(judgment, fact, state=_state())
    assert judgment.satisfied is True
    assert judgment.next_action == "close"
    assert VERIFICATION_FAILED_REASON not in judgment.reason


def test_override_reason_composition() -> None:
    judgment = _close_judgment(reason="judge_complete")
    fact = VerificationFact(
        signal="types", exit_code=1, ok=False, probed_tool="exec.run"
    )
    apply_verification_to_judgment(judgment, fact, state=_state())
    assert judgment.reason == f"judge_complete; {VERIFICATION_FAILED_REASON}"


def test_override_returns_judgment_for_fluent_use() -> None:
    judgment = _close_judgment()
    fact = VerificationFact(
        signal="tests", exit_code=0, ok=True, probed_tool="exec.run"
    )
    returned = apply_verification_to_judgment(judgment, fact, state=_state())
    assert returned is judgment
