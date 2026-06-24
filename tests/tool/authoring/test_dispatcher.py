from __future__ import annotations

from openminion.modules.tool.authoring.schemas import AuthoredToolRow

from ._helpers import FakeExecResult, RecordingSandboxRunner, build_service


def _tool_row(tool_name: str) -> AuthoredToolRow:
    return AuthoredToolRow(
        tool_name=tool_name,
        local_name="adder",
        version_number=1,
        version_hash="hash1",
        source_code="def adder(x, y):\n    return x + y\n",
        unit_tests_source="def test_add():\n    assert True\n",
        args_schema_json='{"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"}},"required":["x","y"]}',
        returns_schema_json='{"type":"integer"}',
        description="Add two integers",
        dependencies_json="[]",
        tier="experimental",
        min_scope="POWER_USER",
        policy_grant_id="grant-1",
        created_at="2026-05-21T00:00:00Z",
        updated_at="2026-05-21T00:00:00Z",
        created_by_agent_id="agent-1",
        promoted_at=None,
        promoted_by=None,
        success_count=0,
        failure_count=0,
        last_invocation_at=None,
        removed_at=None,
        removed_by=None,
    )


def test_service_invoke_updates_success_counters(tmp_path) -> None:
    runner = RecordingSandboxRunner(
        FakeExecResult(stdout='{"ok": true, "content": "3", "data": {"result": 3}}')
    )
    service = build_service(tmp_path, sandbox_runner=runner)
    try:
        service._store.insert_authored_tool(_tool_row("authored.adder@v1"))  # noqa: SLF001
        result = service.invoke("authored.adder@v1", {"x": 1, "y": 2})
        assert result["ok"] is True
        row = service.get_authored_tool("authored.adder@v1")
        assert row is not None
        assert row.success_count == 1
        assert row.failure_count == 0
    finally:
        service.close()


def test_service_invoke_surfaces_subprocess_error(tmp_path) -> None:
    runner = RecordingSandboxRunner(FakeExecResult(returncode=1, stderr="boom"))
    service = build_service(tmp_path, sandbox_runner=runner)
    try:
        service._store.insert_authored_tool(_tool_row("authored.adder@v1"))  # noqa: SLF001
        result = service.invoke("authored.adder@v1", {"x": 1, "y": 2})
        assert result["ok"] is False
        assert result["error"]["code"] == "AUTHORED_TOOL_FAILED"
        row = service.get_authored_tool("authored.adder@v1")
        assert row is not None
        assert row.failure_count == 1
    finally:
        service.close()


def test_service_invoke_reports_time_limit(tmp_path) -> None:
    runner = RecordingSandboxRunner(FakeExecResult(timed_out=True))
    service = build_service(tmp_path, sandbox_runner=runner)
    try:
        service._store.insert_authored_tool(_tool_row("authored.adder@v1"))  # noqa: SLF001
        result = service.invoke("authored.adder@v1", {"x": 1, "y": 2})
        assert result["ok"] is False
        assert result["error"]["code"] == "AUTHORED_TOOL_LIMIT_EXCEEDED"
    finally:
        service.close()


def test_service_invoke_rejects_removed_tool(tmp_path) -> None:
    runner = RecordingSandboxRunner(
        FakeExecResult(stdout='{"ok": true, "content": "3"}')
    )
    service = build_service(tmp_path, sandbox_runner=runner)
    try:
        row = _tool_row("authored.adder@v1")
        row = AuthoredToolRow(**{**row.__dict__, "removed_at": "2026-05-21T01:00:00Z"})
        service._store.insert_authored_tool(row)  # noqa: SLF001
        result = service.invoke("authored.adder@v1", {"x": 1, "y": 2})
        assert result["ok"] is False
        assert result["error"]["code"] == "AUTHORED_TOOL_REMOVED"
    finally:
        service.close()
