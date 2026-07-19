from datetime import timedelta

import pytest

from openminion.base.time import utc_now
from openminion.tools.ops.policy import (
    BreakGlassGrant,
    decide_operation_policy,
)
from openminion.tools.ops.contracts import OperationTarget


@pytest.fixture
def staging_target() -> OperationTarget:
    return OperationTarget(
        target_id="staging",
        kind="local",
        environment="staging",
    )


def test_read_only_policy_allows_known_unprivileged_profiles(
    staging_target: OperationTarget,
) -> None:
    decision = decide_operation_policy(staging_target, risk="read")

    assert decision.outcome == "allow"
    assert decision.reason == "read-only observation"


def test_policy_denies_disabled_targets() -> None:
    target = OperationTarget(target_id="disabled", kind="local", enabled=False)

    decision = decide_operation_policy(target, risk="read")

    assert decision.outcome == "deny"
    assert decision.reason == "operation target is disabled"


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"profile_known": False}, "unknown operation profile"),
        ({"privileged": True}, "privileged operations are outside"),
        ({"headless": True}, "interactive approval surface"),
    ],
)
def test_write_safe_policy_fails_closed(
    staging_target: OperationTarget,
    kwargs: dict[str, bool],
    reason: str,
) -> None:
    decision = decide_operation_policy(staging_target, risk="write_safe", **kwargs)

    assert decision.outcome == "deny"
    assert reason in decision.reason


def test_write_safe_policy_requires_approval_outside_production(
    staging_target: OperationTarget,
) -> None:
    decision = decide_operation_policy(staging_target, risk="write_safe")

    assert decision.outcome == "ask"


def test_production_write_requires_current_target_scoped_breakglass() -> None:
    target = OperationTarget(
        target_id="production",
        kind="local",
        environment="production",
    )
    denied = decide_operation_policy(target, risk="write_safe")
    expired = BreakGlassGrant(
        target_id="production",
        reason="incident",
        expires_at=(utc_now() - timedelta(seconds=1)).isoformat(),
    )
    wrong_target = expired.model_copy(
        update={
            "target_id": "other",
            "expires_at": (utc_now() + timedelta(minutes=5)).isoformat(),
        }
    )
    allowed = expired.model_copy(
        update={"expires_at": (utc_now() + timedelta(minutes=5)).isoformat()}
    )

    assert denied.outcome == "deny"
    assert (
        decide_operation_policy(target, risk="write_safe", breakglass=expired).outcome
        == "deny"
    )
    assert (
        decide_operation_policy(
            target, risk="write_safe", breakglass=wrong_target
        ).outcome
        == "deny"
    )
    assert (
        decide_operation_policy(target, risk="write_safe", breakglass=allowed).outcome
        == "ask"
    )
