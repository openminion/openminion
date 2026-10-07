from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from openminion.tools.commerce.constants import (
    ACTION_REQUEST_STATE_VALUES,
    FULFILLMENT_STATE_VALUES,
    ORDER_STATE_VALUES,
    PAYMENT_STATE_VALUES,
    SHIPMENT_STATE_VALUES,
)
from openminion.tools.commerce.models import (
    CommerceLifecycleState,
    LineItem,
    MerchantIdentity,
    Money,
    SafeBuyerProfile,
    SafeDestination,
    SafePaymentMethod,
    SellerIdentity,
)
from openminion.tools.commerce.storage import (
    PreparationPayload,
    SQLiteCommerceOrderStore,
)
from openminion.tools.commerce.storage.migrations import run_migrations
from openminion.modules.storage.migrations.module_ids import (
    get_module_application_id,
)

_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_DIGEST_C = "sha256:" + "c" * 64


@pytest.fixture
def payload() -> PreparationPayload:
    return PreparationPayload(
        merchant=MerchantIdentity(
            provider_id="merchant-1", display_name="Example Merchant"
        ),
        seller=SellerIdentity(provider_id="seller-1", display_name="Example Seller"),
        items=(
            LineItem(
                product_id="product-1",
                offer_id="offer-1",
                variant_id="blue-medium",
                line_item_id="line-1",
                quantity=1,
                unit_price=Money(currency="USD", amount_minor=1299),
                availability="available",
                returnable=True,
            ),
        ),
        buyer=SafeBuyerProfile(profile_digest=_DIGEST_A, label="Saved buyer"),
        destination=SafeDestination(
            destination_digest=_DIGEST_B,
            label="Home ending 42",
            region="US-CA",
        ),
        payment=SafePaymentMethod(
            payment_destination_digest=_DIGEST_C,
            label="Visa ending 4242",
            brand="Visa",
            last_four="4242",
        ),
        total=Money(currency="USD", amount_minor=1299),
    )


def _lifecycle(**updates: str) -> CommerceLifecycleState:
    payload: dict[str, object] = {
        "order": "prepared",
        "fulfillment": "unfulfilled",
        "payment": "pending",
        "shipments": {"shipment-1": "label_created"},
        "action_request": "prepared",
    }
    payload.update(updates)
    return CommerceLifecycleState.model_validate(payload)


def _seed_order(
    store: SQLiteCommerceOrderStore,
    payload: PreparationPayload,
    *,
    subject_id: str = "subject-1",
    preparation_id: str = "preparation-1",
    order_id: str = "order-1",
) -> None:
    store.save_preparation(
        subject_id=subject_id,
        preparation_id=preparation_id,
        payload=payload,
    )
    store.save_order(
        subject_id=subject_id,
        order_id=order_id,
        preparation_id=preparation_id,
        provider_order_digest=_DIGEST_A,
        lifecycle=_lifecycle(),
    )


def test_commerce_migration_bootstraps_standard_module_identity(tmp_path: Path) -> None:
    db_path = tmp_path / "commerce.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE existing_data(value TEXT NOT NULL)")
        connection.execute("INSERT INTO existing_data VALUES ('preserved')")

    run_migrations(db_path)

    with sqlite3.connect(db_path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        metadata = dict(connection.execute("SELECT key, value FROM om_meta"))
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        preserved = connection.execute("SELECT value FROM existing_data").fetchone()

    assert {
        "commerce_attempts",
        "commerce_preparations",
        "commerce_orders",
        "commerce_material_snapshots",
        "commerce_action_preparations",
    } <= tables
    assert metadata["module_id"] == "commerce"
    assert metadata["schema_head"] == "0004_action_preparations"
    assert application_id == get_module_application_id("commerce")
    assert preserved == ("preserved",)


def test_order_store_round_trips_every_closed_lifecycle_state(
    tmp_path: Path, payload: PreparationPayload
) -> None:
    store = SQLiteCommerceOrderStore(tmp_path / "commerce.db")
    _seed_order(store, payload)

    cases = (
        ("order", ORDER_STATE_VALUES),
        ("fulfillment", FULFILLMENT_STATE_VALUES),
        ("payment", PAYMENT_STATE_VALUES),
        ("action_request", ACTION_REQUEST_STATE_VALUES),
    )
    for field, values in cases:
        for value in values:
            lifecycle = _lifecycle(**{field: value})
            assert (
                store.update_order_lifecycle(
                    subject_id="subject-1",
                    order_id="order-1",
                    lifecycle=lifecycle,
                ).lifecycle
                == lifecycle
            )
    for value in SHIPMENT_STATE_VALUES:
        lifecycle = _lifecycle()
        lifecycle = lifecycle.model_copy(update={"shipments": {"shipment-1": value}})
        assert (
            store.update_order_lifecycle(
                subject_id="subject-1",
                order_id="order-1",
                lifecycle=lifecycle,
            ).lifecycle
            == lifecycle
        )


def test_store_rejects_copied_records_and_cross_subject_children(
    tmp_path: Path, payload: PreparationPayload
) -> None:
    store = SQLiteCommerceOrderStore(tmp_path / "commerce.db")
    _seed_order(store, payload)

    with pytest.raises(ValueError, match="preparation identity"):
        store.save_preparation(
            subject_id="subject-2",
            preparation_id="preparation-1",
            payload=payload,
        )
    with pytest.raises(ValueError, match="preparation must belong"):
        store.save_order(
            subject_id="subject-2",
            order_id="order-2",
            preparation_id="preparation-1",
            provider_order_digest=_DIGEST_B,
            lifecycle=_lifecycle(),
        )
    store.save_preparation(
        subject_id="subject-2",
        preparation_id="preparation-2",
        payload=payload,
    )
    with pytest.raises(ValueError, match="order identity"):
        store.save_order(
            subject_id="subject-2",
            order_id="order-1",
            preparation_id="preparation-2",
            provider_order_digest=_DIGEST_B,
            lifecycle=_lifecycle(),
        )
    with pytest.raises(ValueError, match="snapshot target"):
        store.save_material_snapshot(
            subject_id="subject-2",
            snapshot_id="snapshot-1",
            target_type="order",
            target_id="order-1",
            lifecycle=_lifecycle(),
        )


def test_concurrent_preparation_reservation_has_one_winner(tmp_path: Path) -> None:
    db_path = tmp_path / "commerce.db"
    stores = [SQLiteCommerceOrderStore(db_path) for _ in range(2)]

    def reserve(index: int) -> bool:
        return (
            stores[index]
            .reserve_preparation(
                subject_id="subject-1",
                target_id="cart-1",
                idempotency_key=f"prepare-{index}",
                request_digest=_DIGEST_A,
            )
            .created
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, range(2)))

    assert sorted(results) == [False, True]
    stores[0].close()
    stores[1].close()


def test_invalidated_preparation_cannot_be_reserved_for_placement(
    tmp_path: Path, payload: PreparationPayload
) -> None:
    store = SQLiteCommerceOrderStore(tmp_path / "commerce.db")
    store.save_preparation(
        subject_id="subject-1",
        preparation_id="preparation-1",
        payload=payload,
    )

    invalidated = store.invalidate_preparation(
        subject_id="subject-1",
        preparation_id="preparation-1",
    )

    assert invalidated.invalidated_at is not None
    with pytest.raises(ValueError, match="invalidated"):
        store.reserve_placement(
            subject_id="subject-1",
            preparation_id="preparation-1",
            idempotency_key="place-1",
            request_digest=_DIGEST_A,
        )


def test_preparation_response_loss_recovers_by_idempotency_after_restart(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "commerce.db"
    store = SQLiteCommerceOrderStore(db_path)
    reserved = store.reserve_preparation(
        subject_id="subject-1",
        target_id="cart-1",
        idempotency_key="prepare-once",
        request_digest=_DIGEST_A,
    )
    store.finish_attempt(
        subject_id="subject-1",
        attempt_id=reserved.attempt.attempt_id,
        state="outcome_unknown",
        provider_reference_digest=_DIGEST_B,
    )
    store.close()

    restarted = SQLiteCommerceOrderStore(db_path)
    recovered = restarted.get_attempt_by_idempotency(
        subject_id="subject-1",
        kind="preparation",
        idempotency_key="prepare-once",
    )

    assert recovered is not None
    assert recovered.attempt_id == reserved.attempt.attempt_id
    assert recovered.state == "outcome_unknown"
    assert recovered.provider_reference_digest == _DIGEST_B


def test_only_one_care_action_can_be_active(
    tmp_path: Path, payload: PreparationPayload
) -> None:
    store = SQLiteCommerceOrderStore(tmp_path / "commerce.db")
    _seed_order(store, payload)

    first = store.reserve_action(
        subject_id="subject-1",
        order_id="order-1",
        operation="cancel",
        idempotency_key="cancel-1",
        request_digest=_DIGEST_A,
    )
    second = store.reserve_action(
        subject_id="subject-1",
        order_id="order-1",
        operation="refund",
        idempotency_key="refund-1",
        request_digest=_DIGEST_B,
    )

    assert first.created is True
    assert second.created is False
    assert second.attempt.attempt_id == first.attempt.attempt_id
    store.finish_attempt(
        subject_id="subject-1",
        attempt_id=first.attempt.attempt_id,
        state="succeeded",
        response_digest=_DIGEST_C,
    )
    third = store.reserve_action(
        subject_id="subject-1",
        order_id="order-1",
        operation="refund",
        idempotency_key="refund-1",
        request_digest=_DIGEST_B,
    )
    assert third.created is True


def test_store_persists_no_protected_payment_values(
    tmp_path: Path, payload: PreparationPayload
) -> None:
    with pytest.raises(ValidationError):
        PreparationPayload.model_validate(
            {**payload.model_dump(), "payment_token": "tok_protected_123"}
        )

    db_path = tmp_path / "commerce.db"
    store = SQLiteCommerceOrderStore(db_path)
    _seed_order(store, payload)
    store.save_material_snapshot(
        subject_id="subject-1",
        snapshot_id="snapshot-1",
        target_type="order",
        target_id="order-1",
        lifecycle=_lifecycle(),
        cursor_digest=_DIGEST_C,
    )
    store.close()

    stored = db_path.read_bytes()
    assert b"tok_protected_123" not in stored
    assert b"payment_token" not in stored
    assert b"4242424242424242" not in stored
