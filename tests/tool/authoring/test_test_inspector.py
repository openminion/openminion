from __future__ import annotations

from openminion.modules.tool.authoring.runtime.tests import run_tool_tests

from ._helpers import FakeExecResult, RecordingSandboxRunner


def test_run_tool_tests_passes_and_strips_openminion_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENMINION_SHOULD_NOT_LEAK", "secret")
    runner = RecordingSandboxRunner(
        FakeExecResult(returncode=0, stdout="2 passed in 0.01s\n")
    )

    result = run_tool_tests(
        source_code="def add(x, y):\n    return x + y\n",
        unit_tests_source="from tool_impl import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        entry_function="add",
        sandbox_runner=runner,
    )

    assert result.passed == 2
    assert result.failed == 0
    spec, sandbox = runner.calls[0]
    assert "OPENMINION_SHOULD_NOT_LEAK" not in spec.env
    assert sandbox.timeout_s == 30


def test_run_tool_tests_reports_failures() -> None:
    runner = RecordingSandboxRunner(
        FakeExecResult(returncode=1, stdout="1 passed, 1 failed in 0.01s\n")
    )

    result = run_tool_tests(
        source_code="def add(x, y):\n    return x + y\n",
        unit_tests_source="def test_fail():\n    assert False\n",
        entry_function="add",
        sandbox_runner=runner,
    )

    assert result.passed == 1
    assert result.failed == 1
    assert result.errors


def test_run_tool_tests_reports_collection_error() -> None:
    runner = RecordingSandboxRunner(
        FakeExecResult(returncode=2, stdout="ERROR collecting test_tool_impl.py\n")
    )

    result = run_tool_tests(
        source_code="def add(x, y):\n    return x + y\n",
        unit_tests_source="import missing_module\n",
        entry_function="add",
        sandbox_runner=runner,
    )

    assert result.ran == 1
    assert result.errors[0]["message"] == "TEST_COLLECTION_FAILED"


def test_run_tool_tests_reports_timeout() -> None:
    runner = RecordingSandboxRunner(FakeExecResult(timed_out=True))

    result = run_tool_tests(
        source_code="def add(x, y):\n    return x + y\n",
        unit_tests_source="def test_sleep():\n    import time; time.sleep(60)\n",
        entry_function="add",
        sandbox_runner=runner,
    )

    assert result.timed_out is True
    assert result.errors[0]["message"] == "AUTHORED_TOOL_LIMIT_EXCEEDED"
