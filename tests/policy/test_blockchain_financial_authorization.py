from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from openminion.modules.policy.interfaces import POLICY_INTERFACE_VERSION
from openminion.modules.policy.models import (
    PolicyConfig,
    PolicyControlError,
    PolicyGrantInput,
    RiskSpec,
    stable_invocation_hash,
)
from openminion.modules.policy.runtime.service import PolicyCtl
from openminion.modules.policy.storage.migrations import MIGRATIONS
from openminion.modules.storage.migrations.module_ids import get_module_application_id
from openminion.modules.storage.migrations.runner import MigrationRunner


def _invocation(value: str = "1") -> dict:
    return {
        "tool": "blockchain",
        "method": "send_transaction",
        "args": {"transaction": {"value_wei": value}},
        "invocation_id": "invocation-1",
    }


def _context() -> dict:
    return {
        "subject_id": "operator-1",
        "trace_id": "trace-1",
        "session_id": "session-1",
    }


def _ctl(path: Path) -> PolicyCtl:
    ctl = PolicyCtl.with_sqlite(path, config=PolicyConfig(mode="enforce"))
    ctl.register_risk(
        "blockchain.send_transaction",
        RiskSpec(
            risk_class="financial",
            side_effects="external_account",
            reversibility="irreversible",
            default_confirm=True,
        ),
    )
    return ctl


def test_policy_interface_and_migration_head_are_v2() -> None:
    assert POLICY_INTERFACE_VERSION == "v2"
    assert MIGRATIONS == (
        "0001_baseline",
        "0002_blockchain_confirmations",
    )


def test_with_sqlite_upgrades_real_baseline_file(tmp_path: Path) -> None:
    db_path = tmp_path / "policy.db"
    storage_root = (
        Path(__file__).parents[2]
        / "src"
        / "openminion"
        / "modules"
        / "policy"
        / "storage"
    )
    alembic_config = Config(str(storage_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(storage_root / "migrations"))
    alembic_config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(alembic_config, "0001_baseline")
    runner = MigrationRunner(
        module_id="policy",
        db_path=db_path,
        module_application_id=get_module_application_id("policy"),
    )
    ctl = _ctl(db_path)
    ctl.close()

    state = runner.detect()
    assert state.alembic_revision == "0002_blockchain_confirmations"
    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        grant_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(policy_grants)")
        }
        decision_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(policy_decisions)")
        }
    finally:
        connection.close()
    assert "policy_pending_confirmations" in tables
    assert "approval_id" in grant_columns
    assert {"approval_id", "invocation_hash"} <= decision_columns

    command.downgrade(alembic_config, "0001_baseline")
    connection = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        grant_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(policy_grants)")
        }
    finally:
        connection.close()
    assert "policy_pending_confirmations" not in tables
    assert "approval_id" not in grant_columns


def test_exact_send_creates_reusable_server_owned_pending_confirmation(
    tmp_path: Path,
) -> None:
    ctl = _ctl(tmp_path / "policy.db")
    try:
        first = ctl.check(_invocation(), _context())
        second = ctl.check(_invocation(), _context())

        assert first.decision == "REQUIRE_CONFIRM"
        assert first.approval_id == second.approval_id
        assert first.confirm_request == {
            "approval_id": first.approval_id,
            "choices": ["allow_once", "deny"],
            "preview": {
                "tool": "blockchain",
                "method": "send_transaction",
                "args": {"transaction": {"_type": "object", "size": 1}},
            },
        }
        assert first.invocation_hash == stable_invocation_hash(
            tool="blockchain",
            method="send_transaction",
            args=_invocation()["args"],
        )
    finally:
        ctl.close()


def test_allow_once_resolution_is_idempotent_and_consumed_once(tmp_path: Path) -> None:
    ctl = _ctl(tmp_path / "policy.db")
    try:
        pending = ctl.check(_invocation(), _context())
        grant_id = ctl.resolve_confirmation(pending.approval_id or "", "allow_once")
        assert (
            ctl.resolve_confirmation(pending.approval_id or "", "allow_once")
            == grant_id
        )

        allowed = ctl.check(_invocation(), _context())
        assert allowed.decision == "ALLOW"
        assert allowed.matched_grant_id == grant_id
        assert allowed.approval_id == pending.approval_id

        criteria = {
            "subject_id": "operator-1",
            "tool": "blockchain",
            "method": "send_transaction",
            "invocation_hash": pending.invocation_hash or "",
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda _index: ctl.resolve_matching_active_grant_for_use(
                        **criteria
                    ),
                    range(2),
                )
            )
        assert sum(result is not None for result in results) == 1
    finally:
        ctl.close()


def test_deny_resolution_is_idempotent_and_opposite_action_fails(
    tmp_path: Path,
) -> None:
    ctl = _ctl(tmp_path / "policy.db")
    try:
        pending = ctl.check(_invocation(), _context())
        assert ctl.resolve_confirmation(pending.approval_id or "", "deny") is None
        assert ctl.resolve_confirmation(pending.approval_id or "", "deny") is None
        with pytest.raises(PolicyControlError) as captured:
            ctl.resolve_confirmation(pending.approval_id or "", "allow_once")
        assert captured.value.code == "PENDING_CONFIRMATION_ALREADY_RESOLVED"
    finally:
        ctl.close()


def test_generic_grant_paths_cannot_authorize_exact_send(tmp_path: Path) -> None:
    ctl = _ctl(tmp_path / "policy.db")
    try:
        grant = PolicyGrantInput(
            effect="allow",
            subject_id="operator-1",
            tool="blockchain",
            method="send_transaction",
            duration_type="once",
            invocation_hash=stable_invocation_hash(
                tool="blockchain",
                method="send_transaction",
                args=_invocation()["args"],
            ),
        )
        with pytest.raises(PolicyControlError) as direct:
            ctl.create_grant(grant)
        assert direct.value.code == "BLOCKCHAIN_SEND_GRANT_REQUIRES_CONFIRMATION"
        with pytest.raises(PolicyControlError):
            ctl.create_grant_from_confirmation(
                invocation=_invocation(),
                ctx=_context(),
                action="allow_once",
            )

        ctl._store.create_grant(grant)
        decision = ctl.check(_invocation(), _context())
        assert decision.decision == "REQUIRE_CONFIRM"
        assert (
            ctl.resolve_matching_active_grant_for_use(
                subject_id="operator-1",
                tool="blockchain",
                method="send_transaction",
                invocation_hash=grant.invocation_hash or "",
            )
            is None
        )
    finally:
        ctl.close()


def test_exact_registered_financial_risk_wins_over_low_override(tmp_path: Path) -> None:
    ctl = _ctl(tmp_path / "policy.db")
    try:
        decision = ctl.check(
            _invocation(),
            _context(),
            risk_override=RiskSpec(risk_class="read"),
        )
        assert decision.risk.risk_class == "financial"
        assert decision.decision == "REQUIRE_CONFIRM"
        row = ctl.list_decisions(limit=1)[0]
        assert row["approval_id"] == decision.approval_id
        assert row["invocation_hash"] == decision.invocation_hash
    finally:
        ctl.close()


@pytest.mark.parametrize("mode", ["disabled", "log_only"])
def test_non_enforcing_modes_deny_exact_send(mode: str, tmp_path: Path) -> None:
    ctl = PolicyCtl.with_sqlite(tmp_path / f"{mode}.db", config=PolicyConfig(mode=mode))
    try:
        decision = ctl.check(_invocation(), _context())
        assert decision.decision == "DENY"
        assert decision.reason_code == "POLICY_MODE_UNSUPPORTED"
        assert decision.details == {"mode": mode}
    finally:
        ctl.close()
