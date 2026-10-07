"""Direct commerce provider, persistence, and secret-service composition."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol, cast

from openminion.modules.runtime.credentials import (
    CredentialAuditLog,
    InMemoryCredentialAuditLog,
    record_credential_access_event,
    resolve_credential_ref,
)

from .constants import COMMERCE_LOCAL_SUBJECT_ID
from .config import CommerceToolRuntimeConfig, coerce_commerce_tool_runtime_config
from .contracts import InspectionKind
from .provider import (
    ActionRecoveryLocator,
    ApplyOrderActionRequest,
    CommerceHandoff,
    CommerceInspection,
    CommerceOutcomeUnknown,
    CommerceProvider,
    CommerceProviderError,
    CheckoutInspection,
    InspectRequest,
    OrderActionPreparation,
    OrderActionResult,
    OrderInspection,
    OrderPlacement,
    OrderPreparation,
    PlaceOrderRequest,
    PlacementRecoveryLocator,
    PrepareOrderActionRequest,
    PrepareOrderRequest,
    PreparationRecoveryLocator,
    ProviderOrderContext,
    RequestedItem,
)
from .identity import commerce_digest
from .runtime_persistence import CommerceRuntimePersistenceMixin


class CommerceOrderStore(Protocol):
    def get_preparation(self, subject_id: str, preparation_id: str) -> Any: ...

    def get_order(self, subject_id: str, order_id: str) -> Any: ...

    def get_action_preparation(self, subject_id: str, action_id: str) -> Any: ...

    def reserve_preparation(self, **kwargs: Any) -> Any: ...

    def reserve_placement(self, **kwargs: Any) -> Any: ...

    def reserve_action(self, **kwargs: Any) -> Any: ...

    def save_preparation(self, **kwargs: Any) -> Any: ...

    def save_order(self, **kwargs: Any) -> Any: ...

    def save_action_preparation(self, **kwargs: Any) -> Any: ...

    def update_order_lifecycle(self, **kwargs: Any) -> Any: ...

    def finish_attempt(self, **kwargs: Any) -> Any: ...

    def invalidate_preparation(self, **kwargs: Any) -> Any: ...

    def begin_placement_attempt(self, **kwargs: Any) -> Any: ...

    def begin_action_attempt(self, **kwargs: Any) -> Any: ...


class CommerceRuntime(CommerceRuntimePersistenceMixin):
    def __init__(
        self,
        *,
        provider: CommerceProvider,
        base_url: str,
        merchant_id: str,
        provider_secret_key: str,
        buyer_profile_record_id: str,
        payment_token_record_id: str,
        order_store: CommerceOrderStore | None = None,
        secret_service: Any | None = None,
        credential_audit_log: CredentialAuditLog | None = None,
    ) -> None:
        self.provider = provider
        self.base_url = base_url
        self.merchant_id = merchant_id
        self.provider_secret_key = provider_secret_key
        self.buyer_profile_record_id = buyer_profile_record_id
        self.payment_token_record_id = payment_token_record_id
        self.order_store = order_store
        self.secret_service = secret_service
        self.credential_audit_log = credential_audit_log or InMemoryCredentialAuditLog()

    def inspect(self, request: InspectRequest) -> CommerceInspection:
        inspection = self.provider.inspect(
            request.model_copy(update={"merchant_id": self.merchant_id})
        )
        merchant = getattr(inspection, "merchant", None)
        if merchant is not None and merchant.provider_id != self.merchant_id:
            from .provider import CommerceProviderError

            raise CommerceProviderError(
                "MERCHANT_MISMATCH",
                "Provider response does not match the configured merchant.",
            )
        return self._sanitize_links(inspection)

    def inspect_public(self, args: dict[str, Any]) -> CommerceInspection:
        kind = str(args["kind"])
        if kind == "product":
            request = InspectRequest(
                kind="product",
                merchant_id=self.merchant_id,
                product_ref=str(args["product_id"]),
            )
        elif kind == "checkout":
            reference = str(args["preparation_ref"])
            preparation = self._require_owned_preparation(reference).prepared
            if preparation is None:
                raise CommerceProviderError(
                    "STALE_PREPARATION", "Stored checkout reference is unavailable."
                )
            request = InspectRequest(
                kind="checkout",
                merchant_id=self.merchant_id,
                checkout_ref=preparation.checkout_ref,
            )
        elif kind == "shipment":
            order_ref = str(args["local_order_ref"])
            order = self._require_owned_order(order_ref)
            shipment_id = str(args["shipment_id"])
            if shipment_id not in order.lifecycle.shipments:
                raise LookupError("Shipment is not a child of the local order.")
            request = InspectRequest(
                kind="shipment",
                merchant_id=self.merchant_id,
                shipment_ref=shipment_id,
            )
        else:
            order_ref = str(args["local_order_ref"])
            order = self._require_owned_order(order_ref)
            request = InspectRequest(
                kind=cast(InspectionKind, kind),
                merchant_id=self.merchant_id,
                order_ref=order.order_id,
            )
        return self.inspect(request)

    def prepare_public(self, args: dict[str, Any]) -> OrderPreparation:
        items = tuple(RequestedItem.model_validate(item) for item in args["items"])
        request_facts = {
            "merchant_id": self.merchant_id,
            "items": [item.model_dump(mode="json") for item in items],
            "promotion_code": args.get("promotion_code"),
        }
        return self.prepare_order(
            PrepareOrderRequest(
                idempotency_key=commerce_digest(request_facts),
                items=items,
                promotion_code=args.get("promotion_code"),
            )
        )

    def resolve_confirmation_preview(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        subject_id: str,
        session_id: str,
    ) -> Any:
        if subject_id != COMMERCE_LOCAL_SUBJECT_ID or not session_id:
            raise ValueError(
                "Commerce confirmation requires the local subject/session."
            )
        if tool_name == "commerce.place_order":
            return self._placement_confirmation(
                self._validated_place_preparation(args),
                session_id=session_id,
            )
        if tool_name == "commerce.apply_order_action":
            return self._action_confirmation(
                self._validated_action_preparation(args),
                session_id=session_id,
            )
        if tool_name != "commerce.prepare_order":
            raise ValueError("Unsupported commerce confirmation tool.")
        from .confirmation import (
            ConfirmationItem,
            PreparationIntentConfirmationPreview,
        )

        buyer = self._secret_record(self.buyer_profile_record_id)
        payment = self._secret_record(self.payment_token_record_id)
        items = tuple(RequestedItem.model_validate(item) for item in args["items"])
        return PreparationIntentConfirmationPreview(
            merchant=self.merchant_id,
            items=tuple(
                ConfirmationItem(
                    offer_id=item.offer_id,
                    variant_id=item.variant_id,
                    quantity=item.quantity,
                )
                for item in items
            ),
            promotion_code=args.get("promotion_code"),
            buyer_label=self._safe_label(buyer, "buyer_label"),
            destination_label=self._safe_label(buyer, "destination_label"),
            payment_label=self._safe_label(payment, "payment_label"),
            subject_id="local",
            session_id=session_id,
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5))
            .replace(microsecond=0)
            .isoformat(),
            consequence=(
                "Create or refresh one merchant checkout without placing an order "
                "or capturing payment."
            ),
        )

    def place_public(
        self,
        args: dict[str, Any],
        *,
        authorization_hash: str,
        caller_agent_id: str = "",
        caller_profile_id: str = "",
    ) -> OrderPlacement | CommerceHandoff:
        preparation = self._validated_place_preparation(args)
        self._refresh_preparation(preparation)
        self._resolve_placement_credentials(
            preparation,
            caller_agent_id=caller_agent_id,
            caller_profile_id=caller_profile_id,
        )
        request = PlaceOrderRequest(
            idempotency_key=commerce_digest(
                {
                    "subject_id": COMMERCE_LOCAL_SUBJECT_ID,
                    "preparation_ref": preparation.preparation_ref,
                    "preparation_digest": preparation.preparation_digest,
                }
            ),
            preparation_ref=preparation.preparation_ref,
            preparation_digest=preparation.preparation_digest,
        )
        return self.place_order(request, authorization_hash=authorization_hash)

    def prepare_action_public(
        self, args: dict[str, Any]
    ) -> OrderActionPreparation | CommerceHandoff:
        order_ref = str(args["local_order_ref"])
        self._require_owned_order(order_ref)
        request = PrepareOrderActionRequest(
            idempotency_key=commerce_digest(
                {
                    "subject_id": COMMERCE_LOCAL_SUBJECT_ID,
                    "local_order_ref": order_ref,
                    "order_revision": args["order_revision"],
                    "kind": args["kind"],
                    "line_item_ids": args.get("line_item_ids", ()),
                    "quantity": args.get("quantity"),
                    "reason": args.get("reason"),
                    "refund_method": args.get("refund_method"),
                }
            ),
            order_ref=order_ref,
            order_revision=str(args["order_revision"]),
            kind=args["kind"],
            line_item_ids=tuple(args.get("line_item_ids", ())),
            quantity=args.get("quantity"),
            reason=args.get("reason"),
            refund_method=args.get("refund_method"),
        )
        current = self.inspect(
            InspectRequest(
                kind="order",
                merchant_id=self.merchant_id,
                order_ref=order_ref,
            )
        )
        if not isinstance(current, OrderInspection) or (
            current.revision != request.order_revision
        ):
            raise CommerceProviderError("STALE_REVISION", "Order revision is stale.")
        self._validate_action_items(request, current)
        preparation = self.prepare_action(request)
        if isinstance(preparation, CommerceHandoff):
            return self._sanitize_links(preparation)
        self._validate_action_preparation_response(request, current, preparation)
        preparation = self._sanitize_links(preparation)
        if self.order_store is not None:
            self.order_store.save_action_preparation(
                subject_id=COMMERCE_LOCAL_SUBJECT_ID,
                order_id=order_ref,
                prepared=preparation,
            )
        return preparation

    def apply_action_public(
        self,
        args: dict[str, Any],
        *,
        authorization_hash: str,
    ) -> OrderActionResult:
        preparation = self._validated_action_preparation(args)
        request = ApplyOrderActionRequest(
            idempotency_key=commerce_digest(
                {
                    "subject_id": COMMERCE_LOCAL_SUBJECT_ID,
                    "action_ref": preparation.action_ref,
                    "action_digest": preparation.action_digest,
                }
            ),
            action_ref=preparation.action_ref,
            order_ref=preparation.order_ref,
            action_digest=preparation.action_digest,
        )
        return self.apply_action(request, authorization_hash=authorization_hash)

    def prepare_order(self, request: PrepareOrderRequest) -> OrderPreparation:
        request_digest = commerce_digest(request.model_dump(mode="json"))
        attempt = self._reserve_preparation(request, request_digest)
        if attempt is not None and not attempt.created:
            recovered = self.recover_preparation(
                PreparationRecoveryLocator(
                    idempotency_key=attempt.attempt.idempotency_key
                )
            )
            if recovered is None:
                raise RuntimeError("Commerce preparation outcome requires recovery.")
            return self._validated_preparation(recovered, attempt)
        try:
            preparation = self.provider.prepare_order(request, self._provider_context())
        except CommerceOutcomeUnknown:
            recovered = self.recover_preparation(
                PreparationRecoveryLocator(idempotency_key=request.idempotency_key)
            )
            if recovered is None:
                self._finish_attempt(attempt, state="outcome_unknown")
                raise
            preparation = recovered
        return self._validated_preparation(preparation, attempt)

    def recover_preparation(
        self, locator: PreparationRecoveryLocator
    ) -> OrderPreparation | None:
        preparation = self.provider.recover_preparation(locator)
        return self._sanitize_links(preparation) if preparation is not None else None

    def place_order(
        self,
        request: PlaceOrderRequest,
        *,
        authorization_hash: str | None = None,
    ) -> OrderPlacement | CommerceHandoff:
        request_digest = commerce_digest(request.model_dump(mode="json"))
        attempt = self._reserve_placement(
            request, request_digest, authorization_hash=authorization_hash
        )
        order_store = self.order_store
        if attempt is not None and order_store is not None:
            submitting = order_store.begin_placement_attempt(
                subject_id=COMMERCE_LOCAL_SUBJECT_ID,
                attempt_id=attempt.attempt.attempt_id,
            )
            if submitting is None:
                recovery_request = request.model_copy(
                    update={"idempotency_key": attempt.attempt.idempotency_key}
                )
                recovered = self._recover_placement(recovery_request)
                if recovered is None:
                    raise CommerceOutcomeUnknown(
                        "Commerce placement is reserved or submitted and requires recovery."
                    )
                self._validate_placement_result(recovery_request, recovered, attempt)
                return self._placement_result(recovered, attempt)
        try:
            placement = self.provider.place_order(request)
        except CommerceOutcomeUnknown:
            recovered = self._recover_placement(request)
            if recovered is None:
                self._finish_attempt(attempt, state="outcome_unknown")
                raise
            placement = recovered
        self._validate_placement_result(request, placement, attempt)
        return self._placement_result(placement, attempt)

    def recover_placement(
        self, locator: PlacementRecoveryLocator
    ) -> OrderPlacement | None:
        placement = self.provider.recover_placement(locator)
        return self._sanitize_links(placement) if placement is not None else None

    def prepare_action(
        self, request: PrepareOrderActionRequest
    ) -> OrderActionPreparation | CommerceHandoff:
        return self.provider.prepare_action(request)

    def apply_action(
        self,
        request: ApplyOrderActionRequest,
        *,
        authorization_hash: str | None = None,
    ) -> OrderActionResult:
        request_digest = commerce_digest(request.model_dump(mode="json"))
        attempt = self._reserve_action(
            request,
            request_digest,
            authorization_hash=authorization_hash,
        )
        if attempt is not None and not attempt.created:
            if attempt.attempt.idempotency_key != request.idempotency_key:
                raise CommerceProviderError(
                    "ACTION_ALREADY_OPEN",
                    "A prior order action is still active.",
                )
        if attempt is not None and self.order_store is not None:
            submitting = self.order_store.begin_action_attempt(
                subject_id=COMMERCE_LOCAL_SUBJECT_ID,
                attempt_id=attempt.attempt.attempt_id,
            )
            if submitting is None:
                recovered = self._recover_action(request)
                if recovered is None:
                    raise CommerceOutcomeUnknown(
                        "Commerce action is reserved or submitted and requires recovery."
                    )
                self._validate_action_result(request, recovered, attempt)
                return self._action_result(recovered, attempt)
        try:
            result = self.provider.apply_action(request)
        except CommerceOutcomeUnknown:
            recovered = self._recover_action(request)
            if recovered is None:
                self._finish_attempt(attempt, state="outcome_unknown")
                raise
            result = recovered
        self._validate_action_result(request, result, attempt)
        return self._action_result(result, attempt)

    def recover_action(
        self, locator: ActionRecoveryLocator
    ) -> OrderActionResult | None:
        result = self.provider.recover_action(locator)
        return self._sanitize_links(result) if result is not None else None

    def _provider_context(self) -> ProviderOrderContext:
        if self.secret_service is None:
            raise RuntimeError("Commerce secret service is not available.")
        return ProviderOrderContext(
            merchant_id=self.merchant_id,
            provider_secret=self.secret_service.get_secret_sync(
                self.provider_secret_key, namespace="commerce"
            ),
            buyer_profile_record=self._secret_record(self.buyer_profile_record_id),
            payment_token_record=self._secret_record(self.payment_token_record_id),
        )

    def _validated_place_preparation(self, args: dict[str, Any]) -> OrderPreparation:
        preparation_ref = str(args["preparation_ref"])
        record = self._require_owned_preparation(preparation_ref)
        supplied = OrderPreparation.model_validate(args["preparation"])
        if (
            record.invalidated_at is not None
            or record.prepared is None
            or supplied != record.prepared
            or supplied.preparation_ref != preparation_ref
            or supplied.preparation_digest != args["preparation_digest"]
        ):
            raise CommerceProviderError(
                "STALE_PREPARATION",
                "Submitted preparation does not match the subject-owned preparation.",
            )
        if supplied.merchant.provider_id != self.merchant_id:
            raise CommerceProviderError(
                "MERCHANT_MISMATCH",
                "Prepared order does not match the configured merchant.",
            )
        if supplied.state != "prepared" or self._is_expired(supplied.expires_at):
            raise CommerceProviderError(
                "STALE_PREPARATION",
                "Commerce preparation is expired or not ready.",
            )
        return supplied

    def _refresh_preparation(self, preparation: OrderPreparation) -> None:
        refreshed = self.inspect(
            InspectRequest(
                kind="checkout",
                merchant_id=self.merchant_id,
                checkout_ref=preparation.checkout_ref,
            )
        )
        if not isinstance(refreshed, CheckoutInspection) or (
            refreshed.revision != preparation.checkout_revision
            or refreshed.items != preparation.items
            or refreshed.subtotal != preparation.totals.subtotal
            or refreshed.total != preparation.totals.total
            or refreshed.warnings != preparation.warnings
        ):
            raise CommerceProviderError(
                "STALE_PREPARATION",
                "Merchant checkout no longer matches the approved preparation.",
            )

    def _placement_confirmation(
        self, preparation: OrderPreparation, *, session_id: str
    ) -> Any:
        from .confirmation import (
            ConfirmationItem,
            ExactOrderConfirmationPreview,
            PolicyLink,
        )

        policy_links = tuple(
            PolicyLink(
                kind=cast(Literal["terms", "privacy", "shipping", "returns"], kind),
                url=url,
            )
            for kind in ("terms", "privacy", "shipping", "returns")
            if (url := preparation.links.get(kind))
        )
        return ExactOrderConfirmationPreview(
            merchant=preparation.merchant.display_name,
            seller=preparation.seller.display_name,
            preparation_ref=preparation.preparation_ref,
            items=tuple(
                ConfirmationItem(
                    offer_id=item.offer_id,
                    variant_id=item.variant_id,
                    quantity=item.quantity,
                    line_total_minor=(item.unit_price.amount_minor * item.quantity),
                    returnable=item.returnable,
                )
                for item in preparation.items
            ),
            discount_minor=preparation.totals.discount.amount_minor,
            tax_minor=preparation.totals.tax.amount_minor,
            shipping_minor=preparation.totals.shipping.amount_minor,
            fees_minor=preparation.totals.fees.amount_minor,
            total_minor=preparation.totals.total.amount_minor,
            currency=preparation.totals.total.currency,
            destination_label=preparation.destination.label,
            buyer_profile_digest=preparation.buyer.profile_digest,
            destination_digest=preparation.destination.destination_digest,
            payment_label=preparation.payment.label,
            payment_destination_digest=(preparation.payment.payment_destination_digest),
            recurring=preparation.recurring,
            warnings=preparation.warnings,
            policy_links=policy_links,
            checkout_revision=preparation.checkout_revision,
            expires_at=preparation.expires_at,
            preparation_digest=preparation.preparation_digest,
            subject_id=COMMERCE_LOCAL_SUBJECT_ID,
            session_id=session_id,
        )

    def _validated_action_preparation(
        self, args: dict[str, Any]
    ) -> OrderActionPreparation:
        supplied = OrderActionPreparation.model_validate(args["preparation"])
        if (
            supplied.action_ref != args["action_ref"]
            or supplied.action_digest != args["action_digest"]
            or not supplied.eligible
            or self._is_expired(supplied.expires_at)
        ):
            raise CommerceProviderError(
                "STALE_PREPARATION",
                "Order action preparation is stale.",
            )
        self._require_owned_order(supplied.order_ref)
        if self.order_store is not None:
            record = self.order_store.get_action_preparation(
                COMMERCE_LOCAL_SUBJECT_ID,
                supplied.action_ref,
            )
            if (
                record is None
                or record.invalidated_at is not None
                or record.prepared != supplied
            ):
                raise CommerceProviderError(
                    "STALE_PREPARATION",
                    "Order action preparation is not subject-owned.",
                )
        is_partial = supplied.kind in {"partial_cancel", "partial_return"}
        refreshed = self.prepare_action_public(
            {
                "local_order_ref": supplied.order_ref,
                "order_revision": supplied.order_revision,
                "kind": supplied.kind,
                "line_item_ids": (
                    tuple(item.line_item_id for item in supplied.affected_items)
                    if is_partial
                    else ()
                ),
                "quantity": (
                    supplied.affected_items[0].quantity
                    if is_partial and supplied.affected_items
                    else None
                ),
                "reason": supplied.reason,
                "refund_method": supplied.refund_method,
            }
        )
        if refreshed != supplied:
            raise CommerceProviderError(
                "STALE_PREPARATION",
                "Merchant action terms changed after review.",
            )
        return supplied

    @staticmethod
    def _validate_action_items(
        request: PrepareOrderActionRequest,
        inspection: OrderInspection,
    ) -> None:
        if not request.line_item_ids:
            return
        items = {
            item.line_item_id: item
            for item in inspection.items
            if item.line_item_id is not None
        }
        if any(line_item_id not in items for line_item_id in request.line_item_ids):
            raise CommerceProviderError(
                "ACTION_INELIGIBLE",
                "Order action contains an unknown line item.",
            )
        if request.quantity is not None and any(
            request.quantity > items[line_item_id].quantity
            for line_item_id in request.line_item_ids
        ):
            raise CommerceProviderError(
                "ACTION_INELIGIBLE",
                "Order action quantity exceeds the ordered quantity.",
            )

    @staticmethod
    def _validate_action_preparation_response(
        request: PrepareOrderActionRequest,
        inspection: OrderInspection,
        preparation: OrderActionPreparation,
    ) -> None:
        expected_items = (
            tuple(
                (line_item_id, request.quantity or 1)
                for line_item_id in request.line_item_ids
            )
            if request.line_item_ids
            else tuple(
                (item.line_item_id, item.quantity)
                for item in inspection.items
                if item.line_item_id is not None
            )
        )
        returned_items = tuple(
            (item.line_item_id, item.quantity) for item in preparation.affected_items
        )
        is_return = request.kind in {"return", "partial_return"}
        return_logistics = (
            preparation.return_destination,
            preparation.return_method,
            preparation.shipment_responsibility,
            preparation.deadlines or None,
        )
        return_logistics_match = (
            all(value is not None for value in return_logistics)
            if is_return
            else all(value is None for value in return_logistics)
        )
        refund_destination_matches = (preparation.refund_destination is not None) == (
            request.refund_method is not None
        )
        if (
            preparation.order_ref != request.order_ref
            or preparation.order_revision != request.order_revision
            or preparation.kind != request.kind
            or returned_items != expected_items
            or preparation.reason != request.reason
            or preparation.refund_method != request.refund_method
            or preparation.fees.currency != inspection.total.currency
            or preparation.refund.currency != inspection.total.currency
            or not return_logistics_match
            or not refund_destination_matches
        ):
            raise CommerceProviderError(
                "INVALID_RESPONSE",
                "Provider action preparation does not match the requested order action.",
            )

    def _action_confirmation(
        self,
        preparation: OrderActionPreparation,
        *,
        session_id: str,
    ) -> Any:
        from .confirmation import ActionConfirmationItem, OrderActionConfirmationPreview

        destination = preparation.refund_destination
        return OrderActionConfirmationPreview(
            order_id=preparation.order_ref,
            order_revision=preparation.order_revision,
            action_kind=preparation.kind,
            affected_items=tuple(
                ActionConfirmationItem(
                    line_item_id=item.line_item_id,
                    quantity=item.quantity,
                )
                for item in preparation.affected_items
            ),
            fees_minor=preparation.fees.amount_minor,
            refund_minor=preparation.refund.amount_minor,
            currency=preparation.refund.currency,
            refund_method=preparation.refund_method,
            refund_destination_digest=(
                destination.destination_digest if destination is not None else None
            ),
            refund_destination_label=(
                destination.label if destination is not None else None
            ),
            deadlines=preparation.deadlines,
            consequence=preparation.consequence,
            action_revision=preparation.action_revision,
            expires_at=preparation.expires_at,
            action_digest=preparation.action_digest,
            subject_id=COMMERCE_LOCAL_SUBJECT_ID,
            session_id=session_id,
        )

    @staticmethod
    def _is_expired(expires_at: str) -> bool:
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry <= datetime.now(timezone.utc)

    def _resolve_placement_credentials(
        self,
        preparation: OrderPreparation,
        *,
        caller_agent_id: str,
        caller_profile_id: str,
    ) -> None:
        context = self._provider_context()
        expected_labels = (
            (context.buyer_profile_record, "buyer_label", preparation.buyer.label),
            (
                context.buyer_profile_record,
                "destination_label",
                preparation.destination.label,
            ),
            (
                context.payment_token_record,
                "payment_label",
                preparation.payment.label,
            ),
        )
        if any(
            self._safe_label(record, field) != expected
            for record, field, expected in expected_labels
        ):
            raise CommerceProviderError(
                "STALE_PREPARATION",
                "Configured commerce credentials no longer match the preparation.",
            )
        for credential_id in (
            self.provider_secret_key,
            self.buyer_profile_record_id,
            self.payment_token_record_id,
        ):
            record_credential_access_event(
                resolve_credential_ref(
                    credential_id,
                    scope_kind="tool_family",
                    scope_id="commerce",
                    source_kind="secret_ref",
                ),
                access_site="tools.commerce.place_order",
                caller_agent_id=caller_agent_id,
                caller_profile_id=caller_profile_id,
                decision="allowed",
                audit_log=self.credential_audit_log,
            )

    def _secret_record(self, record_id: str) -> dict[str, Any]:
        if self.secret_service is None:
            raise RuntimeError("Commerce secret service is not available.")
        raw = self.secret_service.get_secret_sync(record_id, namespace="commerce")
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError("Commerce secret record must be a JSON object.")
        return decoded

    @staticmethod
    def _safe_label(record: dict[str, Any], field: str) -> str:
        label = str(record.get(field, "") or "").strip()
        if not label:
            raise ValueError(f"Commerce secret record is missing {field}.")
        return label


def build_commerce_runtime(
    *,
    provider: CommerceProvider | None,
    config: CommerceToolRuntimeConfig | dict[str, Any] | None,
    order_store: Any | None = None,
    secret_service: Any | None = None,
) -> CommerceRuntime | None:
    resolved = coerce_commerce_tool_runtime_config(config)
    if provider is None or resolved is None or not resolved.enabled:
        return None
    return CommerceRuntime(
        provider=provider,
        base_url=resolved.base_url,
        merchant_id=resolved.merchant_id,
        provider_secret_key=resolved.provider_secret_key,
        buyer_profile_record_id=resolved.buyer_profile_record_id,
        payment_token_record_id=resolved.payment_token_record_id,
        order_store=order_store,
        secret_service=secret_service,
    )


__all__ = [
    "CommerceOrderStore",
    "CommerceRuntime",
    "build_commerce_runtime",
]
