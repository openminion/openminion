from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from openminion.modules.policy.models import (
    PolicyConfig,
    PolicyControlError,
    PolicyGrantInput,
    RiskSpec,
    stable_invocation_hash,
)
from openminion.modules.policy.runtime.service import PolicyCtl
from openminion.modules.tool.plugin_api import PolicyAuthorization


_METHODS = ("prepare_order", "place_order", "apply_order_action")


def _invocation(method: str, *, marker: str = "one") -> dict[str, object]:
    return {
        "tool": "commerce",
        "method": method,
        "args": {"material_digest": f"sha256:{marker:0<64}"},
        "invocation_id": f"invocation-{marker}",
    }


def _context(
    *, subject_id: str | None = "local", session_id: str | None = "session-1"
) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "subject_id": subject_id,
            "session_id": session_id,
            "trace_id": "trace-1",
        }.items()
        if value is not None
    }


def _preview(method: str) -> dict[str, object]:
    return {
        "schema_version": "commerce-confirmation-preview-v1",
        "method": method,
        "merchant": "Fixture Merchant",
        "consequence": "Creates the approved commerce operation once.",
    }


def _ctl(path: Path, *, mode: str = "enforce") -> PolicyCtl:
    ctl = PolicyCtl.with_sqlite(path, config=PolicyConfig(mode=mode))
    for method in _METHODS:
        ctl.register_risk(
            f"commerce.{method}",
            RiskSpec(
                risk_class=(
                    "state_change" if method == "prepare_order" else "financial"
                ),
                side_effects="external_account",
                reversibility=(
                    "reversible" if method == "prepare_order" else "irreversible"
                ),
                default_confirm=True,
            ),
        )
    return ctl


def _check(ctl: PolicyCtl, method: str, **kwargs: object):
    return ctl.check(
        _invocation(method),
        _context(),
        confirmation_preview=_preview(method),
        **kwargs,
    )


@pytest.mark.parametrize("method", _METHODS)
def test_commerce_confirmation_is_exact_and_consumed_once(
    method: str, tmp_path: Path
) -> None:
    ctl = _ctl(tmp_path / f"{method}.db")
    try:
        pending = _check(ctl, method)
        repeated = _check(ctl, method)

        assert pending.decision == "REQUIRE_CONFIRM"
        assert repeated.approval_id == pending.approval_id
        assert pending.confirm_request == {
            "approval_id": pending.approval_id,
            "choices": ["allow_once", "deny"],
            "preview": _preview(method),
        }

        grant_id = ctl.resolve_confirmation(pending.approval_id or "", "allow_once")
        allowed = _check(ctl, method)
        assert allowed.decision == "ALLOW"
        assert allowed.matched_grant_id == grant_id

        criteria = {
            "subject_id": "local",
            "tool": "commerce",
            "method": method,
            "invocation_hash": pending.invocation_hash or "",
            "session_id": "session-1",
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            consumed = list(
                pool.map(
                    lambda _index: ctl.resolve_matching_active_grant_for_use(
                        **criteria
                    ),
                    range(2),
                )
            )

        assert sum(grant is not None for grant in consumed) == 1
        assert ctl.resolve_matching_active_grant_for_use(**criteria) is None
    finally:
        ctl.close()


def test_commerce_grant_is_bound_to_subject_session_invocation_and_pair(
    tmp_path: Path,
) -> None:
    ctl = _ctl(tmp_path / "policy.db")
    try:
        pending = _check(ctl, "place_order")
        ctl.resolve_confirmation(pending.approval_id or "", "allow_once")
        base = {
            "subject_id": "local",
            "tool": "commerce",
            "method": "place_order",
            "invocation_hash": pending.invocation_hash or "",
            "session_id": "session-1",
        }

        for changed in (
            {"subject_id": "other"},
            {"session_id": "session-2"},
            {"session_id": None},
            {"invocation_hash": "wrong"},
            {"method": "prepare_order"},
            {"tool": "other"},
        ):
            assert ctl.resolve_matching_active_grant_for_use(**(base | changed)) is None

        assert ctl.resolve_matching_active_grant_for_use(**base) is not None
    finally:
        ctl.close()


@pytest.mark.parametrize("method", _METHODS)
def test_generic_grant_paths_cannot_authorize_commerce(
    method: str, tmp_path: Path
) -> None:
    ctl = _ctl(tmp_path / f"{method}.db")
    invocation = _invocation(method)
    grant = PolicyGrantInput(
        effect="allow",
        subject_id="local",
        tool="commerce",
        method=method,
        duration_type="once",
        session_id="session-1",
        invocation_hash=stable_invocation_hash(
            tool="commerce",
            method=method,
            args=invocation["args"],  # type: ignore[arg-type]
        ),
    )
    try:
        with pytest.raises(PolicyControlError) as direct:
            ctl.create_grant(grant)
        assert direct.value.code == "EXACT_AUTHORIZATION_REQUIRES_CONFIRMATION"

        with pytest.raises(PolicyControlError) as derived:
            ctl.create_grant_from_confirmation(
                invocation=invocation,
                ctx=_context(),
                action="allow_once",
            )
        assert derived.value.code == "EXACT_AUTHORIZATION_REQUIRES_CONFIRMATION"

        ctl._store.create_grant(grant)
        decision = _check(ctl, method)
        assert decision.decision == "REQUIRE_CONFIRM"
    finally:
        ctl.close()


@pytest.mark.parametrize(
    ("mode", "decision_name", "reason_code"),
    [
        ("disabled", "REQUIRE_CONFIRM", "EXACT_AUTHORIZATION_REQUIRED"),
        ("log_only", "DENY", "POLICY_MODE_UNSUPPORTED"),
    ],
)
def test_non_enforcing_modes_keep_commerce_floor(
    mode: str, decision_name: str, reason_code: str, tmp_path: Path
) -> None:
    ctl = _ctl(tmp_path / f"{mode}.db", mode=mode)
    try:
        decision = _check(ctl, "place_order")
        assert decision.decision == decision_name
        assert decision.reason_code == reason_code
    finally:
        ctl.close()


@pytest.mark.parametrize(
    ("context", "reason_code"),
    [
        (_context(subject_id="other"), "SUBJECT_UNAVAILABLE"),
        (_context(subject_id=None), "SUBJECT_UNAVAILABLE"),
        (_context(session_id=None), "SUBJECT_UNAVAILABLE"),
    ],
)
def test_commerce_requires_trusted_local_subject_and_session(
    context: dict[str, str], reason_code: str, tmp_path: Path
) -> None:
    ctl = _ctl(tmp_path / "policy.db")
    try:
        decision = ctl.check(
            _invocation("place_order"),
            context,
            confirmation_preview=_preview("place_order"),
        )
        assert decision.decision == "DENY"
        assert decision.reason_code == reason_code
        assert (
            ctl._store._record_store.query_dicts(
                "SELECT approval_id FROM policy_pending_confirmations"
            )
            == []
        )
    finally:
        ctl.close()


def test_missing_commerce_preview_is_denied(tmp_path: Path) -> None:
    ctl = _ctl(tmp_path / "policy.db")
    try:
        decision = ctl.check(_invocation("place_order"), _context())
        assert decision.decision == "DENY"
        assert decision.reason_code == "COMMERCE_CONFIRMATION_PREVIEW_INVALID"
    finally:
        ctl.close()


def test_commerce_pending_confirmation_is_session_bound(tmp_path: Path) -> None:
    ctl = _ctl(tmp_path / "policy.db")
    invocation = _invocation("prepare_order")
    try:
        first = ctl.check(
            invocation,
            _context(session_id="session-1"),
            confirmation_preview=_preview("prepare_order"),
        )
        second = ctl.check(
            invocation,
            _context(session_id="session-2"),
            confirmation_preview=_preview("prepare_order"),
        )
        assert first.approval_id != second.approval_id
    finally:
        ctl.close()


@pytest.mark.parametrize("action", ["allow_session", "allow_forever"])
def test_commerce_confirmation_accepts_only_once_or_deny(
    action: str, tmp_path: Path
) -> None:
    ctl = _ctl(tmp_path / "policy.db")
    try:
        pending = _check(ctl, "place_order")
        with pytest.raises(ValueError, match=r"allow_once\|deny"):
            ctl.resolve_confirmation(pending.approval_id or "", action)
        assert ctl.list_grants(active_only=True) == []
    finally:
        ctl.close()


def test_expired_confirmation_and_replay_do_not_authorize(tmp_path: Path) -> None:
    ctl = _ctl(tmp_path / "policy.db")
    try:
        pending = _check(ctl, "apply_order_action")
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        ctl._store._record_store.execute_count(
            "UPDATE policy_pending_confirmations SET expires_at = ? WHERE approval_id = ?",
            (expired, pending.approval_id),
        )
        with pytest.raises(PolicyControlError) as captured:
            ctl.resolve_confirmation(pending.approval_id or "", "allow_once")
        assert captured.value.code == "PENDING_CONFIRMATION_EXPIRED"

        fresh = _check(ctl, "apply_order_action")
        ctl.resolve_confirmation(fresh.approval_id or "", "deny")
        with pytest.raises(PolicyControlError) as replay:
            ctl.resolve_confirmation(fresh.approval_id or "", "allow_once")
        assert replay.value.code == "PENDING_CONFIRMATION_ALREADY_RESOLVED"
    finally:
        ctl.close()


def test_authorization_fact_accepts_only_closed_pairs_and_bound_commerce() -> None:
    with pytest.raises(ValueError):
        PolicyAuthorization(  # type: ignore[arg-type]
            tool="commerce",
            method="inspect",
            invocation_hash="hash",
            approval_id="approval",
            grant_id="grant",
            duration_type="once",
            subject_id="local",
            session_id="session-1",
        )
    with pytest.raises(ValueError):
        PolicyAuthorization(
            tool="commerce",
            method="place_order",
            invocation_hash="hash",
            approval_id="approval",
            grant_id="grant",
            duration_type="once",
            subject_id="other",
            session_id="session-1",
        )
    with pytest.raises(ValueError):
        PolicyAuthorization(
            tool="commerce",
            method="place_order",
            invocation_hash="hash",
            approval_id="approval",
            grant_id="grant",
            duration_type="once",
            subject_id="local",
        )
    with pytest.raises(ValueError):
        PolicyAuthorization(  # type: ignore[arg-type]
            tool="commerce",
            method="place_order",
            invocation_hash="hash",
            approval_id="approval",
            grant_id="grant",
            duration_type="session",
            subject_id="local",
            session_id="session-1",
        )
