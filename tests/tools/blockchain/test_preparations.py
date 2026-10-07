from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from types import SimpleNamespace

import pytest

from openminion.base.config.env import EnvironmentConfig
from openminion.tools.blockchain.preparations import (
    MAX_RESOLUTION_RECORD_BYTES,
    PreparationReferenceError,
    SessionRecordError,
    claim_operation_record,
    load_operation_record,
    load_resolution_record,
    load_resolved_preparation_record,
    replace_operation_record,
    resolve_prepared_transaction,
    save_prepared_transaction,
    save_resolution_record,
    save_resolved_preparation_record,
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


def _digest(record: dict, *, excluded: set[str]) -> str:
    payload = {key: value for key, value in record.items() if key not in excluded}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _validate_record(record: dict) -> dict:
    return dict(record)


def _resolution_record(owner: str = "session-a") -> dict:
    record = {"schema_version": 1, "owner": owner, "facts": {"chain_id": 1}}
    record["resolution_digest"] = _digest(record, excluded={"resolution_digest"})
    return record


def _resolved_preparation_record() -> dict:
    record = {
        "schema_version": 1,
        "resolution_digest": _resolution_record()["resolution_digest"],
        "state": "prepared",
    }
    record["preparation_digest"] = _digest(record, excluded={"preparation_digest"})
    return record


def _operation_record() -> dict:
    record = {
        "schema_version": 1,
        "preparation_digest": _resolved_preparation_record()["preparation_digest"],
        "resolution_digest": _resolution_record()["resolution_digest"],
        "transaction_hash": "0x" + "ab" * 32,
        "submission_started": True,
        "broadcast_attempts": 1,
        "confirmation_depth": 1,
        "state": "broadcast_unknown",
    }
    record["operation_digest"] = _digest(
        record,
        excluded={"operation_digest", "state"},
    )
    return record


def _record_digester(record: dict) -> str:
    if "operation_digest" in record:
        return _digest(record, excluded={"operation_digest", "state"})
    if "preparation_digest" in record:
        return _digest(record, excluded={"preparation_digest"})
    return _digest(record, excluded={"resolution_digest"})


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
    digest = prepared["preparation_digest"].removeprefix("sha256:")
    assert (
        tmp_path
        / ".openminion"
        / "blockchain"
        / "preparations"
        / "session-a"
        / f"{digest}.json"
    ).is_file()


def test_latest_prepared_transaction_needs_no_model_selector(tmp_path) -> None:
    prepared = _prepared()
    env = _env(tmp_path)
    save_prepared_transaction(
        prepared,
        SimpleNamespace(session_id="session-a", env=env),
    )

    resolved = resolve_prepared_transaction(
        {},
        session_id="session-a",
        env=env,
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


def test_new_records_use_collision_resistant_session_roots(tmp_path) -> None:
    env = _env(tmp_path)
    first = _resolution_record("first")
    second = _resolution_record("second")
    save_resolution_record(
        first,
        SimpleNamespace(session_id="a/b", env=env),
        validator=_validate_record,
        digester=_record_digester,
    )
    save_resolution_record(
        second,
        SimpleNamespace(session_id="a-b", env=env),
        validator=_validate_record,
        digester=_record_digester,
    )

    assert (
        load_resolution_record(
            first["resolution_digest"],
            session_id="a/b",
            env=env,
            validator=_validate_record,
            digester=_record_digester,
        )
        == first
    )
    with pytest.raises(SessionRecordError, match="unavailable"):
        load_resolution_record(
            second["resolution_digest"],
            session_id="a/b",
            env=env,
            validator=_validate_record,
            digester=_record_digester,
        )
    session_roots = list(
        (tmp_path / ".openminion" / "blockchain" / "sessions").iterdir()
    )
    assert len(session_roots) == 2


def test_resolution_and_resolved_preparation_records_round_trip(tmp_path) -> None:
    env = _env(tmp_path)
    context = SimpleNamespace(session_id="session-a", env=env)
    resolution = _resolution_record()
    preparation = _resolved_preparation_record()

    save_resolution_record(
        resolution,
        context,
        validator=_validate_record,
        digester=_record_digester,
    )
    save_resolved_preparation_record(
        preparation,
        context,
        validator=_validate_record,
        digester=_record_digester,
    )

    assert (
        load_resolution_record(
            resolution["resolution_digest"],
            session_id="session-a",
            env=env,
            validator=_validate_record,
            digester=_record_digester,
        )
        == resolution
    )
    assert (
        load_resolved_preparation_record(
            preparation["preparation_digest"],
            session_id="session-a",
            env=env,
            validator=_validate_record,
            digester=_record_digester,
        )
        == preparation
    )


def test_session_record_load_rejects_tampered_content(tmp_path) -> None:
    env = _env(tmp_path)
    record = _resolution_record()
    save_resolution_record(
        record,
        SimpleNamespace(session_id="session-a", env=env),
        validator=_validate_record,
        digester=_record_digester,
    )
    path = next((tmp_path / ".openminion" / "blockchain" / "sessions").rglob("*.json"))
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["facts"]["chain_id"] = 2
    path.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(SessionRecordError, match="digest does not match"):
        load_resolution_record(
            record["resolution_digest"],
            session_id="session-a",
            env=env,
            validator=_validate_record,
            digester=_record_digester,
        )


def test_session_record_save_enforces_serialized_size_limit(tmp_path) -> None:
    record = _resolution_record()
    record["padding"] = "x" * MAX_RESOLUTION_RECORD_BYTES
    record["resolution_digest"] = _record_digester(record)

    with pytest.raises(SessionRecordError, match="size limit") as excinfo:
        save_resolution_record(
            record,
            SimpleNamespace(session_id="session-a", env=_env(tmp_path)),
            validator=_validate_record,
            digester=_record_digester,
        )
    assert excinfo.value.reason == "size_limit"


def test_session_record_load_enforces_serialized_size_limit(tmp_path) -> None:
    env = _env(tmp_path)
    record = _resolution_record()
    save_resolution_record(
        record,
        SimpleNamespace(session_id="session-a", env=env),
        validator=_validate_record,
        digester=_record_digester,
    )
    path = next((tmp_path / ".openminion" / "blockchain" / "sessions").rglob("*.json"))
    path.write_bytes(b"x" * (MAX_RESOLUTION_RECORD_BYTES + 1))

    with pytest.raises(SessionRecordError, match="size limit") as excinfo:
        load_resolution_record(
            record["resolution_digest"],
            session_id="session-a",
            env=env,
            validator=_validate_record,
            digester=_record_digester,
        )
    assert excinfo.value.reason == "size_limit"


def test_operation_claim_has_one_winner_and_writes_complete_record(tmp_path) -> None:
    env = _env(tmp_path)
    context = SimpleNamespace(session_id="session-a", env=env)
    record = _operation_record()

    def claim() -> bool:
        return claim_operation_record(
            record,
            context,
            validator=_validate_record,
            digester=_record_digester,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: claim(), range(8)))

    assert results.count(True) == 1
    assert results.count(False) == 7
    assert (
        load_operation_record(
            record["preparation_digest"],
            session_id="session-a",
            env=env,
            validator=_validate_record,
            digester=_record_digester,
        )
        == record
    )
    assert not list((tmp_path / ".openminion").rglob("*.tmp"))


def test_operation_record_replaces_mutable_state_atomically(tmp_path) -> None:
    env = _env(tmp_path)
    context = SimpleNamespace(session_id="session-a", env=env)
    record = _operation_record()
    assert claim_operation_record(
        record,
        context,
        validator=_validate_record,
        digester=_record_digester,
    )
    pending = {**record, "state": "pending"}

    replace_operation_record(
        pending,
        context,
        validator=_validate_record,
        digester=_record_digester,
    )

    assert (
        load_operation_record(
            record["preparation_digest"],
            session_id="session-a",
            env=env,
            validator=_validate_record,
            digester=_record_digester,
        )["state"]
        == "pending"
    )
