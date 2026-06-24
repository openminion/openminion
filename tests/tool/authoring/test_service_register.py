from __future__ import annotations

from openminion.modules.tool import ToolRegistry

from ._helpers import (
    FakePolicyCtl,
    FakeExecResult,
    RecordingSandboxRunner,
    build_service,
)


def _base_args(source_code: str, tests: str) -> dict[str, object]:
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


def _inspectable_service(tmp_path):
    registry = ToolRegistry()
    policy_ctl = FakePolicyCtl()
    runner = RecordingSandboxRunner(
        FakeExecResult(returncode=0, stdout="1 passed in 0.01s\n")
    )
    service = build_service(
        tmp_path,
        registry=registry,
        policy_ctl=policy_ctl,
        sandbox_runner=runner,
    )
    return service, registry, policy_ctl


def test_register_draft_first_registration_is_v1(tmp_path) -> None:
    service, registry, policy_ctl = _inspectable_service(tmp_path)
    try:
        draft = service.author_draft(
            _base_args(
                "def adder(x, y):\n    return x + y\n",
                "def test_add():\n    assert True\n",
            ),
            agent_id="agent-1",
        )
        service.inspect_draft({"draft_id": draft["draft_id"], "run_tests": True})
        result = service.register_draft(
            {"draft_id": draft["draft_id"]}, agent_id="agent-1"
        )
        assert result["ok"] is True
        assert result["tool_name"] == "authored.adder@v1"
        assert "authored.adder@v1" in registry.list()
        assert policy_ctl.list_grants(active_only=True)
    finally:
        service.close()


def test_register_draft_second_hash_bumps_version(tmp_path) -> None:
    service, registry, policy_ctl = _inspectable_service(tmp_path)
    try:
        draft_one = service.author_draft(
            _base_args(
                "def adder(x, y):\n    return x + y\n",
                "def test_add():\n    assert True\n",
            )
        )
        service.inspect_draft({"draft_id": draft_one["draft_id"], "run_tests": True})
        first = service.register_draft(
            {"draft_id": draft_one["draft_id"]}, agent_id="agent-1"
        )
        draft_two = service.author_draft(
            _base_args(
                "def adder(x, y):\n    return x - y\n",
                "def test_add():\n    assert True\n",
            )
        )
        service.inspect_draft({"draft_id": draft_two["draft_id"], "run_tests": True})
        second = service.register_draft(
            {"draft_id": draft_two["draft_id"]}, agent_id="agent-1"
        )
        assert first["tool_name"] == "authored.adder@v1"
        assert second["tool_name"] == "authored.adder@v2"
        assert second["idempotent"] is False
        assert len(policy_ctl.list_grants(active_only=True)) == 2
        assert "authored.adder@v2" in registry.list()
    finally:
        service.close()


def test_register_draft_same_hash_is_idempotent(tmp_path) -> None:
    service, _, _ = _inspectable_service(tmp_path)
    try:
        draft = service.author_draft(
            _base_args(
                "def adder(x, y):\n    return x + y\n",
                "def test_add():\n    assert True\n",
            )
        )
        service.inspect_draft({"draft_id": draft["draft_id"], "run_tests": True})
        first = service.register_draft(
            {"draft_id": draft["draft_id"]}, agent_id="agent-1"
        )
        second = service.register_draft(
            {"draft_id": draft["draft_id"]}, agent_id="agent-1"
        )
        assert first["idempotent"] is False
        assert second["idempotent"] is True
        assert second["tool_name"] == first["tool_name"]
    finally:
        service.close()


def test_register_draft_rejects_uninspected_or_high_risk(tmp_path) -> None:
    service, _, _ = _inspectable_service(tmp_path)
    try:
        draft = service.author_draft(
            _base_args(
                "def adder(x, y):\n    exec('print(x)')\n    return x + y\n",
                "def test_add():\n    assert True\n",
            )
        )
        uninspected = service.register_draft({"draft_id": draft["draft_id"]})
        assert uninspected["error"]["code"] == "INSPECT_NOT_PASSED"
        service.inspect_draft({"draft_id": draft["draft_id"], "run_tests": False})
        critical = service.register_draft({"draft_id": draft["draft_id"]})
        assert critical["error"]["code"] == "INSPECT_NOT_PASSED"
    finally:
        service.close()
