from types import SimpleNamespace

import pytest

from openminion.base.config.env import EnvironmentConfig
from openminion.tools.blockchain.preparations import (
    PreparationReferenceError,
    resolve_prepared_transaction,
    save_prepared_transaction,
)
from openminion.tools.blockchain.runtime import preparation_digest


def _prepared() -> dict:
    transaction = {
        "schema_version": "evm-transaction-v1",
        "transaction_type": "eip1559",
        "chain_id": 31337,
        "from_address": "0x" + "11" * 20,
        "to_address": "0x" + "22" * 20,
        "value_wei": "1",
        "nonce": "0",
        "gas_limit": "21000",
        "data": "0x",
        "max_fee_per_gas_wei": "2",
        "max_priority_fee_per_gas_wei": "1",
        "max_total_fee_wei": "42000",
    }
    return {
        "transaction": transaction,
        "call_context": None,
        "preparation_digest": preparation_digest(transaction, None),
    }


def _env(tmp_path) -> EnvironmentConfig:
    return EnvironmentConfig(
        values={
            "OPENMINION_HOME": str(tmp_path),
            "OPENMINION_DATA_ROOT": str(tmp_path / ".openminion"),
        }
    )


def test_prepared_transaction_survives_runtime_recreation(tmp_path) -> None:
    prepared = _prepared()
    env = _env(tmp_path)
    save_prepared_transaction(
        prepared,
        SimpleNamespace(session_id="session-a", env=env),
    )

    resolved = resolve_prepared_transaction(
        {"preparation_digest": prepared["preparation_digest"]},
        session_id="session-a",
        env=_env(tmp_path),
    )

    assert resolved == prepared


def test_prepared_transaction_is_scoped_to_session(tmp_path) -> None:
    prepared = _prepared()
    env = _env(tmp_path)
    save_prepared_transaction(
        prepared,
        SimpleNamespace(session_id="session-a", env=env),
    )

    with pytest.raises(PreparationReferenceError, match="unavailable"):
        resolve_prepared_transaction(
            {"preparation_digest": prepared["preparation_digest"]},
            session_id="session-b",
            env=env,
        )


def test_corrupted_prepared_transaction_is_rejected(tmp_path) -> None:
    prepared = _prepared()
    env = _env(tmp_path)
    context = SimpleNamespace(session_id="session-a", env=env)
    save_prepared_transaction(prepared, context)
    preparation_file = next((tmp_path / ".openminion").rglob("*.json"))
    preparation_file.write_text("not json", encoding="utf-8")

    with pytest.raises(PreparationReferenceError, match="unavailable"):
        resolve_prepared_transaction(
            {"preparation_digest": prepared["preparation_digest"]},
            session_id="session-a",
            env=env,
        )
