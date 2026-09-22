from __future__ import annotations

import json

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
        events = service._store.list_audit_events(target_id=str(draft["draft_id"]))  # noqa: SLF001
        inspected = next(
            event for event in events if event.event_type == "tool_authoring.inspected"
        )
        assert inspected.version_hash == result["version_hash"]
        details = json.loads(inspected.details_json)
        assert details["recommend_register"] is True
        assert details["recommend_reason"] == "all checks passed"
        assert "source_code" not in details
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
        events = service._store.list_audit_events(target_id=str(draft["draft_id"]))  # noqa: SLF001
        inspected = next(
            event for event in events if event.event_type == "tool_authoring.inspected"
        )
        assert inspected.version_hash == result["version_hash"]
        details = json.loads(inspected.details_json)
        assert details["recommend_register"] is False
        assert details["tests_failed"] == 1
        assert details["recommend_reason"] == "1 held-out tests failed"
        assert not any(
            event.event_type == "tool_authoring.registered" for event in events
        )
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


def test_inspect_draft_rejects_missing_draft(tmp_path) -> None:
    service = build_service(tmp_path)
    try:
        result = service.inspect_draft({"draft_id": "missing", "run_tests": False})
        assert result["error"]["code"] == "DRAFT_NOT_FOUND"
    finally:
        service.close()


def test_inspect_draft_rejects_source_override(tmp_path) -> None:
    service = build_service(tmp_path)
    try:
        draft = service.author_draft(
            _draft_args(
                "def adder(x, y):\n    return x + y\n",
                "def test_add():\n    assert True\n",
            )
        )
        result = service.inspect_draft(
            {
                "draft_id": draft["draft_id"],
                "source_code": "def adder(x, y):\n    return 0\n",
                "run_tests": False,
            }
        )
        assert result["error"]["code"] == "INSPECTION_SOURCE_MISMATCH"
        stored = service.get_draft(str(draft["draft_id"]))
        assert stored is not None
        assert stored.status == "drafted"
        assert stored.inspect_result_json is None
        assert [
            event.event_type
            for event in service._store.list_audit_events(
                target_id=str(draft["draft_id"])
            )  # noqa: SLF001
        ] == ["tool_authoring.drafted"]
    finally:
        service.close()
