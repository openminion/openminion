from __future__ import annotations

from openminion.modules.policy.models import PolicyConfig, RiskSpec
from openminion.modules.policy.runtime.service import PolicyCtl


def _ctx() -> dict[str, str]:
    return {"trace_id": "trace-1", "session_id": "sess-1", "agent_id": "agent-1"}


def test_policy_check_overrides_preserve_existing_behavior(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db",
        config=PolicyConfig(mode="enforce"),
    )
    try:
        decision = ctl.check(
            {"tool": "fs", "method": "rm", "args": {"path": "/tmp/demo.txt"}},
            _ctx(),
        )
        assert decision.decision == "REQUIRE_CONFIRM"
    finally:
        ctl.close()


def test_policy_check_overrides_default_action_allow(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db",
        config=PolicyConfig(mode="enforce"),
    )
    try:
        decision = ctl.check(
            {"tool": "fs", "method": "read", "args": {"path": "/tmp/demo.txt"}},
            _ctx(),
            risk_override=RiskSpec(
                risk_class="write",
                side_effects="none",
                reversibility="reversible",
                default_confirm=False,
            ),
            config_overrides=PolicyConfig(
                mode="enforce",
                default_action="allow",
                allow_read_only_without_prompt=True,
            ),
        )
        assert decision.decision == "ALLOW"
    finally:
        ctl.close()


def test_policy_check_overrides_read_only_prompting(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db",
        config=PolicyConfig(
            mode="enforce",
            default_action="allow",
            allow_read_only_without_prompt=True,
        ),
    )
    try:
        decision = ctl.check(
            {"tool": "fs", "method": "read", "args": {"path": "/tmp/demo.txt"}},
            _ctx(),
            risk_override=RiskSpec(
                risk_class="read",
                side_effects="none",
                reversibility="reversible",
                default_confirm=False,
            ),
            config_overrides=PolicyConfig(
                mode="enforce",
                default_action="require_confirm",
                allow_read_only_without_prompt=False,
            ),
        )
        assert decision.decision == "REQUIRE_CONFIRM"
        assert decision.reason_code == "DEFAULT_CONFIRM"
    finally:
        ctl.close()


def test_policy_check_overrides_disabled_mode_allows(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db",
        config=PolicyConfig(mode="enforce"),
    )
    try:
        decision = ctl.check(
            {"tool": "fs", "method": "rm", "args": {"path": "/tmp/demo.txt"}},
            _ctx(),
            config_overrides=PolicyConfig(mode="disabled"),
        )
        assert decision.decision == "ALLOW"
        assert decision.reason_code == "POLICY_DISABLED"
    finally:
        ctl.close()
