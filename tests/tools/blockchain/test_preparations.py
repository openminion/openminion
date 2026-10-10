from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.tools.blockchain import preparations
from openminion.base.config.env import EnvironmentConfig
from openminion.tools.blockchain.preparations import (
    MAX_RESOLUTION_RECORD_BYTES,
    PreparationReferenceError,
    SessionRecordError,
    claim_operation_record,
    load_operation_record,
    load_resolution_record,
    load_resolved_preparation_record,
    purge_blockchain_session_records,
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


def _prepared_with_nonce(nonce: str) -> dict:
    prepared = _prepared()
    transaction = {**prepared["transaction"], "nonce": nonce}
    return {
        **prepared,
        "transaction": transaction,
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
    session_key = hashlib.sha256(b"session-a").hexdigest()
    assert (
        tmp_path
        / ".openminion"
        / "blockchain"
        / "sessions"
        / session_key
        / "configured_preparations"
        / f"{digest}.json"
    ).is_file()


def test_configured_preparations_use_collision_resistant_session_roots(
    tmp_path,
) -> None:
    prepared = _prepared()
    env = _env(tmp_path)
    save_prepared_transaction(
        prepared,
        SimpleNamespace(session_id="alpha/beta", env=env),
    )

    with pytest.raises(PreparationReferenceError, match="unavailable"):
        resolve_prepared_transaction(
            {"preparation_digest": prepared["preparation_digest"]},
            session_id="alpha:beta",
            env=env,
        )


def test_configured_preparation_concurrent_writes_are_complete(tmp_path) -> None:
    env = _env(tmp_path)
    context = SimpleNamespace(session_id="session-a", env=env)
    prepared = [_prepared_with_nonce(str(index)) for index in range(8)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda item: save_prepared_transaction(item, context), prepared
            )
        )

    stored = resolve_prepared_transaction({}, session_id="session-a", env=env)
    assert stored in prepared
    for item in prepared:
        assert (
            resolve_prepared_transaction(
                {"preparation_digest": item["preparation_digest"]},
                session_id="session-a",
                env=env,
            )
            == item
        )
    assert not list((tmp_path / ".openminion").rglob("*.tmp"))


def test_ambiguous_legacy_configured_preparation_is_not_read(tmp_path) -> None:
    prepared = _prepared()
    env = _env(tmp_path)
    digest = prepared["preparation_digest"].removeprefix("sha256:")
    legacy_root = (
        tmp_path / ".openminion" / "blockchain" / "preparations" / "alpha-beta"
    )
    legacy_root.mkdir(parents=True)
    (legacy_root / f"{digest}.json").write_text(
        json.dumps(prepared),
        encoding="utf-8",
    )

    with pytest.raises(PreparationReferenceError, match="unavailable"):
        resolve_prepared_transaction(
            {"preparation_digest": prepared["preparation_digest"]},
            session_id="alpha/beta",
            env=env,
        )


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


def test_latest_preparation_read_failure_does_not_use_legacy_pointer(
    tmp_path, monkeypatch
) -> None:
    prepared = _prepared()
    env = _env(tmp_path)
    save_prepared_transaction(
        prepared,
        SimpleNamespace(session_id="session-a", env=env),
    )
    original_read_text = Path.read_text

    def fail_latest_pointer(path: Path, *args, **kwargs) -> str:
        if path.name == "latest_preparation":
            raise PermissionError("denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_latest_pointer)

    with pytest.raises(PreparationReferenceError, match="unavailable"):
        resolve_prepared_transaction({}, session_id="session-a", env=env)


def test_latest_preparation_requires_session(tmp_path) -> None:
    with pytest.raises(PreparationReferenceError, match="unavailable"):
        resolve_prepared_transaction({}, session_id="", env=_env(tmp_path))


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


def test_latest_preparation_tracks_configured_and_resolved_order(
    tmp_path, monkeypatch
) -> None:
    env = _env(tmp_path)
    context = SimpleNamespace(session_id="session-a", env=env)
    configured = _prepared()
    resolved = _resolved_preparation_record()
    monkeypatch.setattr(
        preparations, "validate_resolved_preparation_record", _validate_record
    )
    monkeypatch.setattr(preparations, "resolved_preparation_digest", _record_digester)

    save_prepared_transaction(configured, context)
    save_resolved_preparation_record(
        resolved,
        context,
        validator=_validate_record,
        digester=_record_digester,
    )
    assert resolve_prepared_transaction({}, session_id="session-a", env=env) == resolved

    save_prepared_transaction(configured, context)
    assert (
        resolve_prepared_transaction({}, session_id="session-a", env=env) == configured
    )


def test_purge_removes_only_exact_hashed_session_records(tmp_path) -> None:
    env = _env(tmp_path)
    for session_id in ("session-a", "session-b"):
        save_prepared_transaction(
            _prepared(),
            SimpleNamespace(session_id=session_id, env=env),
        )
    sessions_root = tmp_path / ".openminion" / "blockchain" / "sessions"
    first_root = sessions_root / hashlib.sha256(b"session-a").hexdigest()
    second_root = sessions_root / hashlib.sha256(b"session-b").hexdigest()
    ambiguous_legacy = sessions_root / "session-a"
    ambiguous_legacy.mkdir()
    (ambiguous_legacy / "legacy.json").write_text("{}", encoding="utf-8")

    purge_blockchain_session_records("session-a", env=env)
    purge_blockchain_session_records("session-a", env=env)

    assert not first_root.exists()
    assert second_root.is_dir()
    assert ambiguous_legacy.is_dir()


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
        record,
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


def test_operation_record_rejects_stale_replacement(tmp_path) -> None:
    env = _env(tmp_path)
    context = SimpleNamespace(session_id="session-a", env=env)
    record = _operation_record()
    assert claim_operation_record(
        record,
        context,
        validator=_validate_record,
        digester=_record_digester,
    )
    succeeded = {**record, "state": "succeeded"}
    stale_pending = {**record, "state": "pending"}

    assert replace_operation_record(
        succeeded,
        record,
        context,
        validator=_validate_record,
        digester=_record_digester,
    )
    assert not replace_operation_record(
        stale_pending,
        record,
        context,
        validator=_validate_record,
        digester=_record_digester,
    )

    stored = load_operation_record(
        record["preparation_digest"],
        session_id="session-a",
        env=env,
        validator=_validate_record,
        digester=_record_digester,
    )
    assert stored["state"] == "succeeded"
