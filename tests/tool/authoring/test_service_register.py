from __future__ import annotations

import json

import pytest

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
        assert result["exposure_profile_id"] == "authored_authored_adder_v1"
        assert "authored.adder@v1" in registry.list()
        profile = registry.exposure_service.profile(result["exposure_profile_id"])
        assert profile is not None
        assert profile.tool_names == frozenset({"authored.adder@v1"})
        assert profile.default_active is False
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
    service, registry, policy_ctl = _inspectable_service(tmp_path)
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
        assert registry.list() == {}
        assert policy_ctl.list_grants(active_only=True) == []
        assert policy_ctl.registered_risks == {}
    finally:
        service.close()


def test_register_draft_reinspects_stale_receipt(tmp_path) -> None:
    service, _, _ = _inspectable_service(tmp_path)
    try:
        draft = service.author_draft(
            _base_args(
                "def adder(x, y):\n    return x + y\n",
                "def test_add():\n    assert True\n",
            )
        )
        service.inspect_draft({"draft_id": draft["draft_id"], "run_tests": True})
        service._store.update_draft_inspection(
            str(draft["draft_id"]),
            status="inspected",
            inspect_result_json=json.dumps(
                {
                    "ok": True,
                    "risk_level": "low",
                    "recommend_register": True,
                    "version_hash": "stale",
                }
            ),
        )

        result = service.register_draft(
            {"draft_id": draft["draft_id"]}, agent_id="agent-1"
        )

        assert result["ok"] is True
        stored = service.get_draft(str(draft["draft_id"]))
        assert stored is not None
        assert stored.inspect_result_json is not None
        assert json.loads(stored.inspect_result_json)["version_hash"] != "stale"
    finally:
        service.close()


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize(
    "failed_result",
    [
        FakeExecResult(returncode=1, stdout="1 failed in 0.01s\n"),
        FakeExecResult(returncode=2, stderr="pytest collection error\n"),
        FakeExecResult(returncode=0, stdout="no tests ran in 0.01s\n"),
        FakeExecResult(returncode=124, timed_out=True),
    ],
)
def test_promotion_requires_passing_reinspection(
    tmp_path, failed_result: FakeExecResult, force: bool
) -> None:
    registry = ToolRegistry()
    runner = RecordingSandboxRunner(
        FakeExecResult(returncode=0, stdout="1 passed in 0.01s\n")
    )
    service = build_service(
        tmp_path,
        registry=registry,
        policy_ctl=FakePolicyCtl(),
        sandbox_runner=runner,
    )
    try:
        draft = service.author_draft(
            _base_args(
                "def adder(x, y):\n    return x + y\n",
                "def test_add():\n    assert True\n",
            )
        )
        service.inspect_draft({"draft_id": draft["draft_id"], "run_tests": True})
        registered = service.register_draft(
            {"draft_id": draft["draft_id"]}, agent_id="agent-1"
        )
        runner.result = failed_result

        result = service.promote_tool(
            str(registered["tool_name"]), force=force, actor_id="agent-1"
        )

        assert result["error"]["code"] == "PROMOTION_REJECTED"
        stored = service.get_authored_tool_detail(str(registered["tool_name"]))
        assert stored is not None
        assert stored["tier"] == "experimental"
    finally:
        service.close()
