from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time

from openminion.modules.policy.models import (
    PolicyConfig,
    PolicyGrantInput,
    stable_invocation_hash,
)
from openminion.modules.policy.runtime.service import PolicyCtl


def _invocation(path: str = "/tmp/demo.txt") -> dict:
    return {"tool": "fs", "method": "rm", "args": {"path": path}}


def _ctx() -> dict:
    return {"trace_id": "trace-1", "session_id": "sess-1", "agent_id": "agent-1"}


def test_disabled_mode_allows_without_confirmation(tmp_path):
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="disabled")
    )
    try:
        decision = ctl.check(_invocation(), _ctx())
        assert decision.decision == "ALLOW"
        assert decision.reason_code == "POLICY_DISABLED"
    finally:
        ctl.close()


def test_log_only_mode_records_would_enforce_decision(tmp_path):
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="log_only")
    )
    try:
        decision = ctl.check(_invocation(), _ctx())
        assert decision.decision == "ALLOW"
        assert decision.reason_code == "LOG_ONLY_ALLOW"
        assert decision.details["would_decision"] == "REQUIRE_CONFIRM"

        rows = ctl.list_decisions(limit=1)
        assert rows
        assert rows[0]["decision"] == "require_confirm"
    finally:
        ctl.close()


def test_enforce_destructive_requires_confirmation(tmp_path):
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    try:
        decision = ctl.check(_invocation(), _ctx())
        assert decision.decision == "REQUIRE_CONFIRM"
        assert decision.reason_code in {"HIGH_RISK", "DEFAULT_CONFIRM"}
        assert isinstance(decision.confirm_request, dict)
    finally:
        ctl.close()


def test_allow_until_grant_expires(tmp_path):
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    try:
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
        ctl.create_grant(
            PolicyGrantInput(
                effect="allow",
                tool="fs",
                method="rm",
                duration_type="until",
                expires_at=expires_at,
                target_json={"path_prefix": "/tmp"},
            )
        )

        allowed = ctl.check(_invocation("/tmp/expiring.txt"), _ctx())
        assert allowed.decision == "ALLOW"

        time.sleep(1.2)
        ctl.cleanup_expired()
        expired = ctl.check(_invocation("/tmp/expiring.txt"), _ctx())
        assert expired.decision == "REQUIRE_CONFIRM"
    finally:
        ctl.close()


def test_allow_once_is_hash_bound_and_single_use(tmp_path):
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    try:
        invocation = _invocation("/tmp/once.txt")
        once_hash = stable_invocation_hash(
            tool="fs", method="rm", args=invocation["args"]
        )
        ctl.create_grant(
            PolicyGrantInput(
                effect="allow",
                tool="fs",
                method="rm",
                duration_type="once",
                invocation_hash=once_hash,
            )
        )

        first = ctl.check(invocation, _ctx())
        second = ctl.check(invocation, _ctx())
        other = ctl.check(_invocation("/tmp/other.txt"), _ctx())

        assert first.decision == "ALLOW"
        assert second.decision == "REQUIRE_CONFIRM"
        assert other.decision == "REQUIRE_CONFIRM"
    finally:
        ctl.close()


def test_revoke_grant_blocks_subsequent_calls(tmp_path):
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    try:
        grant_id = ctl.create_grant(
            PolicyGrantInput(
                effect="allow",
                tool="fs",
                method="rm",
                duration_type="forever",
                target_json={"path_prefix": "/tmp"},
            )
        )
        assert ctl.check(_invocation("/tmp/revoke.txt"), _ctx()).decision == "ALLOW"

        revoked = ctl.revoke_grant(grant_id)
        assert revoked is True

        blocked = ctl.check(_invocation("/tmp/revoke.txt"), _ctx())
        assert blocked.decision == "REQUIRE_CONFIRM"
    finally:
        ctl.close()


def test_parse_confirmation_response_defaults(tmp_path):
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    try:
        assert ctl.parse_confirmation_response("yes") == "affirm"
        assert ctl.parse_confirmation_response("yes!") == "affirm"
        assert ctl.parse_confirmation_response("no") == "deny"
        assert ctl.parse_confirmation_response("maybe later") == "unclear"
        assert ctl.parse_confirmation_response("yes please") == "unclear"
        assert ctl.parse_confirmation_response("yes and no") == "unclear"
        assert ctl.parse_confirmation_response("no but yes") == "unclear"
        assert ctl.parse_confirmation_response("deny yes") == "unclear"
    finally:
        ctl.close()


def test_parse_confirmation_response_honors_custom_tokens(tmp_path):
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db",
        config=PolicyConfig(
            mode="enforce",
            affirmative_tokens=["absolutely", "ship it"],
            negative_tokens=["decline", "skip it"],
        ),
    )
    try:
        assert ctl.parse_confirmation_response("absolutely") == "affirm"
        assert ctl.parse_confirmation_response("ship it") == "affirm"
        assert ctl.parse_confirmation_response("ship it now") == "unclear"
        assert ctl.parse_confirmation_response("decline") == "deny"
        assert ctl.parse_confirmation_response("skip it") == "deny"
        assert ctl.parse_confirmation_response("skip it later") == "unclear"
        assert ctl.parse_confirmation_response("yes") == "unclear"
    finally:
        ctl.close()
