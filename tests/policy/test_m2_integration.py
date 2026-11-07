from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.schemas import ToolCommand
from openminion.modules.policy.adapters.brain import PolicyCtlBrainAdapter
from openminion.modules.policy.models import PolicyConfig, PolicyGrantInput, RiskSpec
from openminion.modules.policy.runtime.service import PolicyCtl


def _brain_ctx(
    session_id: str = "sess-1",
    agent_id: str = "agent-1",
    trace_id: str = "trace-1",
) -> dict:
    return {"session_id": session_id, "agent_id": agent_id, "trace_id": trace_id}


def _tool_command(
    tool: str = "fs",
    method: str = "rm",
    args: dict | None = None,
    risk_level: str = "high",
) -> dict:
    return {
        "kind": "tool",
        "tool": tool,
        "method": method,
        "args": args or {"path": "/tmp/demo.txt"},
        "risk_level": risk_level,
        "command_id": "cmd-test-1",
    }


def _grant_once(adapter) -> None:
    """Approve one allow_once grant for the destructive command under test."""
    adapter.grant_once_from_confirmation(
        command=SimpleNamespace(
            kind="tool",
            tool_name="fs.rm",
            args={"path": "/tmp/demo.txt"},
            idempotency_key="cmd-test-1",
            risk_level="high",
        ),
        working_state=SimpleNamespace(
            session_id="sess-1", agent_id="agent-1", trace_id="trace-1"
        ),
        session_context={"subject_id": "local"},
    )


def test_brain_adapter_require_confirm_for_destructive_tool(tmp_path):
    adapter = PolicyCtlBrainAdapter.with_sqlite(tmp_path / "p.db")
    try:
        result = adapter.check_command(_tool_command(), _brain_ctx())
        assert result.requires_confirmation()
        assert result.code in {"HIGH_RISK", "DEFAULT_CONFIRM"}
    finally:
        adapter.close()


def test_brain_adapter_classifies_bounded_exec_diagnostic_as_read_only(tmp_path):
    ctl = PolicyCtl.with_sqlite(tmp_path / "exec-policy.db")
    ctl.register_risk(
        "exec.run",
        RiskSpec(
            risk_class="destructive",
            side_effects="local",
            reversibility="irreversible",
            default_confirm=True,
        ),
    )
    adapter = PolicyCtlBrainAdapter(ctl)
    try:
        result = adapter.evaluate(
            command=ToolCommand(
                title="Ping Google",
                tool_name="exec.run",
                args={"command": "ping -c 4 google.com"},
                risk_level="high",
            ),
            working_state=SimpleNamespace(
                session_id="sess-1",
                agent_id="agent-1",
                trace_id="trace-1",
                permission_mode="ask",
                permission_overrides={},
            ),
            session_context={},
        )

        assert result.outcome == "ALLOW"
        assert result.policy_facts["risk"] == {
            "risk_class": "read",
            "side_effects": "none",
            "reversibility": "reversible",
            "default_confirm": False,
            "sensitive_targets": [],
        }
    finally:
        ctl.close()


def test_brain_adapter_allows_with_active_grant(tmp_path):
    adapter = PolicyCtlBrainAdapter.with_sqlite(tmp_path / "p.db")
    try:
        ctl = adapter._ctl
        ctl.create_grant(
            PolicyGrantInput(
                effect="allow",
                tool="fs",
                method="rm",
                duration_type="forever",
                target_json={"path_prefix": "/tmp"},
            )
        )
        result = adapter.check_command(_tool_command(), _brain_ctx())
        assert result.is_allowed()
    finally:
        adapter.close()


def test_brain_adapter_log_only_never_blocks(tmp_path):
    adapter = PolicyCtlBrainAdapter.with_sqlite(
        tmp_path / "p.db", config=PolicyConfig(mode="log_only")
    )
    try:
        result = adapter.check_command(_tool_command(), _brain_ctx())
        assert result.is_allowed()
        assert result.code == "LOG_ONLY_ALLOW"
    finally:
        adapter.close()


def test_brain_adapter_a2a_command_normalised(tmp_path):
    adapter = PolicyCtlBrainAdapter.with_sqlite(tmp_path / "p.db")
    try:
        a2a_cmd = {
            "kind": "a2a",
            "provider": "data_agent",
            "action": "query",
            "args": {"sql": "SELECT 1"},
            "risk_level": "low",
            "command_id": "cmd-a2a-1",
        }
        result = adapter.check_command(a2a_cmd, _brain_ctx())
        assert result.action in {"allow", "require_confirm", "deny"}
        assert result.code
    finally:
        adapter.close()


def test_policy_decisions_are_per_session(tmp_path):
    ctl = PolicyCtl.with_sqlite(tmp_path / "p.db")
    try:
        # Grant for session-A only (session_id scoping not enforced at grant level,
        # but we verify that a grant created under agent-A is visible consistently)
        ctl.create_grant(
            PolicyGrantInput(
                effect="allow",
                tool="fs",
                method="write",
                duration_type="forever",
                target_json={"path_prefix": "/data/agent-a/"},
            )
        )
        ctx_a = {"session_id": "sess-a", "agent_id": "agent-a", "trace_id": "t-1"}
        ctx_b = {"session_id": "sess-b", "agent_id": "agent-b", "trace_id": "t-2"}
        inv = {
            "tool": "fs",
            "method": "write",
            "args": {"path": "/data/agent-a/file.txt"},
        }

        result_a = ctl.check(inv, ctx_a)
        result_b = ctl.check(inv, ctx_b)

        # Both sessions see the global grant (policy is not session-scoped by default)
        assert result_a.decision == "ALLOW"
        assert result_b.decision == "ALLOW"  # global grant applies to all unless scoped
    finally:
        ctl.close()


def test_grant_max_uses_exhausts_and_falls_back_to_confirm(tmp_path):
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "p.db",
        config=PolicyConfig(
            default_action="require_confirm", allow_read_only_without_prompt=False
        ),
    )
    try:
        ctl.create_grant(
            PolicyGrantInput(
                effect="allow",
                tool="fs",
                method="read",
                duration_type="forever",
                max_uses=2,
                target_json={"path_prefix": "/data/"},
            )
        )
        inv = {"tool": "fs", "method": "read", "args": {"path": "/data/file.txt"}}
        ctx = {"session_id": "sess-1", "agent_id": "agent-1", "trace_id": "t-1"}

        result1 = ctl.check(inv, ctx)
        result2 = ctl.check(inv, ctx)
        result3 = ctl.check(inv, ctx)

        assert result1.decision == "ALLOW"
        assert result2.decision == "ALLOW"
        assert result3.decision == "REQUIRE_CONFIRM"
    finally:
        ctl.close()


def test_brain_adapter_missing_tool_defaults_gracefully(tmp_path):
    adapter = PolicyCtlBrainAdapter.with_sqlite(tmp_path / "p.db")
    try:
        bad_cmd = {"kind": "tool", "method": "run", "args": {}, "risk_level": "low"}
        result = adapter.check_command(bad_cmd, _brain_ctx())
        assert result.action in {"allow", "require_confirm", "deny"}
    finally:
        adapter.close()


def test_brain_adapter_empty_context_does_not_raise(tmp_path):
    adapter = PolicyCtlBrainAdapter.with_sqlite(tmp_path / "p.db")
    try:
        result = adapter.check_command(_tool_command(), {})
        assert result.action in {"allow", "require_confirm", "deny"}
    finally:
        adapter.close()


def test_brain_adapter_result_to_dict_is_serialisable(tmp_path):
    import json

    adapter = PolicyCtlBrainAdapter.with_sqlite(tmp_path / "p.db")
    try:
        result = adapter.check_command(_tool_command(), _brain_ctx())
        serialised = json.dumps(result.to_dict())
        assert serialised
    finally:
        adapter.close()


def test_brain_adapter_does_not_lower_registered_risk(tmp_path):
    adapter = PolicyCtlBrainAdapter.with_sqlite(tmp_path / "p.db")
    try:
        result = adapter.check_command(
            _tool_command(),
            _brain_ctx(),
            risk_override={
                "risk_class": "read",
                "side_effects": "none",
                "reversibility": "reversible",
                "default_confirm": False,
            },
        )
        assert result.requires_confirmation()
        assert result.code == "HIGH_RISK"
    finally:
        adapter.close()


def test_brain_adapter_confirmation_roundtrip(tmp_path):
    adapter = PolicyCtlBrainAdapter.with_sqlite(tmp_path / "p.db")
    try:
        result = adapter.check_command(_tool_command(), _brain_ctx())
        assert result.requires_confirmation()

        _grant_once(adapter)

        result2 = adapter.check_command(_tool_command(), _brain_ctx())
        assert result2.is_allowed()
    finally:
        adapter.close()


def test_once_grant_does_not_authorise_a_different_payload(tmp_path):
    """An allow_once approval is bound to tool, method and arguments."""
    adapter = PolicyCtlBrainAdapter.with_sqlite(tmp_path / "p.db")
    try:
        assert adapter.check_command(
            _tool_command(), _brain_ctx()
        ).requires_confirmation()

        _grant_once(adapter)

        replay = adapter.check_command(
            _tool_command(args={"path": "/tmp/other.txt"}), _brain_ctx()
        )

        assert not replay.is_allowed()
        assert replay.requires_confirmation()
        # The refused replay must not consume the approval it did not match.
        assert adapter.check_command(_tool_command(), _brain_ctx()).is_allowed()
        # The grant is exactly once: the payload it authorised now needs approval.
        assert adapter.check_command(
            _tool_command(), _brain_ctx()
        ).requires_confirmation()
    finally:
        adapter.close()


def test_plan_mode_requires_confirmation_more_aggressively(tmp_path):
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "p.db",
        config=PolicyConfig(
            default_action="allow", allow_read_only_without_prompt=True
        ),
    )
    try:
        invocation = {
            "tool": "fs",
            "method": "write",
            "args": {"path": "/tmp/demo.txt"},
        }
        act_single = ctl.check(
            invocation,
            {
                "session_id": "sess-1",
                "agent_id": "agent-1",
                "trace_id": "t-1",
                "mode_name": "act_single",
            },
        )
        plan_mode = ctl.check(
            invocation,
            {
                "session_id": "sess-1",
                "agent_id": "agent-1",
                "trace_id": "t-1",
                "mode_name": "plan",
            },
        )

        assert act_single.decision == "REQUIRE_CONFIRM"
        assert act_single.reason_code != "PLAN_MODE_CONFIRM"
        assert plan_mode.decision == "REQUIRE_CONFIRM"
        assert plan_mode.reason_code == "PLAN_MODE_CONFIRM"
    finally:
        ctl.close()
