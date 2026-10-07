"""Persistence and recovery helpers for the commerce runtime."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from .constants import COMMERCE_LOCAL_SUBJECT_ID
from .identity import commerce_digest
from .provider import (
    ActionRecoveryLocator,
    ApplyOrderActionRequest,
    CommerceHandoff,
    CommerceProvider,
    CommerceProviderError,
    OrderActionResult,
    OrderPlacement,
    OrderPreparation,
    PlaceOrderRequest,
    PlacementRecoveryLocator,
    PrepareOrderRequest,
    build_commerce_handoff,
    safe_commerce_links,
)

_CommerceModelT = TypeVar("_CommerceModelT", bound=BaseModel)


class CommerceRuntimePersistenceMixin:
    provider: CommerceProvider
    base_url: str
    merchant_id: str
    order_store: Any | None
    _checkout_refs: dict[str, str]

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
            target_id=commerce_digest(
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

    def _reserve_action(
        self,
        request: ApplyOrderActionRequest,
        digest: str,
        *,
        authorization_hash: str | None,
    ) -> Any:
        if self.order_store is None:
            return None
        return self.order_store.reserve_action(
            subject_id=COMMERCE_LOCAL_SUBJECT_ID,
            order_id=request.order_ref,
            operation="apply",
            idempotency_key=request.idempotency_key,
            request_digest=digest,
            authorization_hash=authorization_hash,
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
            provider_reference_digest=commerce_digest(preparation.preparation_ref),
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
                provider_order_digest=commerce_digest(placement.order_ref),
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
            response_digest=commerce_digest(placement.model_dump(mode="json")),
            provider_reference_digest=(
                commerce_digest(placement.order_ref) if placement.order_ref else None
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

    def _action_result(
        self,
        result: OrderActionResult,
        attempt: Any,
    ) -> OrderActionResult:
        result = self._sanitize_links(result)
        if self.order_store is None:
            return result
        self.order_store.update_order_lifecycle(
            subject_id=COMMERCE_LOCAL_SUBJECT_ID,
            order_id=result.order_ref,
            lifecycle=result.lifecycle,
        )
        if result.state == "pending":
            return result
        self._finish_attempt(
            attempt,
            state=(
                "succeeded"
                if result.state == "completed"
                else "outcome_unknown"
                if result.state == "outcome_unknown"
                else "failed"
            ),
            response_digest=commerce_digest(result.model_dump(mode="json")),
            provider_reference_digest=commerce_digest(result.action_ref),
        )
        return result

    @staticmethod
    def _validate_action_result(
        request: ApplyOrderActionRequest,
        result: OrderActionResult,
    ) -> None:
        if (
            result.idempotency_key != request.idempotency_key
            or result.action_ref != request.action_ref
            or result.order_ref != request.order_ref
        ):
            raise CommerceProviderError(
                "INVALID_RESPONSE",
                "Provider action result does not match the requested order action.",
            )

    def _sanitize_links(self, value: _CommerceModelT) -> _CommerceModelT:
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
