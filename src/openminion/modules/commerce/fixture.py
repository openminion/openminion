"""Deterministic direct-injection commerce provider fixture."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from .models import (
    CommerceLifecycleState,
    LineItem,
    MerchantIdentity,
    Money,
    SafeBuyerProfile,
    SafeDestination,
    SafePaymentMethod,
    SellerIdentity,
)
from .provider import (
    ActionRecoveryLocator,
    ApplyOrderActionRequest,
    CheckoutInspection,
    CommerceInspection,
    CommerceOutcomeUnknown,
    CommerceProviderError,
    CommerceTotals,
    FulfillmentSelection,
    InspectRequest,
    OrderActionKind,
    OrderActionPreparation,
    OrderActionResult,
    OrderActionResultState,
    OrderActionsInspection,
    OrderInspection,
    OrderPlacement,
    OrderPreparation,
    PlaceOrderRequest,
    PlacementRecoveryLocator,
    PlacementState,
    PrepareOrderActionRequest,
    PrepareOrderRequest,
    PreparationRecoveryLocator,
    ProductInspection,
    ProviderOrderContext,
    RefundDestination,
    ShipmentInspection,
)

_FIXTURE_EXPIRES_AT = "2030-01-01T00:00:00Z"
_BUYER_DIGEST = "sha256:" + "1" * 64
_DESTINATION_DIGEST = "sha256:" + "2" * 64
_PAYMENT_DIGEST = "sha256:" + "3" * 64


class DroppedCommerceResponse(CommerceOutcomeUnknown):
    pass


@dataclass(frozen=True)
class FixtureLedgerEntry:
    operation: Literal["prepare_order", "place_order", "apply_action"]
    idempotency_key: str
    accepted_ref: str


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class FixtureCommerceProvider:
    provider_id = "fixture"

    def __init__(self) -> None:
        self.merchant = MerchantIdentity(
            provider_id="merchant-fixture", display_name="Fixture Merchant"
        )
        self.seller = SellerIdentity(
            provider_id="seller-fixture", display_name="Fixture Seller"
        )
        self.item = LineItem(
            product_id="product-1",
            offer_id="offer-1",
            variant_id="standard",
            line_item_id="line-1",
            quantity=1,
            unit_price=Money(currency="USD", amount_minor=2500),
            availability="available",
            returnable=True,
        )
        self.buyer = SafeBuyerProfile(
            profile_digest=_BUYER_DIGEST, label="Fixture buyer"
        )
        self.destination = SafeDestination(
            destination_digest=_DESTINATION_DIGEST,
            label="Home ending 42",
            region="US-CA",
        )
        self.payment = SafePaymentMethod(
            payment_destination_digest=_PAYMENT_DIGEST,
            label="Visa ending 4242",
            brand="Visa",
            last_four="4242",
        )
        self.checkout_ref = "checkout-1"
        self.checkout_revision = "checkout-1:r1"
        self.ledger: list[FixtureLedgerEntry] = []
        self.inspect_calls: list[InspectRequest] = []
        self._preparations: dict[str, OrderPreparation] = {}
        self._placements: dict[str, OrderPlacement] = {}
        self._action_preparations: dict[str, OrderActionPreparation] = {}
        self._action_results: dict[str, OrderActionResult] = {}
        self._orders: dict[str, OrderPlacement] = {}
        self._next_placement_state: PlacementState = "succeeded"
        self._next_action_state: OrderActionResultState = "completed"
        self._drop_next: set[str] = set()

    def set_next_placement_state(self, state: PlacementState) -> None:
        self._next_placement_state = state

    def set_next_action_state(self, state: OrderActionResultState) -> None:
        self._next_action_state = state

    def advance_checkout_revision(self) -> None:
        self.checkout_revision = "checkout-1:r2"

    def drop_next_response(
        self, operation: Literal["prepare_order", "place_order", "apply_action"]
    ) -> None:
        self._drop_next.add(operation)

    def inspect(self, request: InspectRequest) -> CommerceInspection:
        self.inspect_calls.append(request)
        if request.merchant_id != self.merchant.provider_id:
            raise CommerceProviderError(
                "MERCHANT_MISMATCH", "Configured merchant does not match fixture."
            )
        if request.kind == "product":
            return ProductInspection(
                reference=request.reference,
                revision="product-1:r1",
                merchant=self.merchant,
                seller=self.seller,
                item=self.item,
                links={"product": "https://fixture.invalid/products/product-1"},
            )
        if request.kind == "checkout":
            self._require_reference(request.reference, self.checkout_ref)
            preparation = next(reversed(tuple(self._preparations.values())))
            return CheckoutInspection(
                reference=self.checkout_ref,
                revision=self.checkout_revision,
                items=preparation.items,
                subtotal=preparation.totals.subtotal,
                total=preparation.totals.total,
                warnings=preparation.warnings,
                links={"checkout": "https://fixture.invalid/checkouts/checkout-1"},
            )
        if request.kind == "order":
            placement = self._require_order(request.reference)
            terminal_action_refs = {
                result.action_ref
                for result in self._action_results.values()
                if result.state in {"completed", "rejected", "failed"}
            }
            return OrderInspection(
                reference=request.reference,
                revision=placement.order_revision or "order-1:r1",
                items=(self.item,),
                lifecycle=placement.lifecycle,
                total=self.item.unit_price,
                open_action_ids=tuple(
                    sorted(
                        preparation.action_ref
                        for preparation in self._action_preparations.values()
                        if preparation.order_ref == request.reference
                        and preparation.action_ref not in terminal_action_refs
                    )
                ),
                links=placement.links,
            )
        if request.kind == "shipment":
            placement = self._shipment_placement(request.reference)
            return ShipmentInspection(
                reference=request.reference,
                order_ref=str(placement.order_ref),
                revision=f"{request.reference}:r1",
                state=placement.lifecycle.shipments[request.reference],
                line_item_ids=(str(self.item.line_item_id),),
                links={"tracking": f"https://fixture.invalid/{request.reference}"},
            )
        placement = self._require_order(request.reference)
        return OrderActionsInspection(
            reference=str(placement.order_ref),
            revision=str(placement.order_revision),
            actions=(
                "cancel",
                "partial_cancel",
                "return",
                "partial_return",
                "refund_request",
            ),
        )

    def prepare_order(
        self, request: PrepareOrderRequest, context: ProviderOrderContext
    ) -> OrderPreparation:
        del context
        recovered = self._preparations.get(request.idempotency_key)
        if recovered is not None:
            return recovered
        quantity = 0
        for requested in request.items:
            if (
                requested.offer_id != self.item.offer_id
                or requested.variant_id != self.item.variant_id
            ):
                raise CommerceProviderError(
                    "ITEM_UNAVAILABLE",
                    "The requested offer or variant is unavailable.",
                )
            quantity += requested.quantity
        item = self.item.model_copy(update={"quantity": quantity})
        subtotal_minor = item.unit_price.amount_minor * quantity
        zero = Money(currency="USD", amount_minor=0)
        shipping = Money(currency="USD", amount_minor=500)
        tax = Money(currency="USD", amount_minor=subtotal_minor // 10)
        totals = CommerceTotals(
            subtotal=Money(currency="USD", amount_minor=subtotal_minor),
            discount=zero,
            tax=tax,
            shipping=shipping,
            fees=zero,
            total=Money(
                currency="USD", amount_minor=subtotal_minor + tax.amount_minor + 500
            ),
        )
        digest = _digest(
            {
                "checkout_ref": self.checkout_ref,
                "checkout_revision": self.checkout_revision,
                "quantity": quantity,
                "promotion_code": request.promotion_code,
                "total": totals.total.model_dump(),
            }
        )
        preparation = OrderPreparation(
            preparation_ref=f"preparation-{len(self._preparations) + 1}",
            checkout_ref=self.checkout_ref,
            checkout_revision=self.checkout_revision,
            items=(item,),
            merchant=self.merchant,
            seller=self.seller,
            fulfillment=FulfillmentSelection(
                kind="shipping",
                label=self.destination.label,
                destination=self.destination,
            ),
            buyer=self.buyer,
            destination=self.destination,
            payment=self.payment,
            totals=totals,
            state="prepared",
            expires_at=_FIXTURE_EXPIRES_AT,
            preparation_digest=digest,
        )
        self._preparations[request.idempotency_key] = preparation
        self.ledger.append(
            FixtureLedgerEntry(
                operation="prepare_order",
                idempotency_key=request.idempotency_key,
                accepted_ref=preparation.preparation_ref,
            )
        )
        self._drop_after_acceptance("prepare_order")
        return preparation

    def recover_preparation(
        self, locator: PreparationRecoveryLocator
    ) -> OrderPreparation | None:
        return self._preparations.get(locator.idempotency_key)

    def place_order(self, request: PlaceOrderRequest) -> OrderPlacement:
        recovered = self._placements.get(request.idempotency_key)
        if recovered is not None:
            self._require_digest(
                request.preparation_digest,
                self._preparation_for_ref(request.preparation_ref).preparation_digest,
            )
            return recovered
        preparation = self._preparation_for_ref(request.preparation_ref)
        self._require_digest(request.preparation_digest, preparation.preparation_digest)
        if preparation.checkout_revision != self.checkout_revision:
            raise CommerceProviderError("STALE_REVISION", "Checkout revision is stale.")
        state = self._next_placement_state
        self._next_placement_state = "succeeded"
        order_ref = (
            f"order-{len(self._placements) + 1}"
            if state in {"succeeded", "action_required", "outcome_unknown"}
            else None
        )
        quantity = sum(item.quantity for item in preparation.items)
        shipments = (
            {f"shipment-{index}": "label_created" for index in range(1, quantity + 1)}
            if state == "succeeded"
            else {}
        )
        lifecycle = CommerceLifecycleState(
            order={
                "succeeded": "accepted",
                "declined": "declined",
                "action_required": "action_required",
                "failed": "provider_unknown",
                "outcome_unknown": "outcome_unknown",
            }[state],
            fulfillment="unfulfilled" if state == "succeeded" else "provider_unknown",
            payment={
                "succeeded": "authorized",
                "declined": "declined",
                "action_required": "pending",
                "failed": "provider_unknown",
                "outcome_unknown": "provider_unknown",
            }[state],
            shipments=shipments,
            action_request="pending" if state == "action_required" else None,
        )
        placement = OrderPlacement(
            idempotency_key=request.idempotency_key,
            preparation_ref=request.preparation_ref,
            state=state,
            order_ref=order_ref,
            order_revision=f"{order_ref}:r1" if order_ref else None,
            lifecycle=lifecycle,
            links=(
                {"order": f"https://fixture.invalid/orders/{order_ref}"}
                if order_ref
                else {}
            ),
        )
        self._placements[request.idempotency_key] = placement
        if order_ref:
            self._orders[order_ref] = placement
        self.ledger.append(
            FixtureLedgerEntry(
                operation="place_order",
                idempotency_key=request.idempotency_key,
                accepted_ref=order_ref or request.preparation_ref,
            )
        )
        self._drop_after_acceptance("place_order")
        return placement

    def recover_placement(
        self, locator: PlacementRecoveryLocator
    ) -> OrderPlacement | None:
        placement = self._placements.get(locator.idempotency_key)
        if placement is None:
            return None
        preparation = self._preparation_for_ref(placement.preparation_ref)
        self._require_digest(locator.preparation_digest, preparation.preparation_digest)
        return placement

    def prepare_action(
        self, request: PrepareOrderActionRequest
    ) -> OrderActionPreparation:
        recovered = self._action_preparations.get(request.idempotency_key)
        if recovered is not None:
            return recovered
        placement = self._require_order(request.order_ref)
        if request.order_revision != placement.order_revision:
            raise CommerceProviderError("STALE_REVISION", "Order revision is stale.")
        partial = request.kind in {"partial_cancel", "partial_return"}
        if partial and not request.line_item_ids:
            raise CommerceProviderError(
                "ACTION_INELIGIBLE", "Partial actions require line item ids."
            )
        digest = _digest(request.model_dump(mode="json"))
        preparation = OrderActionPreparation(
            action_ref=f"action-{len(self._action_preparations) + 1}",
            order_ref=request.order_ref,
            order_revision=request.order_revision,
            kind=request.kind,
            eligible=True,
            consequence=self._action_consequence(request.kind),
            refund_method=request.refund_method,
            refund_destination=(
                RefundDestination(
                    destination_digest=self.payment.payment_destination_digest,
                    label=self.payment.label,
                )
                if request.refund_method == "original_payment_method"
                else RefundDestination(
                    destination_digest="sha256:" + "4" * 64,
                    label="Fixture store credit",
                )
                if request.refund_method == "store_credit"
                else None
            ),
            returnable_line_item_ids=(
                tuple(request.line_item_ids)
                if request.kind in {"partial_return"}
                else (str(self.item.line_item_id),)
                if request.kind == "return"
                else ()
            ),
            return_destination=(
                self.destination
                if request.kind in {"return", "partial_return"}
                else None
            ),
            expires_at=_FIXTURE_EXPIRES_AT,
            action_digest=digest,
        )
        self._action_preparations[request.idempotency_key] = preparation
        return preparation

    def apply_action(self, request: ApplyOrderActionRequest) -> OrderActionResult:
        recovered = self._action_results.get(request.idempotency_key)
        if recovered is not None:
            self._require_digest(
                request.action_digest,
                self._action_preparation_for_ref(request.action_ref).action_digest,
            )
            return recovered
        preparation = self._action_preparation_for_ref(request.action_ref)
        self._require_digest(request.action_digest, preparation.action_digest)
        state = self._next_action_state
        self._next_action_state = "completed"
        lifecycle = self._action_lifecycle(preparation, state)
        result = OrderActionResult(
            idempotency_key=request.idempotency_key,
            action_ref=request.action_ref,
            order_ref=preparation.order_ref,
            state=state,
            lifecycle=lifecycle,
            links={
                "order": f"https://fixture.invalid/orders/{preparation.order_ref}"
            },
        )
        self._action_results[request.idempotency_key] = result
        self.ledger.append(
            FixtureLedgerEntry(
                operation="apply_action",
                idempotency_key=request.idempotency_key,
                accepted_ref=request.action_ref,
            )
        )
        self._drop_after_acceptance("apply_action")
        return result

    def recover_action(
        self, locator: ActionRecoveryLocator
    ) -> OrderActionResult | None:
        result = self._action_results.get(locator.idempotency_key)
        if result is None:
            return None
        preparation = self._action_preparation_for_ref(result.action_ref)
        self._require_digest(locator.action_digest, preparation.action_digest)
        return result

    @staticmethod
    def _action_consequence(kind: OrderActionKind) -> str:
        return {
            "cancel": "Cancels the whole order.",
            "partial_cancel": "Cancels the selected line items.",
            "return": "Returns all returnable line items.",
            "partial_return": "Returns the selected line items.",
            "refund_request": "Requests a refund from the merchant.",
        }[kind]

    def _action_lifecycle(
        self,
        preparation: OrderActionPreparation,
        state: OrderActionResultState,
    ) -> CommerceLifecycleState:
        if state != "completed":
            return CommerceLifecycleState(
                order="accepted",
                fulfillment="unfulfilled",
                payment="authorized",
                action_request={
                    "pending": "pending",
                    "rejected": "rejected",
                    "failed": "failed",
                    "outcome_unknown": "outcome_unknown",
                }[state],
            )
        if preparation.kind in {"cancel", "partial_cancel"}:
            return CommerceLifecycleState(
                order="cancelled",
                fulfillment="cancelled",
                payment="refund_pending",
                action_request="completed",
            )
        if preparation.kind in {"return", "partial_return"}:
            return CommerceLifecycleState(
                order="completed",
                fulfillment="returned",
                payment="refund_pending",
                action_request="completed",
            )
        return CommerceLifecycleState(
            order="completed",
            fulfillment="fulfilled",
            payment="refund_pending",
            action_request="completed",
        )

    def _drop_after_acceptance(self, operation: str) -> None:
        if operation not in self._drop_next:
            return
        self._drop_next.remove(operation)
        raise DroppedCommerceResponse(
            f"Fixture dropped the {operation} response after acceptance."
        )

    def _preparation_for_ref(self, reference: str) -> OrderPreparation:
        for preparation in self._preparations.values():
            if preparation.preparation_ref == reference:
                return preparation
        raise CommerceProviderError("NOT_FOUND", "Preparation was not found.")

    def _action_preparation_for_ref(
        self, reference: str
    ) -> OrderActionPreparation:
        for preparation in self._action_preparations.values():
            if preparation.action_ref == reference:
                return preparation
        raise CommerceProviderError("NOT_FOUND", "Action preparation was not found.")

    def _require_order(self, reference: str) -> OrderPlacement:
        placement = self._orders.get(reference)
        if placement is None:
            raise CommerceProviderError("NOT_FOUND", "Order was not found.")
        return placement

    def _shipment_placement(self, shipment_ref: str) -> OrderPlacement:
        for placement in self._orders.values():
            if shipment_ref in placement.lifecycle.shipments:
                return placement
        raise CommerceProviderError("NOT_FOUND", "Shipment was not found.")

    @staticmethod
    def _require_reference(actual: str, expected: str) -> None:
        if actual != expected:
            raise CommerceProviderError("NOT_FOUND", "Commerce reference was not found.")

    @staticmethod
    def _require_digest(actual: str, expected: str) -> None:
        if actual != expected:
            raise CommerceProviderError("DIGEST_MISMATCH", "Commerce digest mismatch.")


__all__ = [
    "DroppedCommerceResponse",
    "FixtureCommerceProvider",
    "FixtureLedgerEntry",
]
