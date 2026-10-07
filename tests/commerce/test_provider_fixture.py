from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from openminion.modules.commerce.fixture import DroppedCommerceResponse
from openminion.modules.commerce.provider import (
    ActionRecoveryLocator,
    ApplyOrderActionRequest,
    CommerceProviderError,
    InspectRequest,
    PlaceOrderRequest,
    PlacementRecoveryLocator,
    PrepareOrderActionRequest,
    PrepareOrderRequest,
    PreparationRecoveryLocator,
    RequestedItem,
)
from openminion.modules.commerce.storage import SQLiteCommerceOrderStore
from tests.helpers.commerce_runtime import build_fixture_commerce_runtime


def _prepare(*, quantity: int = 1, store_path: Path | None = None):
    runtime, provider = build_fixture_commerce_runtime(store_path=store_path)
    request = PrepareOrderRequest(
        idempotency_key="prepare-1",
        items=(
            RequestedItem(
                offer_id="offer-1",
                variant_id="standard",
                quantity=quantity,
            ),
        ),
    )
    return runtime, provider, runtime.prepare_order(request)


def _place(*, quantity: int = 1, store_path: Path | None = None):
    runtime, provider, preparation = _prepare(
        quantity=quantity,
        store_path=store_path,
    )
    request = PlaceOrderRequest(
        idempotency_key="place-1",
        preparation_ref=preparation.preparation_ref,
        preparation_digest=preparation.preparation_digest,
    )
    return runtime, provider, preparation, request


def test_fixture_inspects_product_and_checkout_and_recovers_preparation() -> None:
    runtime, _, preparation = _prepare()

    product = runtime.inspect(
        InspectRequest(
            kind="product", merchant_id="merchant-fixture", product_ref="product-1"
        )
    )
    checkout = runtime.inspect(
        InspectRequest(
            kind="checkout",
            merchant_id="merchant-fixture",
            checkout_ref=preparation.checkout_ref,
        )
    )

    assert product.kind == "product"
    assert checkout.kind == "checkout"
    assert preparation.recurring is False
    assert preparation.fulfillment.kind == "shipping"
    assert (
        runtime.recover_preparation(
            PreparationRecoveryLocator(idempotency_key="prepare-1")
        )
        == preparation
    )


def test_prepare_order_requires_a_non_empty_item_list() -> None:
    with pytest.raises(ValidationError):
        PrepareOrderRequest(idempotency_key="prepare-empty", items=())


@pytest.mark.parametrize(
    ("provider_state", "order_state", "payment_state"),
    [
        ("succeeded", "accepted", "authorized"),
        ("declined", "declined", "declined"),
    ],
)
def test_fixture_places_accepted_and_declined_outcomes(
    provider_state: str,
    order_state: str,
    payment_state: str,
) -> None:
    runtime, provider, _, request = _place()
    provider.set_next_placement_state(provider_state)  # type: ignore[arg-type]

    result = runtime.place_order(request)

    assert result.state == provider_state
    assert result.lifecycle.order == order_state
    assert result.lifecycle.payment == payment_state


def test_action_required_handoff_invalidates_preparation(
    tmp_path,
) -> None:
    runtime, provider, preparation, request = _place(
        store_path=tmp_path / "commerce.db"
    )
    provider.set_next_placement_state("action_required")

    handoff = runtime.place_order(request)

    assert handoff.state == "handoff_required"
    record = runtime.order_store.get_preparation(
        "local",
        preparation.preparation_ref,
    )
    assert record.invalidated_at is not None
    with pytest.raises(CommerceProviderError, match="invalidated") as exc_info:
        runtime.place_order(
            request.model_copy(update={"idempotency_key": "place-after-handoff"})
        )
    assert exc_info.value.code == "STALE_PREPARATION"
    assert [entry.operation for entry in provider.ledger].count("place_order") == 1


def test_stable_placement_collision_recovers_existing_provider_attempt(
    tmp_path,
) -> None:
    runtime, provider, _, request = _place(store_path=tmp_path / "commerce.db")
    placed = runtime.place_order(request)

    recovered = runtime.place_order(
        request.model_copy(update={"idempotency_key": "place-retry"})
    )

    assert recovered == placed
    assert [entry.operation for entry in provider.ledger].count("place_order") == 1


def test_stable_preparation_collision_recovers_existing_provider_attempt(
    tmp_path: Path,
) -> None:
    runtime, provider, preparation = _prepare(store_path=tmp_path / "commerce.db")

    recovered = runtime.prepare_order(
        PrepareOrderRequest(
            idempotency_key="prepare-retry",
            items=(
                RequestedItem(
                    offer_id="offer-1",
                    variant_id="standard",
                    quantity=1,
                ),
            ),
        )
    )

    assert recovered == preparation
    assert [entry.operation for entry in provider.ledger].count("prepare_order") == 1


def test_active_action_collision_returns_typed_conflict(tmp_path: Path) -> None:
    runtime, provider, _, request = _place(store_path=tmp_path / "commerce.db")
    placement = runtime.place_order(request)
    assert placement.order_ref is not None
    runtime.order_store.reserve_action(
        subject_id="local",
        order_id=placement.order_ref,
        operation="apply",
        idempotency_key="apply-existing",
        request_digest="sha256:" + "a" * 64,
    )

    with pytest.raises(CommerceProviderError, match="still active") as exc_info:
        runtime.apply_action(
            ApplyOrderActionRequest(
                idempotency_key="apply-new",
                action_ref="action-new",
                order_ref=placement.order_ref,
                action_digest="sha256:" + "b" * 64,
            )
        )

    assert exc_info.value.code == "ACTION_ALREADY_OPEN"
    assert [entry.operation for entry in provider.ledger].count("apply_action") == 0


def test_preparation_snapshot_round_trips_after_store_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "commerce.db"
    runtime, _, preparation = _prepare(store_path=database_path)
    runtime.order_store.close()

    restarted = SQLiteCommerceOrderStore(database_path)
    record = restarted.get_preparation("local", preparation.preparation_ref)

    assert record is not None
    assert record.prepared == preparation
    assert "fixture-provider-secret" not in database_path.read_bytes().decode(
        errors="ignore"
    )


def test_fixture_rejects_stale_preparation_and_splits_shipments() -> None:
    runtime, provider, preparation, request = _place(quantity=2)
    provider.advance_checkout_revision()
    with pytest.raises(CommerceProviderError, match="stale") as exc_info:
        runtime.place_order(request)
    assert exc_info.value.code == "STALE_REVISION"

    provider.checkout_revision = preparation.checkout_revision
    placement = runtime.place_order(request)
    assert len(placement.lifecycle.shipments) == 2


@pytest.mark.parametrize(
    "kind",
    ["cancel", "partial_cancel", "return", "partial_return", "refund_request"],
)
def test_fixture_prepares_and_applies_closed_order_actions(kind: str) -> None:
    runtime, _, _, place_request = _place()
    placement = runtime.place_order(place_request)
    assert placement.order_ref is not None
    partial = kind in {"partial_cancel", "partial_return"}
    action = runtime.prepare_action(
        PrepareOrderActionRequest(
            idempotency_key=f"prepare-{kind}",
            order_ref=placement.order_ref,
            order_revision=str(placement.order_revision),
            kind=kind,  # type: ignore[arg-type]
            line_item_ids=("line-1",) if partial else (),
            quantity=1 if partial else None,
            reason=(
                "Fixture request"
                if kind in {"return", "partial_return", "refund_request"}
                else None
            ),
            refund_method=(
                "original_payment_method" if kind == "refund_request" else None
            ),
        )
    )
    result = runtime.apply_action(
        ApplyOrderActionRequest(
            idempotency_key=f"apply-{kind}",
            action_ref=action.action_ref,
            order_ref=placement.order_ref,
            action_digest=action.action_digest,
        )
    )

    assert action.eligible is True
    assert result.state == "completed"


def test_fixture_recovers_dropped_placement_and_action_responses() -> None:
    runtime, provider, preparation, place_request = _place()
    provider.drop_next_response("place_order")
    with pytest.raises(DroppedCommerceResponse):
        provider.place_order(place_request)
    placement = runtime.recover_placement(
        PlacementRecoveryLocator(
            idempotency_key=place_request.idempotency_key,
            preparation_digest=preparation.preparation_digest,
        )
    )
    assert placement is not None
    assert placement.order_ref is not None
    assert provider.ledger[-1].operation == "place_order"

    action = runtime.prepare_action(
        PrepareOrderActionRequest(
            idempotency_key="prepare-cancel",
            order_ref=placement.order_ref,
            order_revision=str(placement.order_revision),
            kind="cancel",
        )
    )
    apply_request = ApplyOrderActionRequest(
        idempotency_key="apply-cancel",
        action_ref=action.action_ref,
        order_ref=placement.order_ref,
        action_digest=action.action_digest,
    )
    provider.drop_next_response("apply_action")
    with pytest.raises(DroppedCommerceResponse):
        provider.apply_action(apply_request)
    recovered = runtime.recover_action(
        ActionRecoveryLocator(
            idempotency_key=apply_request.idempotency_key,
            action_digest=action.action_digest,
        )
    )
    assert recovered is not None
    assert recovered.state == "completed"
    assert provider.ledger[-1].operation == "apply_action"


def test_fixture_retains_latest_order_inspection_revision() -> None:
    runtime, provider, _, place_request = _place()
    placement = runtime.place_order(place_request)
    provider.set_order_inspection_lifecycles(
        (
            placement.lifecycle,
            placement.lifecycle.model_copy(update={"fulfillment": "partial"}),
        )
    )
    request = InspectRequest(
        kind="order",
        merchant_id="merchant-fixture",
        order_ref=str(placement.order_ref),
    )

    assert runtime.inspect(request).revision == "order-1:r1"
    assert runtime.inspect(request).revision == "order-1:r2"
    assert runtime.inspect(request).revision == "order-1:r2"


def test_runtime_recovers_dropped_preparation_response() -> None:
    runtime, provider = build_fixture_commerce_runtime()
    provider.drop_next_response("prepare_order")

    preparation = runtime.prepare_order(
        PrepareOrderRequest(
            idempotency_key="prepare-dropped",
            items=(
                RequestedItem(
                    offer_id="offer-1",
                    variant_id="standard",
                    quantity=1,
                ),
            ),
        )
    )

    assert preparation.state == "prepared"
    assert provider.ledger[-1].operation == "prepare_order"
