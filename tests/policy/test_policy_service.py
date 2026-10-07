from __future__ import annotations

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import os
import time
from types import SimpleNamespace

import pytest

from openminion.base.config.base import ConfigError
from openminion.base.config.parser.action import _build_action_policy_config

from openminion.modules.policy.models import (
    PolicyConfig,
    PolicyControlError,
    PolicyGrantInput,
    stable_invocation_hash,
)
from openminion.modules.policy.runtime.service import PolicyCtl


_OPPC_TAG = "created_from_confirmation:allow_session_exact"


def _invocation(path: str = "/tmp/demo.txt") -> dict:
    return {"tool": "fs", "method": "rm", "args": {"path": path}}


def _ctx() -> dict:
    return {
        "trace_id": "trace-1",
        "session_id": "sess-1",
        "agent_id": "agent-1",
        "subject_id": "local",
    }


def _oppc_policy_db() -> str:
    path = os.environ.get("OPENMINION_OPPC_POLICY_DB", "").strip()
    if not path:
        pytest.skip("OPENMINION_OPPC_POLICY_DB is required for rollback proof")
    return path


def test_action_policy_rules_round_trip_exact_tool_contract() -> None:
    config = _build_action_policy_config(
        {
            "rules": [
                {
                    "match": {"tool_name": "file.write", "min_risk_class": "write"},
                    "mode": "ask",
                }
            ]
        }
    )
    assert config.rules[0].match.tool_name == "file.write"
    assert config.rules[0].match.min_risk_class == "write"


def test_action_policy_category_rule_fails_with_migration_guidance() -> None:
    with pytest.raises(ConfigError, match="exact.*tool_name"):
        _build_action_policy_config(
            {"rules": [{"match": {"tool_category": "filesystem"}, "mode": "ask"}]}
        )


@pytest.mark.parametrize("rules", [None, "bad", {}])
def test_explicit_invalid_action_policy_rules_fail(rules: object) -> None:
    with pytest.raises(ConfigError, match="must be a list"):
        _build_action_policy_config({"rules": rules})


def test_action_policy_rules_may_be_omitted() -> None:
    assert _build_action_policy_config({}).rules == []


def test_serialized_empty_action_policy_rules_remain_compatible() -> None:
    assert _build_action_policy_config({"rules": []}).rules == []


def test_action_policy_wildcard_rule_fails_as_non_exact() -> None:
    with pytest.raises(ConfigError, match="must be exact"):
        _build_action_policy_config(
            {"rules": [{"match": {"tool_name": "file.*"}, "mode": "block"}]}
        )


def _ops_invocation(plan_id: str = "plan-1", plan_hash: str = "a" * 64) -> dict:
    return {
        "tool": "ops.command",
        "method": "run",
        "args": {"plan_id": plan_id, "plan_hash": plan_hash},
        "invocation_id": plan_id,
    }


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


def test_historical_session_grant_without_session_id_never_matches(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    try:
        ctl._store.create_grant(
            PolicyGrantInput(
                effect="allow",
                tool="fs",
                method="rm",
                duration_type="session",
            )
        )

        decision = ctl.check(_invocation("/tmp/legacy.txt"), _ctx())

        assert decision.decision == "REQUIRE_CONFIRM"
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


def test_ops_command_requires_exact_pending_once_grant(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    invocation = _ops_invocation()
    try:
        pending = ctl.check(invocation, _ctx())
        assert pending.decision == "REQUIRE_CONFIRM"
        assert pending.approval_id
        assert pending.confirm_request == {
            "approval_id": pending.approval_id,
            "choices": ["allow_once", "deny"],
            "preview": {
                "plan_id": "plan-1",
                "plan_hash": "a" * 64,
                "target_id": "",
                "target_revision": None,
                "argv": [],
                "cwd": "",
                "timeout_seconds": None,
                "expires_at": "",
            },
        }

        ctl.resolve_confirmation(pending.approval_id, "allow_once")
        allowed = ctl.check(invocation, _ctx())
        repeated = ctl.check(invocation, _ctx())

        assert allowed.decision == "ALLOW"
        assert allowed.reason_code == "EXACT_PENDING_ALLOW"
        assert allowed.approval_id == pending.approval_id
        assert allowed.matched_grant_id
        assert repeated.decision == "REQUIRE_CONFIRM"
        assert repeated.approval_id != pending.approval_id
    finally:
        ctl.close()


def test_ops_command_rejects_broad_and_wrong_session_grants(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    invocation = _ops_invocation()
    invocation_hash = stable_invocation_hash(
        tool="ops.command", method="run", args=invocation["args"]
    )
    try:
        with pytest.raises(ValueError, match="allow once"):
            ctl.create_grant(
                PolicyGrantInput(
                    effect="allow",
                    tool="ops.command",
                    method="run",
                    duration_type="forever",
                )
            )
        assert ctl.check(invocation, _ctx()).decision == "REQUIRE_CONFIRM"

        pending = ctl.check(invocation, _ctx())
        assert pending.approval_id
        ctl.resolve_confirmation(pending.approval_id, "allow_once")
        wrong_session = {**_ctx(), "session_id": "sess-2"}
        assert ctl.check(invocation, wrong_session).decision == "REQUIRE_CONFIRM"

        grants = ctl.list_grants(tool="ops.command", method="run", active_only=True)
        exact = next(
            grant for grant in grants if grant.invocation_hash == invocation_hash
        )
        assert exact.uses_count == 0
    finally:
        ctl.close()


@pytest.mark.parametrize(
    ("tool", "method"),
    [
        ("financial", "transfer"),
        ("commerce", "place_order"),
        ("blockchain", "send_transaction"),
        ("ops.command", "run"),
        ("project", "start"),
    ],
)
def test_reusable_wildcard_allow_never_bypasses_one_time_family(
    tmp_path, tool: str, method: str
) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / f"{tool.replace('.', '-')}.db",
        config=PolicyConfig(mode="enforce", default_action="require_confirm"),
    )
    try:
        grant_id = ctl.create_grant(
            PolicyGrantInput(
                effect="allow",
                tool="*",
                method="*",
                duration_type="forever",
            )
        )
        decision = ctl.check(
            {"tool": tool, "method": method, "args": {}},
            _ctx(),
        )
        assert decision.decision != "ALLOW"
        grant = next(item for item in ctl.list_grants() if item.grant_id == grant_id)
        assert grant.uses_count == 0
    finally:
        ctl.close()


@pytest.mark.parametrize("duration_type", ["until", "session", "forever"])
@pytest.mark.parametrize(
    ("tool", "method"),
    [
        ("financial", "transfer"),
        ("commerce", "prepare_order"),
        ("blockchain", "send_transaction"),
        ("ops.command", "run"),
        ("project", "start"),
    ],
)
def test_one_time_families_reject_direct_reusable_grants(
    tmp_path, tool: str, method: str, duration_type: str
) -> None:
    ctl = PolicyCtl.with_sqlite(tmp_path / f"direct-{tool}-{duration_type}.db")
    try:
        with pytest.raises((ValueError, PolicyControlError)) as caught:
            ctl.create_grant(
                PolicyGrantInput(
                    effect="allow",
                    tool=tool,
                    method=method,
                    duration_type=duration_type,
                    expires_at=(
                        (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
                        if duration_type == "until"
                        else None
                    ),
                    session_id="sess-1" if duration_type == "session" else None,
                )
            )
        assert (
            "once" in str(caught.value).lower()
            or "confirmation" in str(caught.value).lower()
        )
    finally:
        ctl.close()


def test_public_grant_resolution_binds_session(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    invocation = _ops_invocation()
    invocation_hash = stable_invocation_hash(
        tool="ops.command", method="run", args=invocation["args"]
    )
    try:
        pending = ctl.check(invocation, _ctx())
        assert pending.approval_id
        ctl.resolve_confirmation(pending.approval_id, "allow_once")

        assert (
            ctl.resolve_matching_active_grant_for_use(
                subject_id="local",
                tool="ops.command",
                method="run",
                invocation_hash=invocation_hash,
                session_id="sess-2",
            )
            is None
        )
    finally:
        ctl.close()


@pytest.mark.parametrize(
    ("tool", "method"),
    [
        ("financial", "transfer"),
        ("commerce", "prepare_order"),
        ("blockchain", "send_transaction"),
        ("ops.command", "run"),
        ("project", "start"),
    ],
)
@pytest.mark.parametrize(
    ("action", "until_seconds"),
    [
        ("allow_until", 600),
        ("allow_session_exact", None),
        ("allow_forever", None),
    ],
)
def test_one_time_families_reject_reusable_confirmation_grants(
    tmp_path,
    tool: str,
    method: str,
    action: str,
    until_seconds: int | None,
) -> None:
    ctl = PolicyCtl.with_sqlite(tmp_path / f"{tool}-{action}.db")
    try:
        with pytest.raises((ValueError, PolicyControlError)) as caught:
            ctl.create_grant_from_confirmation(
                invocation={"tool": tool, "method": method, "args": {}},
                ctx={"session_id": "sess-1"},
                action=action,
                until_seconds=until_seconds,
            )
        assert (
            "once" in str(caught.value).lower()
            or "confirmation" in str(caught.value).lower()
        )
    finally:
        ctl.close()


def test_ops_command_keeps_exact_floor_in_disabled_mode(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "disabled.db", config=PolicyConfig(mode="disabled")
    )
    invocation = _ops_invocation()
    try:
        prompt = ctl.check(invocation, _ctx())
        assert prompt.decision == "REQUIRE_CONFIRM"
        assert prompt.approval_id
        ctl.resolve_confirmation(prompt.approval_id, "allow_once")
        assert ctl.check(invocation, _ctx()).decision == "ALLOW"
        assert ctl.check(invocation, _ctx()).decision == "REQUIRE_CONFIRM"
    finally:
        ctl.close()


def test_ops_command_rejects_log_only_mode(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "log-only.db", config=PolicyConfig(mode="log_only")
    )
    try:
        decision = ctl.check(_ops_invocation(), _ctx())
        assert decision.decision == "DENY"
        assert decision.reason_code == "POLICY_MODE_UNSUPPORTED"
    finally:
        ctl.close()


def test_exact_tool_block_rule_applies_in_disabled_mode(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db",
        config=PolicyConfig(
            mode="disabled",
            rules=(
                SimpleNamespace(tool_name="fs.rm", min_risk_class="", mode="block"),
            ),
        ),
    )
    try:
        decision = ctl.check(_invocation(), _ctx())
        assert decision.decision == "DENY"
        assert decision.reason_code == "CONFIG_RULE_BLOCK"
    finally:
        ctl.close()


def test_rule_precedence_is_independent_of_config_order(tmp_path) -> None:
    rules = (
        SimpleNamespace(tool_name="fs.rm", min_risk_class="", mode="auto"),
        SimpleNamespace(tool_name="fs.rm", min_risk_class="write", mode="ask"),
        SimpleNamespace(tool_name="fs.rm", min_risk_class="", mode="block"),
    )
    for index, configured in enumerate((rules, tuple(reversed(rules)))):
        ctl = PolicyCtl.with_sqlite(
            tmp_path / f"policy-{index}.db",
            config=PolicyConfig(mode="enforce", rules=configured),
        )
        try:
            decision = ctl.check(_invocation(), _ctx())
            assert decision.decision == "DENY"
            assert decision.reason_code == "CONFIG_RULE_BLOCK"
        finally:
            ctl.close()


def test_auto_rule_waives_policy_prompt_only(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db",
        config=PolicyConfig(
            mode="enforce",
            rules=(
                SimpleNamespace(tool_name="fs.rm", min_risk_class="write", mode="auto"),
            ),
        ),
    )
    try:
        decision = ctl.check(_invocation(), _ctx())
        assert decision.decision == "ALLOW"
        assert decision.reason_code == "CONFIG_RULE_AUTO"
    finally:
        ctl.close()


@pytest.mark.parametrize(
    ("mode", "decision", "reason_code"),
    [
        ("block", "DENY", "CONFIG_RULE_BLOCK"),
        ("ask", "REQUIRE_CONFIRM", "CONFIG_RULE_ASK"),
        ("auto", "ALLOW", "CONFIG_RULE_AUTO"),
    ],
)
def test_exact_rules_match_canonical_tools_without_a_method_suffix(
    tmp_path, mode: str, decision: str, reason_code: str
) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / f"weather-{mode}.db",
        config=PolicyConfig(
            mode="enforce",
            rules=(SimpleNamespace(tool_name="weather", min_risk_class="", mode=mode),),
        ),
    )
    try:
        result = ctl.check(
            {"tool": "weather", "method": "default", "args": {}},
            _ctx(),
        )
        assert result.decision == decision
        assert result.reason_code == reason_code
    finally:
        ctl.close()


def test_auto_rule_does_not_bypass_commerce_authorization(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db",
        config=PolicyConfig(
            mode="enforce",
            rules=(
                SimpleNamespace(
                    tool_name="commerce.prepare_order",
                    min_risk_class="",
                    mode="auto",
                ),
            ),
        ),
    )
    try:
        decision = ctl.check(
            {"tool": "commerce", "method": "prepare_order", "args": {}},
            _ctx(),
            confirmation_preview={},
        )
        assert decision.decision == "REQUIRE_CONFIRM"
        assert decision.reason_code == "EXACT_AUTHORIZATION_REQUIRED"
        assert decision.approval_id
    finally:
        ctl.close()


def test_disabled_one_use_grant_has_one_concurrent_winner(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db",
        config=PolicyConfig(
            mode="disabled",
            rules=(SimpleNamespace(tool_name="fs.rm", min_risk_class="", mode="ask"),),
        ),
    )
    invocation = _invocation("/tmp/concurrent.txt")
    invocation_hash = stable_invocation_hash(
        tool="fs", method="rm", args=invocation["args"]
    )
    try:
        ctl.create_grant(
            PolicyGrantInput(
                effect="allow",
                tool="fs",
                method="rm",
                duration_type="once",
                invocation_hash=invocation_hash,
            )
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            decisions = list(
                pool.map(lambda _: ctl.check(invocation, _ctx()), range(2))
            )

        assert sorted(decision.decision for decision in decisions) == [
            "ALLOW",
            "REQUIRE_CONFIRM",
        ]
        assert ctl.check(invocation, _ctx()).decision == "REQUIRE_CONFIRM"
    finally:
        ctl.close()


def test_exact_session_grant_binds_hash_and_session(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    invocation = _invocation("/tmp/session.txt")
    try:
        grant_id = ctl.create_grant_from_confirmation(
            invocation=invocation,
            ctx=_ctx(),
            action="allow_session_exact",
        )
        grant = next(row for row in ctl.list_grants() if row.grant_id == grant_id)
        assert grant.session_id == "sess-1"
        assert grant.invocation_hash == stable_invocation_hash(
            tool="fs", method="rm", args=invocation["args"]
        )
        assert grant.reason == "created_from_confirmation:allow_session_exact"
        assert ctl.check(invocation, _ctx()).decision == "ALLOW"
        assert ctl.check(invocation, {**_ctx(), "session_id": "sess-2"}).decision == (
            "REQUIRE_CONFIRM"
        )
        assert ctl.check(_invocation("/tmp/changed.txt"), _ctx()).decision == (
            "REQUIRE_CONFIRM"
        )
    finally:
        ctl.close()


def test_any_grant_with_session_id_stays_in_that_session(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(tmp_path / "policy.db")
    try:
        ctl.create_grant(
            PolicyGrantInput(
                effect="allow",
                tool="fs",
                method="rm",
                duration_type="forever",
                session_id="sess-1",
            )
        )
        assert ctl.check(_invocation(), _ctx()).decision == "ALLOW"
        other = ctl.check(_invocation(), {**_ctx(), "session_id": "sess-2"})
        assert other.decision == "REQUIRE_CONFIRM"
    finally:
        ctl.close()


def test_grant_rejects_unknown_duration(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(tmp_path / "policy.db")
    try:
        with pytest.raises(ValueError, match="duration_type"):
            ctl.create_grant(
                PolicyGrantInput(
                    effect="allow",
                    duration_type="bogus",  # type: ignore[arg-type]
                )
            )
    finally:
        ctl.close()


@pytest.mark.parametrize("expires_at", ["zzzz", "2026-10-07T12:00:00"])
def test_until_grant_requires_timezone_aware_iso_expiry(
    expires_at: str, tmp_path
) -> None:
    ctl = PolicyCtl.with_sqlite(tmp_path / "policy.db")
    try:
        with pytest.raises(ValueError, match="expires_at"):
            ctl.create_grant(
                PolicyGrantInput(
                    effect="allow",
                    duration_type="until",
                    expires_at=expires_at,
                )
            )
    finally:
        ctl.close()


def test_until_grant_normalizes_offset_before_expiry_checks(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(tmp_path / "policy.db")
    expired = datetime.now(timezone.utc) - timedelta(minutes=1)
    offset_expired = expired.astimezone(timezone(timedelta(hours=14))).isoformat()
    try:
        ctl.create_grant(
            PolicyGrantInput(
                effect="allow",
                duration_type="until",
                expires_at=offset_expired,
            )
        )
        assert ctl.list_grants(active_only=True) == []
    finally:
        ctl.close()


def test_session_grant_requires_session_id(tmp_path) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    try:
        try:
            ctl.create_grant_from_confirmation(
                invocation=_invocation(),
                ctx={**_ctx(), "session_id": ""},
                action="allow_session_exact",
            )
        except ValueError as exc:
            assert "session_id" in str(exc)
        else:
            raise AssertionError("expected missing session_id to fail")
    finally:
        ctl.close()


def test_oppc_rollback_fixture_writes_tagged_grant() -> None:
    ctl = PolicyCtl.with_sqlite(
        _oppc_policy_db(),
        config=PolicyConfig(mode="enforce", default_action="require_confirm"),
    )
    try:
        for method, path in (("write", "one.txt"), ("edit", "two.txt")):
            ctl.create_grant_from_confirmation(
                invocation={
                    "tool": "file",
                    "method": method,
                    "args": {"path": path},
                },
                ctx=_ctx(),
                action="allow_session_exact",
            )
        tagged = [grant for grant in ctl.list_grants() if grant.reason == _OPPC_TAG]
        assert len(tagged) >= 2
    finally:
        ctl.close()


def test_oppc_legacy_matcher_does_not_broaden_revoked_exact_session_grant() -> None:
    ctl = PolicyCtl.with_sqlite(
        _oppc_policy_db(),
        config=PolicyConfig(mode="enforce", default_action="require_confirm"),
    )
    try:
        assert not [
            grant
            for grant in ctl.list_grants(active_only=True)
            if grant.reason == _OPPC_TAG
        ]
        decision = ctl.check(
            {"tool": "file", "method": "write", "args": {"path": "one.txt"}},
            _ctx(),
        )
        assert decision.decision == "REQUIRE_CONFIRM"
        assert decision.matched_grant_id is None
    finally:
        ctl.close()
