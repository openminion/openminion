"""Direct commerce provider, persistence, and secret-service composition."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from openminion.modules.runtime.credentials import (
    CredentialAuditLog,
    InMemoryCredentialAuditLog,
    record_credential_access_event,
    resolve_credential_ref,
)

from .constants import COMMERCE_LOCAL_SUBJECT_ID
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
    OrderPlacement,
    OrderPreparation,
    PlaceOrderRequest,
    PlacementRecoveryLocator,
    PrepareOrderActionRequest,
    PrepareOrderRequest,
    PreparationRecoveryLocator,
    ProviderOrderContext,
    RequestedItem,
    build_commerce_handoff,
    safe_commerce_links,
)


class CommerceOrderStore(Protocol):
    def get_preparation(self, subject_id: str, preparation_id: str) -> Any: ...

    def get_order(self, subject_id: str, order_id: str) -> Any: ...

    def reserve_preparation(self, **kwargs: Any) -> Any: ...

    def reserve_placement(self, **kwargs: Any) -> Any: ...

    def reserve_action(self, **kwargs: Any) -> Any: ...

    def save_preparation(self, **kwargs: Any) -> Any: ...

    def save_order(self, **kwargs: Any) -> Any: ...

    def update_order_lifecycle(self, **kwargs: Any) -> Any: ...

    def finish_attempt(self, **kwargs: Any) -> Any: ...

    def invalidate_preparation(self, **kwargs: Any) -> Any: ...

    def begin_placement_attempt(self, **kwargs: Any) -> Any: ...


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class CommerceRuntime:
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
        self.credential_audit_log = (
            credential_audit_log or InMemoryCredentialAuditLog()
        )
        self._checkout_refs: dict[str, str] = {}

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
            self._require_owned_preparation(reference)
            request = InspectRequest(
                kind="checkout",
                merchant_id=self.merchant_id,
                checkout_ref=self._checkout_refs.get(reference, reference),
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
                kind=kind,
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
                idempotency_key=_digest(request_facts),
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
            raise ValueError("Commerce confirmation requires the local subject/session.")
        if tool_name == "commerce.place_order":
            return self._placement_confirmation(
                self._validated_place_preparation(args),
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
            expires_at=(
                datetime.now(timezone.utc) + timedelta(minutes=5)
            ).replace(microsecond=0).isoformat(),
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
            idempotency_key=_digest(
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

    def prepare_order(self, request: PrepareOrderRequest) -> OrderPreparation:
        request_digest = _digest(request.model_dump(mode="json"))
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
            preparation = self.recover_preparation(
                PreparationRecoveryLocator(idempotency_key=request.idempotency_key)
            )
            if preparation is None:
                self._finish_attempt(attempt, state="outcome_unknown")
                raise
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
        request_digest = _digest(request.model_dump(mode="json"))
        attempt = self._reserve_placement(
            request, request_digest, authorization_hash=authorization_hash
        )
        if attempt is not None:
            submitting = self.order_store.begin_placement_attempt(
                subject_id=COMMERCE_LOCAL_SUBJECT_ID,
                attempt_id=attempt.attempt.attempt_id,
            )
            if submitting is None:
                recovered = self.provider.recover_placement(
                    PlacementRecoveryLocator(
                        idempotency_key=attempt.attempt.idempotency_key,
                        preparation_digest=request.preparation_digest,
                    )
                )
                if recovered is None:
                    raise CommerceOutcomeUnknown(
                        "Commerce placement is reserved or submitted and requires recovery."
                    )
                return self._placement_result(recovered)
        try:
            placement = self.provider.place_order(request)
        except CommerceOutcomeUnknown:
            placement = self._recover_placement(request)
            if placement is None:
                self._finish_attempt(attempt, state="outcome_unknown")
                raise
        return self._placement_result(placement, attempt)

    def recover_placement(
        self, locator: PlacementRecoveryLocator
    ) -> OrderPlacement | None:
        placement = self.provider.recover_placement(locator)
        return self._sanitize_links(placement) if placement is not None else None

    def prepare_action(
        self, request: PrepareOrderActionRequest
    ) -> OrderActionPreparation:
        return self.provider.prepare_action(request)

    def apply_action(self, request: ApplyOrderActionRequest) -> OrderActionResult:
        request_digest = _digest(request.model_dump(mode="json"))
        attempt = self._reserve_action(request, request_digest)
        if attempt is not None and not attempt.created:
            if attempt.attempt.idempotency_key != request.idempotency_key:
                raise CommerceProviderError(
                    "ACTION_ALREADY_OPEN",
                    "A prior order action is still active.",
                )
            recovered = self._recover_action(request)
            if recovered is None:
                raise RuntimeError("Commerce action outcome requires recovery.")
            return self._sanitize_links(recovered)
        try:
            result = self.provider.apply_action(request)
        except CommerceOutcomeUnknown:
            result = self._recover_action(request)
            if result is None:
                self._finish_attempt(attempt, state="outcome_unknown")
                raise
        result = self._sanitize_links(result)
        if self.order_store is not None:
            self.order_store.update_order_lifecycle(
                subject_id=COMMERCE_LOCAL_SUBJECT_ID,
                order_id=result.order_ref,
                lifecycle=result.lifecycle,
            )
            self._finish_attempt(
                attempt,
                state="succeeded" if result.state == "completed" else "failed",
                response_digest=_digest(result.model_dump(mode="json")),
                provider_reference_digest=_digest(result.action_ref),
            )
        return result

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

    def _validated_place_preparation(
        self, args: dict[str, Any]
    ) -> OrderPreparation:
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
            PolicyLink(kind=kind, url=url)
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
                    line_total_minor=(
                        item.unit_price.amount_minor * item.quantity
                    ),
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
            payment_destination_digest=(
                preparation.payment.payment_destination_digest
            ),
            recurring=preparation.recurring,
            warnings=preparation.warnings,
            policy_links=policy_links,
            checkout_revision=preparation.checkout_revision,
            expires_at=preparation.expires_at,
            preparation_digest=preparation.preparation_digest,
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

    def _require_owned_preparation(self, preparation_ref: str) -> Any:
        if self.order_store is None:
            raise PermissionError("Commerce preparation ownership is unavailable.")
        record = self.order_store.get_preparation(
            COMMERCE_LOCAL_SUBJECT_ID, preparation_ref
        )
        if record is None:
            raise PermissionError("Commerce preparation is not owned by the subject.")
        return record

    def _require_owned_order(self, order_ref: str) -> Any:
        if self.order_store is None:
            raise PermissionError("Commerce order ownership is unavailable.")
        record = self.order_store.get_order(COMMERCE_LOCAL_SUBJECT_ID, order_ref)
        if record is None:
            raise PermissionError("Commerce order is not owned by the subject.")
        return record

    def _reserve_preparation(self, request: PrepareOrderRequest, digest: str) -> Any:
        if self.order_store is None:
            return None
        return self.order_store.reserve_preparation(
            subject_id=COMMERCE_LOCAL_SUBJECT_ID,
            target_id=_digest(
                {
                    "merchant_id": self.merchant_id,
                    "items": [item.model_dump(mode="json") for item in request.items],
                    "promotion_code": request.promotion_code,
                }
            ),
            idempotency_key=request.idempotency_key,
            request_digest=digest,
        )

    def _reserve_placement(
        self,
        request: PlaceOrderRequest,
        digest: str,
        *,
        authorization_hash: str | None,
    ) -> Any:
        if self.order_store is None:
            return None
        preparation = self._require_owned_preparation(request.preparation_ref)
        if preparation.invalidated_at is not None:
            raise CommerceProviderError(
                "STALE_PREPARATION",
                "Commerce preparation was invalidated by user takeover.",
            )
        return self.order_store.reserve_placement(
            subject_id=COMMERCE_LOCAL_SUBJECT_ID,
            preparation_id=request.preparation_ref,
            idempotency_key=request.idempotency_key,
            request_digest=digest,
            authorization_hash=authorization_hash,
        )

    def _reserve_action(self, request: ApplyOrderActionRequest, digest: str) -> Any:
        if self.order_store is None:
            return None
        return self.order_store.reserve_action(
            subject_id=COMMERCE_LOCAL_SUBJECT_ID,
            order_id=request.order_ref,
            operation="apply",
            idempotency_key=request.idempotency_key,
            request_digest=digest,
        )

    def _persist_preparation(self, preparation: OrderPreparation, attempt: Any) -> None:
        if self.order_store is None:
            return
        from .storage.models import PreparationPayload

        self.order_store.save_preparation(
            subject_id=COMMERCE_LOCAL_SUBJECT_ID,
            preparation_id=preparation.preparation_ref,
            payload=PreparationPayload(
                merchant=preparation.merchant,
                seller=preparation.seller,
                items=preparation.items,
                buyer=preparation.buyer,
                destination=preparation.destination,
                payment=preparation.payment,
                total=preparation.totals.total,
            ),
            prepared=preparation,
            preparation_attempt_id=attempt.attempt.attempt_id if attempt else None,
        )

    def _validated_preparation(
        self, preparation: OrderPreparation, attempt: Any
    ) -> OrderPreparation:
        if preparation.merchant.provider_id != self.merchant_id:
            self._finish_attempt(attempt, state="failed")
            raise CommerceProviderError(
                "MERCHANT_MISMATCH",
                "Provider response does not match the configured merchant.",
            )
        preparation = self._sanitize_links(preparation)
        self._persist_preparation(preparation, attempt)
        self._checkout_refs[preparation.preparation_ref] = preparation.checkout_ref
        self._finish_attempt(
            attempt,
            state="succeeded",
            response_digest=preparation.preparation_digest,
            provider_reference_digest=_digest(preparation.preparation_ref),
        )
        return preparation

    def _persist_placement(self, placement: OrderPlacement, attempt: Any) -> None:
        if self.order_store is None:
            return
        if placement.order_ref is not None:
            self.order_store.save_order(
                subject_id=COMMERCE_LOCAL_SUBJECT_ID,
                order_id=placement.order_ref,
                preparation_id=placement.preparation_ref,
                provider_order_digest=_digest(placement.order_ref),
                lifecycle=placement.lifecycle,
                placement_attempt_id=attempt.attempt.attempt_id if attempt else None,
            )
        self._finish_attempt(
            attempt,
            state=(
                "succeeded"
                if placement.state == "succeeded"
                else "outcome_unknown"
                if placement.state == "outcome_unknown"
                else "failed"
            ),
            response_digest=_digest(placement.model_dump(mode="json")),
            provider_reference_digest=(
                _digest(placement.order_ref) if placement.order_ref else None
            ),
        )

    def _placement_result(
        self, placement: OrderPlacement, attempt: Any = None
    ) -> OrderPlacement | CommerceHandoff:
        placement = self._sanitize_links(placement)
        self._persist_placement(placement, attempt)
        if placement.state != "action_required":
            return placement
        if self.order_store is not None:
            self.order_store.invalidate_preparation(
                subject_id=COMMERCE_LOCAL_SUBJECT_ID,
                preparation_id=placement.preparation_ref,
            )
        return build_commerce_handoff(
            reason_code="authentication_required",
            configured_base_url=self.base_url,
            candidate_url=placement.links.get("order"),
        )

    def _sanitize_links(self, value: Any) -> Any:
        links = getattr(value, "links", None)
        if not isinstance(links, dict):
            return value
        return value.model_copy(
            update={
                "links": safe_commerce_links(
                    links,
                    configured_base_url=self.base_url,
                )
            }
        )

    def _finish_attempt(self, attempt: Any, *, state: str, **facts: Any) -> None:
        if self.order_store is None or attempt is None:
            return
        self.order_store.finish_attempt(
            subject_id=COMMERCE_LOCAL_SUBJECT_ID,
            attempt_id=attempt.attempt.attempt_id,
            state=state,
            **facts,
        )

    def _recover_placement(self, request: PlaceOrderRequest) -> OrderPlacement | None:
        return self.provider.recover_placement(
            PlacementRecoveryLocator(
                idempotency_key=request.idempotency_key,
                preparation_digest=request.preparation_digest,
            )
        )

    def _recover_action(
        self, request: ApplyOrderActionRequest
    ) -> OrderActionResult | None:
        return self.provider.recover_action(
            ActionRecoveryLocator(
                idempotency_key=request.idempotency_key,
                action_digest=request.action_digest,
            )
        )


__all__ = ["CommerceOrderStore", "CommerceRuntime"]
