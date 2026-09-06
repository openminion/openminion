from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from web3 import Web3

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
from openminion.tools.blockchain.confirmation import (
    BlockchainConfirmationPreviewError,
    build_blockchain_send_confirmation_preview,
)
from openminion.tools.blockchain.abi import abi_signature, encode_function_call
from openminion.tools.blockchain.schema_types import FunctionAbi
from openminion.tools.blockchain.runtime import preparation_digest


def _invocation(value: str = "1") -> dict:
    transaction = {
        "schema_version": "evm-transaction-v1",
        "transaction_type": "eip1559",
        "chain_id": 31337,
        "from_address": "0x" + "11" * 20,
        "to_address": "0x" + "22" * 20,
        "value_wei": value,
        "nonce": "0",
        "gas_limit": "21000",
        "data": "0x",
        "max_fee_per_gas_wei": "2",
        "max_priority_fee_per_gas_wei": "1",
        "max_total_fee_wei": "42000",
    }
    return {
        "tool": "blockchain",
        "method": "send_transaction",
        "args": {
            "transaction": transaction,
            "call_context": None,
            "preparation_digest": preparation_digest(transaction, None),
        },
        "invocation_id": "invocation-1",
    }


def _check(ctl: PolicyCtl, value: str = "1", **kwargs):
    invocation = _invocation(value)
    return ctl.check(
        invocation,
        _context(),
        confirmation_preview=build_blockchain_send_confirmation_preview(
            invocation["args"]
        ),
        **kwargs,
    )


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


def test_policy_interface_and_migration_head_are_v3() -> None:
    assert POLICY_INTERFACE_VERSION == "v3"
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
        first = _check(ctl)
        second = _check(ctl)

        assert first.decision == "REQUIRE_CONFIRM"
        assert first.approval_id == second.approval_id
        assert first.confirm_request == {
            "approval_id": first.approval_id,
            "choices": ["allow_once", "deny"],
            "preview": first.confirmation_preview.__dict__,
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
        pending = _check(ctl)
        grant_id = ctl.resolve_confirmation(pending.approval_id or "", "allow_once")
        assert (
            ctl.resolve_confirmation(pending.approval_id or "", "allow_once")
            == grant_id
        )

        allowed = _check(ctl)
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
        pending = _check(ctl)
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
        decision = _check(ctl)
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
        decision = _check(
            ctl,
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
        decision = _check(ctl)
        assert decision.decision == "DENY"
        assert decision.reason_code == "POLICY_MODE_UNSUPPORTED"
        assert decision.details == {"mode": mode}
    finally:
        ctl.close()


def test_exact_send_without_verified_preview_is_denied(tmp_path: Path) -> None:
    ctl = _ctl(tmp_path / "policy.db")
    try:
        decision = ctl.check(_invocation(), _context())

        assert decision.decision == "DENY"
        assert decision.reason_code == "BLOCKCHAIN_CONFIRMATION_PREVIEW_INVALID"
        assert decision.details == {"reason": "request_schema"}
        assert (
            ctl._store._record_store.query_dicts(
                "SELECT approval_id FROM policy_pending_confirmations"
            )
            == []
        )
    finally:
        ctl.close()


def test_preview_builder_rejects_digest_and_calldata_limit() -> None:
    args = _invocation()["args"]
    invalid_digest = {**args, "preparation_digest": "sha256:" + "0" * 64}
    with pytest.raises(BlockchainConfirmationPreviewError) as digest_error:
        build_blockchain_send_confirmation_preview(invalid_digest)
    assert digest_error.value.reason == "preparation_digest"

    oversized_transaction = {
        **args["transaction"],
        "data": "0x" + "11" * 4097,
    }
    oversized = {
        "transaction": oversized_transaction,
        "call_context": None,
        "preparation_digest": preparation_digest(oversized_transaction, None),
    }
    with pytest.raises(BlockchainConfirmationPreviewError) as size_error:
        build_blockchain_send_confirmation_preview(oversized)
    assert size_error.value.reason == "calldata_limit"


def test_preview_builder_rejects_serialized_preview_limit() -> None:
    args = _invocation()["args"]
    function = FunctionAbi.model_validate(
        {
            "type": "function",
            "name": "note",
            "inputs": [{"name": "value", "type": "string"}],
            "outputs": [],
            "stateMutability": "nonpayable",
        }
    )
    function_args = ["\x01" * 3000]
    transaction = {
        **args["transaction"],
        "data": encode_function_call(Web3(), function, function_args),
    }
    call_context = {
        "function_abi": function.model_dump(mode="json"),
        "function_args": function_args,
        "function_signature": abi_signature(function),
    }

    with pytest.raises(BlockchainConfirmationPreviewError) as captured:
        build_blockchain_send_confirmation_preview(
            {
                "transaction": transaction,
                "call_context": call_context,
                "preparation_digest": preparation_digest(transaction, call_context),
            }
        )

    assert captured.value.reason == "preview_limit"


def test_opaque_calldata_preview_contains_complete_payload() -> None:
    args = _invocation()["args"]
    transaction = {**args["transaction"], "data": "0x1234"}
    preview = build_blockchain_send_confirmation_preview(
        {
            "transaction": transaction,
            "call_context": None,
            "preparation_digest": preparation_digest(transaction, None),
        }
    )

    assert preview.calldata_bytes == "2"
    assert preview.calldata_hex == "0x1234"
    assert preview.call is None
    assert preview.opaque_calldata is True


def test_legacy_pending_preview_cannot_mint_grant(tmp_path: Path) -> None:
    ctl = _ctl(tmp_path / "policy.db")
    try:
        pending = _check(ctl)
        ctl._store._record_store.execute_count(
            "UPDATE policy_pending_confirmations SET preview_json = '{}' "
            "WHERE approval_id = ?",
            (pending.approval_id,),
        )

        with pytest.raises(PolicyControlError) as captured:
            ctl.resolve_confirmation(pending.approval_id or "", "allow_once")

        assert captured.value.code == "BLOCKCHAIN_CONFIRMATION_PREVIEW_INVALID"
        assert ctl.list_grants(active_only=True) == []
        stored = ctl._store._record_store.query_dicts(
            "SELECT state, resolution_action, resolved_at "
            "FROM policy_pending_confirmations WHERE approval_id = ?",
            (pending.approval_id,),
        )[0]
        assert stored["state"] == "denied"
        assert stored["resolution_action"] == "deny"
        assert stored["resolved_at"]
    finally:
        ctl.close()


def test_non_checksum_stored_preview_cannot_mint_grant(tmp_path: Path) -> None:
    ctl = _ctl(tmp_path / "policy.db")
    try:
        invocation = _invocation()
        transaction = {
            **invocation["args"]["transaction"],
            "to_address": "0x" + "ab" * 20,
        }
        invocation["args"] = {
            "transaction": transaction,
            "call_context": None,
            "preparation_digest": preparation_digest(transaction, None),
        }
        preview = build_blockchain_send_confirmation_preview(invocation["args"])
        pending = ctl.check(
            invocation,
            _context(),
            confirmation_preview=preview,
        )
        stored = asdict(preview)
        stored["to_address"] = stored["to_address"].lower()
        ctl._store._record_store.execute_count(
            "UPDATE policy_pending_confirmations SET preview_json = ? "
            "WHERE approval_id = ?",
            (json.dumps(stored), pending.approval_id),
        )

        with pytest.raises(PolicyControlError) as captured:
            ctl.resolve_confirmation(pending.approval_id or "", "allow_once")

        assert captured.value.code == "BLOCKCHAIN_CONFIRMATION_PREVIEW_INVALID"
        assert ctl.list_grants(active_only=True) == []
    finally:
        ctl.close()


def test_legacy_grant_preview_is_revoked_before_consumption(tmp_path: Path) -> None:
    ctl = _ctl(tmp_path / "policy.db")
    try:
        pending = _check(ctl)
        grant_id = ctl.resolve_confirmation(pending.approval_id or "", "allow_once")
        ctl._store._record_store.execute_count(
            "UPDATE policy_pending_confirmations SET preview_json = '{}' "
            "WHERE approval_id = ?",
            (pending.approval_id,),
        )

        with pytest.raises(PolicyControlError) as captured:
            ctl.resolve_matching_active_grant_for_use(
                subject_id="operator-1",
                tool="blockchain",
                method="send_transaction",
                invocation_hash=pending.invocation_hash or "",
            )

        assert captured.value.code == "BLOCKCHAIN_CONFIRMATION_PREVIEW_INVALID"
        assert ctl._store.get_grant(grant_id or "").revoked_at is not None
    finally:
        ctl.close()
