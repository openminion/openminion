from __future__ import annotations

from ._helpers import (
    FakeExecResult,
    FakePolicyCtl,
    RecordingSandboxRunner,
    build_service,
)


def _base_args(source_code: str) -> dict[str, object]:
    return {
        "name": "adder",
        "description": "Add two integers",
        "source_code": source_code,
        "unit_tests_source": "def test_add():\n    assert True\n",
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


def test_policy_grant_lifecycle_register_invoke_remove(tmp_path) -> None:
    policy_ctl = FakePolicyCtl()
    service = build_service(
        tmp_path,
        policy_ctl=policy_ctl,
        sandbox_runner=RecordingSandboxRunner(
            FakeExecResult(returncode=0, stdout="1 passed in 0.01s\n")
        ),
    )
    try:
        draft = service.author_draft(_base_args("def adder(x, y):\n    return x + y\n"))
        service.inspect_draft({"draft_id": draft["draft_id"], "run_tests": True})
        registered = service.register_draft(
            {"draft_id": draft["draft_id"]}, agent_id="agent-1"
        )
        active = policy_ctl.list_grants(active_only=True)
        assert len(active) == 1
        assert active[0]["tool"] == registered["tool_name"]

        service._dispatcher._sandbox_runner = RecordingSandboxRunner(  # noqa: SLF001
            FakeExecResult(stdout='{"ok": true, "content": "3", "data": {"result": 3}}')
        )
        invoke = service.invoke(registered["tool_name"], {"x": 1, "y": 2})
        assert invoke["ok"] is True

        removed = service.remove_tool(
            registered["tool_name"], actor_id="toolctl", reason="cleanup"
        )
        assert removed["ok"] is True
        assert policy_ctl.list_grants(active_only=True) == []
    finally:
        service.close()
