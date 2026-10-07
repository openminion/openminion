from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from openminion.modules.storage.migrations.module_ids import get_module_application_id
from openminion.modules.storage.migrations.runner import MigrationRunner
from openminion.tools.commerce.identity import commerce_digest
from openminion.tools.commerce.provider import (
    CommerceOutcomeUnknown,
    CommerceProviderError,
    PlaceOrderRequest,
)
from openminion.tools.commerce.storage import SQLiteCommerceOrderStore
from openminion.modules.tool.plugin_api import stable_invocation_hash
from openminion.tools.commerce.authorization import canonical_commerce_args
from tests.commerce.test_apply_order_action import (
    _adapter,
    _GrantPolicy,
    _prepared_action,
)
from tests.commerce.test_audit_redaction import _Telemetry
from tests.helpers.commerce_runtime import build_fixture_commerce_runtime


@pytest.fixture
def transaction(tmp_path):
    path = tmp_path / "commerce.db"
    runtime, provider = build_fixture_commerce_runtime(store_path=path)
    preparation = runtime.prepare_public(
        {"items": [{"offer_id": "offer-1", "variant_id": "standard", "quantity": 1}]}
    )
    request = PlaceOrderRequest(
        idempotency_key="placement-1",
        preparation_ref=preparation.preparation_ref,
        preparation_digest=preparation.preparation_digest,
    )
    try:
        yield runtime, provider, path, preparation, request
    finally:
        runtime.order_store.close()


def _reopen(runtime, provider, path):
    runtime.order_store.close()
    restarted, _ = build_fixture_commerce_runtime(store_path=path)
    restarted.provider = provider
    return restarted


def _attempt(runtime, request):
    return runtime.order_store.get_attempt_by_idempotency(
        subject_id="local", kind="placement", idempotency_key=request.idempotency_key
    )


@pytest.mark.parametrize("field", ["idempotency_key", "preparation_ref"])
@pytest.mark.parametrize("source", ["direct", "response_loss", "reopened"])
def test_mismatched_placement_never_persists_success(
    transaction, monkeypatch, field, source
) -> None:
    runtime, provider, path, _, request = transaction
    if source == "reopened":
        reservation = runtime.order_store.reserve_placement(
            subject_id="local",
            preparation_id=request.preparation_ref,
            idempotency_key=request.idempotency_key,
            request_digest=commerce_digest(request.model_dump(mode="json")),
        )
        runtime.order_store.begin_placement_attempt(
            subject_id="local", attempt_id=reservation.attempt.attempt_id
        )
        provider.place_order(request)
        runtime = _reopen(runtime, provider, path)
    elif source == "response_loss":
        provider.drop_next_response("place_order")
    method = "place_order" if source == "direct" else "recover_placement"
    original = getattr(provider, method)

    def mismatched(value):
        return original(value).model_copy(update={field: "unrelated-transaction"})

    monkeypatch.setattr(provider, method, mismatched)
    try:
        with pytest.raises(CommerceOutcomeUnknown) as error:
            runtime.place_order(request)
        assert error.value.code == "OUTCOME_UNKNOWN"
        assert runtime.order_store.get_order("local", "order-1") is None
        attempt = _attempt(runtime, request)
        assert attempt.state == "outcome_unknown"
        assert attempt.active and attempt.attempt_count == 1
        assert [entry.operation for entry in provider.ledger].count("place_order") == 1
    finally:
        if source == "reopened":
            runtime.order_store.close()


@pytest.mark.parametrize("source", ["direct", "recovered"])
@pytest.mark.parametrize(
    "method,field",
    [
        ("place_order", "idempotency_key"),
        ("place_order", "preparation_ref"),
        ("apply_order_action", "idempotency_key"),
        ("apply_order_action", "action_ref"),
        ("apply_order_action", "order_ref"),
    ],
)
def test_mismatched_tool_result_reports_unknown_then_recovers_once(
    transaction, tmp_path, monkeypatch, source, method, field
):
    runtime, provider, _, preparation, _ = transaction
    if method == "place_order":
        args = {
            "preparation_ref": preparation.preparation_ref,
            "preparation": preparation.model_dump(mode="json"),
            "preparation_digest": preparation.preparation_digest,
        }
        operation, recovery, kind = "place_order", "recover_placement", "placement"
    else:
        runtime, provider, args = _prepared_action(tmp_path / "action", kind="cancel")
        operation, recovery, kind = "apply_action", "recover_action", "action"
    invocation_hash = stable_invocation_hash(
        tool="commerce", method=method, args=canonical_commerce_args(args)
    )
    adapter = _adapter(
        tmp_path, runtime, _GrantPolicy(invocation_hash, invocation_hash)
    )
    telemetry = _Telemetry()
    adapter.telemetryctl = telemetry
    command = {
        "tool_name": f"commerce.{method}",
        "args": args,
        "inputs": {
            "confirmation_source": "policy_replay",
            "confirmation_grant_id": "grant-1",
        },
    }
    callback = operation if source == "direct" else recovery
    original = getattr(provider, callback)

    def mismatched(value):
        return original(value).model_copy(update={field: "unrelated-transaction"})

    try:
        with monkeypatch.context() as patch:
            patch.setattr(provider, callback, mismatched)
            if source == "recovered":
                provider.drop_next_response(operation)
            unknown = adapter.execute(
                command=command, session_id="session-1", trace_id="mismatch"
            )
        assert "error" not in unknown, unknown
        assert unknown["outputs"]["state"] == "outcome_unknown"
        assert unknown["outputs"]["data"]["recovery_required"] is True
        assert "do not submit it again" in unknown["content"]
        events = [
            payload
            for event, payload in telemetry.events
            if event == "tool.commerce.action"
        ]
        assert [event["outcome"] for event in events] == ["outcome_unknown"]
        entry = next(item for item in provider.ledger if item.operation == operation)
        attempt = runtime.order_store.get_attempt_by_idempotency(
            subject_id="local", kind=kind, idempotency_key=entry.idempotency_key
        )
        assert attempt.state == "outcome_unknown" and attempt.active
        assert (
            attempt.attempt_count == 1 and attempt.authorization_hash == invocation_hash
        )
        if kind == "placement":
            assert runtime.order_store.get_order("local", entry.accepted_ref) is None
        command["inputs"]["confirmation_grant_id"] = "grant-2"
        recovered = adapter.execute(
            command=command, session_id="session-1", trace_id="recovered"
        )
        assert "error" not in recovered, recovered
        assert recovered["outputs"]["state"] == (
            "succeeded" if kind == "placement" else "completed"
        )
        finished = runtime.order_store.get_attempt_by_idempotency(
            subject_id="local", kind=kind, idempotency_key=entry.idempotency_key
        )
        assert finished.attempt_id == attempt.attempt_id
        assert finished.state == "succeeded" and not finished.active
        assert finished.attempt_count == 1
        assert sum(item.operation == operation for item in provider.ledger) == 1
    finally:
        if kind == "action":
            runtime.order_store.close()


@pytest.mark.parametrize("crash_site", ["save_order", "finish_attempt"])
def test_crash_reopen_reconciles_original_attempt(transaction, monkeypatch, crash_site):
    runtime, provider, path, _, request = transaction

    def crash(**kwargs):
        raise RuntimeError("fixture crash after merchant submission")

    monkeypatch.setattr(runtime.order_store, crash_site, crash)
    with pytest.raises(RuntimeError, match="fixture crash"):
        runtime.place_order(request, authorization_hash="a" * 64)
    before = _attempt(runtime, request)
    assert before.state == "submitted" and before.attempt_count == 1
    restarted = _reopen(runtime, provider, path)
    try:
        recovered = restarted.place_order(request, authorization_hash="a" * 64)
        after = _attempt(restarted, request)
        order = restarted.order_store.get_order("local", recovered.order_ref)
        assert recovered.state == "succeeded"
        assert order.placement_attempt_id == before.attempt_id == after.attempt_id
        assert after.state == "succeeded" and not after.active
        assert after.attempt_count == 1 and after.authorization_hash == "a" * 64
        assert after.response_digest is not None
        assert after.provider_reference_digest == commerce_digest(recovered.order_ref)
        assert [entry.operation for entry in provider.ledger].count("place_order") == 1
    finally:
        restarted.order_store.close()


def test_reopened_submitted_attempt_without_result_does_not_resubmit(transaction):
    runtime, provider, path, _, request = transaction
    reservation = runtime.order_store.reserve_placement(
        subject_id="local",
        preparation_id=request.preparation_ref,
        idempotency_key=request.idempotency_key,
        request_digest=commerce_digest(request.model_dump(mode="json")),
    )
    runtime.order_store.begin_placement_attempt(
        subject_id="local", attempt_id=reservation.attempt.attempt_id
    )
    restarted = _reopen(runtime, provider, path)
    try:
        with pytest.raises(CommerceOutcomeUnknown):
            restarted.place_order(request)
        assert _attempt(restarted, request).attempt_count == 1
        assert [entry.operation for entry in provider.ledger] == ["prepare_order"]
    finally:
        restarted.order_store.close()


def test_checkout_inspection_uses_persisted_reference_after_reopen(transaction):
    runtime, provider, path, preparation, _ = transaction
    assert preparation.checkout_ref != preparation.preparation_ref
    restarted = _reopen(runtime, provider, path)
    try:
        inspection = restarted.inspect_public(
            {"kind": "checkout", "preparation_ref": preparation.preparation_ref}
        )
        assert inspection.reference == preparation.checkout_ref
        assert provider.inspect_calls[-1].checkout_ref == preparation.checkout_ref
        assert [entry.operation for entry in provider.ledger] == ["prepare_order"]
    finally:
        restarted.order_store.close()


def test_legacy_preparation_without_checkout_snapshot_fails_before_provider(
    transaction,
):
    runtime, provider, _, preparation, _ = transaction
    runtime.order_store._record_store.execute_count(
        "UPDATE commerce_preparations SET prepared_json = NULL WHERE preparation_id = ?",
        (preparation.preparation_ref,),
    )
    with pytest.raises(CommerceProviderError) as error:
        runtime.inspect_public(
            {"kind": "checkout", "preparation_ref": preparation.preparation_ref}
        )
    assert error.value.code == "STALE_PREPARATION"
    assert provider.inspect_calls == []


@pytest.mark.parametrize(
    "revision",
    [
        "0001_baseline",
        "0002_preparation_invalidation",
        "0003_placement_attempt_authorization",
    ],
)
def test_populated_historical_database_keeps_identity_and_attempts(
    transaction, revision
):
    runtime, provider, path, preparation, request = transaction
    historical_path = path.with_name("historical.db")
    historical_path.touch()
    runner = MigrationRunner(
        module_id="commerce",
        db_path=historical_path,
        module_application_id=get_module_application_id("commerce"),
    )
    assert runner.alembic_script_location == (
        Path(__file__).resolve().parents[2]
        / "src/openminion/tools/commerce/storage/migrations"
    )
    migrated = runner.migrate(target=revision)
    assert migrated.success, migrated.error
    placement = provider.place_order(request)
    saved = runtime.order_store.get_preparation("local", preparation.preparation_ref)
    _populate_historical_database(historical_path, revision, saved, request, placement)
    before = runner.detect()
    assert before.alembic_revision == revision
    assert before.application_id == 0x4F4D0012

    upgraded = runner.migrate(target="head")
    assert upgraded.success, upgraded.error
    reopened = SQLiteCommerceOrderStore(historical_path)
    try:
        order = reopened.get_order("local", placement.order_ref)
        attempt = reopened.get_attempt_by_idempotency(
            subject_id="local",
            kind="placement",
            idempotency_key=request.idempotency_key,
        )
        assert order.payload == saved.payload and order.lifecycle == placement.lifecycle
        assert order.placement_attempt_id == attempt.attempt_id == "historical-attempt"
        assert attempt.state == "submitted" and attempt.active
        had_authorization = revision == "0003_placement_attempt_authorization"
        assert attempt.authorization_hash == ("a" * 64 if had_authorization else None)
        assert attempt.attempt_count == (1 if had_authorization else 0)
        assert (
            reopened.begin_placement_attempt(
                subject_id="local", attempt_id=attempt.attempt_id
            )
            is None
        )
        stored = reopened.get_preparation("local", preparation.preparation_ref)
        assert stored.prepared == (None if revision == "0001_baseline" else preparation)
        after = runner.detect()
        assert after.application_id == before.application_id
        assert after.om_meta["module_id"] == "commerce"
        assert after.alembic_revision == "0004_action_preparations"
        assert after.om_meta["schema_head"] == after.alembic_revision
        with sqlite3.connect(historical_path) as connection:
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        reopened.close()


def _populate_historical_database(path, revision, saved, request, placement):
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO commerce_attempts (
                attempt_id, subject_id, kind, target_id, operation, idempotency_key,
                request_digest, state, active, created_at, updated_at
            ) VALUES ('historical-attempt', 'local', 'placement', ?, 'place', ?, ?,
                      'submitted', 1, ?, ?)""",
            (
                saved.preparation_id,
                request.idempotency_key,
                commerce_digest(request.model_dump(mode="json")),
                saved.created_at,
                saved.created_at,
            ),
        )
        connection.execute(
            """INSERT INTO commerce_preparations (
                preparation_id, subject_id, payload_digest, payload_json, created_at
            ) VALUES (?, 'local', ?, ?, ?)""",
            (
                saved.preparation_id,
                saved.payload_digest,
                saved.payload.model_dump_json(),
                saved.created_at,
            ),
        )
        connection.execute(
            """INSERT INTO commerce_orders (
                order_id, subject_id, preparation_id, provider_order_digest,
                payload_digest, payload_json, lifecycle_json, placement_attempt_id,
                created_at, updated_at
            ) VALUES (?, 'local', ?, ?, ?, ?, ?, 'historical-attempt', ?, ?)""",
            (
                placement.order_ref,
                saved.preparation_id,
                commerce_digest(placement.order_ref),
                saved.payload_digest,
                saved.payload.model_dump_json(),
                placement.lifecycle.model_dump_json(),
                saved.created_at,
                saved.created_at,
            ),
        )
        if revision != "0001_baseline":
            connection.execute(
                "UPDATE commerce_preparations SET prepared_json = ?",
                (saved.prepared.model_dump_json(),),
            )
        if revision == "0003_placement_attempt_authorization":
            connection.execute(
                "UPDATE commerce_attempts SET authorization_hash = ?, attempt_count = 1",
                ("a" * 64,),
            )
