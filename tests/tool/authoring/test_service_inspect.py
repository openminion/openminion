from __future__ import annotations

from ._helpers import FakeExecResult, RecordingSandboxRunner, build_service


def _draft_args(source_code: str, tests: str) -> dict[str, object]:
    return {
        "name": "adder",
        "description": "Add two integers",
        "source_code": source_code,
        "unit_tests_source": tests,
        "args_schema": {
            "type": "object",
            "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
            "required": ["x", "y"],
        },
        "returns_schema": {"type": "integer"},
        "requirements": [],
        "dependencies": [],
        "proposed_scope_tier": "POWER_USER",
    }


def test_inspect_draft_clean_path(tmp_path) -> None:
    runner = RecordingSandboxRunner(
        FakeExecResult(returncode=0, stdout="1 passed in 0.01s\n")
    )
    service = build_service(tmp_path, sandbox_runner=runner)
    try:
        draft = service.author_draft(
            _draft_args(
                "def adder(x, y):\n    return x + y\n",
                "from tool_impl import adder\n\ndef test_add():\n    assert adder(1, 2) == 3\n",
            )
        )
        result = service.inspect_draft(
            {"draft_id": draft["draft_id"], "run_tests": True}
        )
        assert result["ok"] is True
        assert result["risk_level"] == "low"
        assert result["recommend_register"] is True
        stored = service.get_draft(str(draft["draft_id"]))
        assert stored is not None
        assert stored.status == "inspected"
        assert stored.inspect_result_json is not None
    finally:
        service.close()


def test_inspect_draft_critical_static_finding_blocks_register(tmp_path) -> None:
    service = build_service(tmp_path)
    try:
        draft = service.author_draft(
            _draft_args(
                "def adder(x, y):\n    exec('print(x)')\n    return x + y\n",
                "def test_placeholder():\n    assert True\n",
            )
        )
        result = service.inspect_draft(
            {"draft_id": draft["draft_id"], "run_tests": False}
        )
        assert result["risk_level"] == "critical"
        assert result["recommend_register"] is False
    finally:
        service.close()


def test_inspect_draft_failing_tests_block_register(tmp_path) -> None:
    runner = RecordingSandboxRunner(
        FakeExecResult(returncode=1, stdout="1 failed in 0.01s\n")
    )
    service = build_service(tmp_path, sandbox_runner=runner)
    try:
        draft = service.author_draft(
            _draft_args(
                "def adder(x, y):\n    return x + y\n",
                "def test_fail():\n    assert False\n",
            )
        )
        result = service.inspect_draft(
            {"draft_id": draft["draft_id"], "run_tests": True}
        )
        assert result["recommend_register"] is False
        assert result["test_results"]["failed"] == 1
    finally:
        service.close()


def test_inspect_draft_supports_ad_hoc_source(tmp_path) -> None:
    runner = RecordingSandboxRunner(
        FakeExecResult(returncode=0, stdout="1 passed in 0.01s\n")
    )
    service = build_service(tmp_path, sandbox_runner=runner)
    try:
        result = service.inspect_draft(
            {
                "source_code": "def adder(x, y):\n    return x + y\n",
                "unit_tests_source": "def test_add():\n    assert True\n",
                "run_tests": True,
            }
        )
        assert result["ok"] is True
        assert result["draft_id"] is None
    finally:
        service.close()
