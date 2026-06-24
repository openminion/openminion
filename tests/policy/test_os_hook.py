from __future__ import annotations

import importlib
import sys
from pathlib import Path

from openminion.modules.policy.models import PolicyConfig, PolicyGrantInput
from openminion.modules.policy.runtime.os_hook import PolicyToolHook
from openminion.modules.policy.runtime.service import PolicyCtl

_ROOT = Path(__file__).resolve().parents[2]
_TOOL_SRC = _ROOT / "openminion-tool" / "src"
if _TOOL_SRC.exists() and str(_TOOL_SRC) not in sys.path:
    sys.path.insert(0, str(_TOOL_SRC))

_TOOL_CONTRACT_IMPL = importlib.import_module("openminion.modules.tool.plugin_contract")
ToolCapabilities = _TOOL_CONTRACT_IMPL.ToolCapabilities
ToolContext = _TOOL_CONTRACT_IMPL.ToolContext
ToolInvocation = _TOOL_CONTRACT_IMPL.ToolInvocation


def test_os_hook_maps_require_confirm(tmp_path):
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    hook = PolicyToolHook(ctl)
    try:
        decision = hook.check(
            invocation=ToolInvocation(
                tool="ssh", method="exec", args={"host": "prod-a", "argv": ["ls"]}
            ),
            ctx=ToolContext(trace_id="trace-1", session_id="sess-1"),
            capabilities=ToolCapabilities(risk_level="high", side_effects="remote"),
        )
        assert decision.action == "require_confirm"
        assert decision.code == "CONFIRM_REQUIRED"
        assert isinstance(decision.details.get("confirm_request"), dict)
    finally:
        ctl.close()


def test_os_hook_allows_when_grant_exists(tmp_path):
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    hook = PolicyToolHook(ctl)
    try:
        ctl.create_grant(
            PolicyGrantInput(
                effect="allow",
                tool="ssh",
                method="exec",
                target_json={"host": "prod-*"},
                duration_type="forever",
            )
        )
        decision = hook.check(
            invocation=ToolInvocation(
                tool="ssh", method="exec", args={"host": "prod-a", "argv": ["ls"]}
            ),
            ctx=ToolContext(trace_id="trace-1", session_id="sess-1"),
            capabilities=ToolCapabilities(risk_level="high", side_effects="remote"),
        )
        assert decision.action == "allow"
        assert decision.code == "POLICY_ALLOW"
    finally:
        ctl.close()


def test_os_hook_log_only_never_blocks(tmp_path):
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="log_only")
    )
    hook = PolicyToolHook(ctl)
    try:
        decision = hook.check(
            invocation=ToolInvocation(
                tool="ssh", method="exec", args={"host": "prod-a", "argv": ["ls"]}
            ),
            ctx=ToolContext(trace_id="trace-1", session_id="sess-1"),
            capabilities=ToolCapabilities(risk_level="high", side_effects="remote"),
        )
        assert decision.action == "allow"
        assert decision.details.get("reason_code") == "LOG_ONLY_ALLOW"
    finally:
        ctl.close()
