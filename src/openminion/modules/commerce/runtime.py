"""Direct commerce provider, persistence, and secret-service composition."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from .constants import COMMERCE_LOCAL_SUBJECT_ID
from .provider import (
    ActionRecoveryLocator,
    ApplyOrderActionRequest,
    CommerceInspection,
    CommerceOutcomeUnknown,
    CommerceProvider,
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
)


class CommerceOrderStore(Protocol):
    def reserve_preparation(self, **kwargs: Any) -> Any: ...

    def reserve_placement(self, **kwargs: Any) -> Any: ...

    def reserve_action(self, **kwargs: Any) -> Any: ...

    def save_preparation(self, **kwargs: Any) -> Any: ...

    def save_order(self, **kwargs: Any) -> Any: ...

    def update_order_lifecycle(self, **kwargs: Any) -> Any: ...

    def finish_attempt(self, **kwargs: Any) -> Any: ...


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
        merchant_id: str,
        provider_secret_key: str,
        buyer_profile_record_id: str,
        payment_token_record_id: str,
        order_store: CommerceOrderStore | None = None,
        secret_service: Any | None = None,
    ) -> None:
        self.provider = provider
        self.merchant_id = merchant_id
        self.provider_secret_key = provider_secret_key
        self.buyer_profile_record_id = buyer_profile_record_id
        self.payment_token_record_id = payment_token_record_id
        self.order_store = order_store
        self.secret_service = secret_service

    def inspect(self, request: InspectRequest) -> CommerceInspection:
        return self.provider.inspect(request)

    def prepare_order(self, request: PrepareOrderRequest) -> OrderPreparation:
        request_digest = _digest(request.model_dump(mode="json"))
        attempt = self._reserve_preparation(request, request_digest)
        if attempt is not None and not attempt.created:
            recovered = self.recover_preparation(
                PreparationRecoveryLocator(idempotency_key=request.idempotency_key)
            )
            if recovered is None:
                raise RuntimeError("Commerce preparation outcome requires recovery.")
            return recovered
        try:
            preparation = self.provider.prepare_order(request, self._provider_context())
        except CommerceOutcomeUnknown:
            preparation = self.recover_preparation(
                PreparationRecoveryLocator(idempotency_key=request.idempotency_key)
            )
            if preparation is None:
                self._finish_attempt(attempt, state="outcome_unknown")
                raise
        self._persist_preparation(preparation, attempt)
        return preparation

    def recover_preparation(
        self, locator: PreparationRecoveryLocator
    ) -> OrderPreparation | None:
        return self.provider.recover_preparation(locator)

    def place_order(self, request: PlaceOrderRequest) -> OrderPlacement:
        request_digest = _digest(request.model_dump(mode="json"))
        attempt = self._reserve_placement(request, request_digest)
        if attempt is not None and not attempt.created:
            recovered = self._recover_placement(request)
            if recovered is None:
                raise RuntimeError("Commerce placement outcome requires recovery.")
            return recovered
        try:
            placement = self.provider.place_order(request)
        except CommerceOutcomeUnknown:
            placement = self._recover_placement(request)
            if placement is None:
                self._finish_attempt(attempt, state="outcome_unknown")
                raise
        self._persist_placement(placement, attempt)
        return placement

    def recover_placement(
        self, locator: PlacementRecoveryLocator
    ) -> OrderPlacement | None:
        return self.provider.recover_placement(locator)

    def prepare_action(
        self, request: PrepareOrderActionRequest
    ) -> OrderActionPreparation:
        return self.provider.prepare_action(request)

    def apply_action(self, request: ApplyOrderActionRequest) -> OrderActionResult:
        request_digest = _digest(request.model_dump(mode="json"))
        attempt = self._reserve_action(request, request_digest)
        if attempt is not None and not attempt.created:
            recovered = self._recover_action(request)
            if recovered is None:
                raise RuntimeError("Commerce action outcome requires recovery.")
            return recovered
        try:
            result = self.provider.apply_action(request)
        except CommerceOutcomeUnknown:
            result = self._recover_action(request)
            if result is None:
                self._finish_attempt(attempt, state="outcome_unknown")
                raise
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
        return self.provider.recover_action(locator)

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

    def _secret_record(self, record_id: str) -> dict[str, Any]:
        raw = self.secret_service.get_secret_sync(record_id, namespace="commerce")
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError("Commerce secret record must be a JSON object.")
        return decoded

    def _reserve_preparation(self, request: PrepareOrderRequest, digest: str) -> Any:
        if self.order_store is None:
            return None
        return self.order_store.reserve_preparation(
            subject_id=COMMERCE_LOCAL_SUBJECT_ID,
            target_id=f"{self.merchant_id}:{_digest([item.model_dump(mode='json') for item in request.items])}",
            idempotency_key=request.idempotency_key,
            request_digest=digest,
        )

    def _reserve_placement(self, request: PlaceOrderRequest, digest: str) -> Any:
        if self.order_store is None:
            return None
        return self.order_store.reserve_placement(
            subject_id=COMMERCE_LOCAL_SUBJECT_ID,
            preparation_id=request.preparation_ref,
            idempotency_key=request.idempotency_key,
            request_digest=digest,
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
            preparation_attempt_id=attempt.attempt.attempt_id if attempt else None,
        )
        self._finish_attempt(
            attempt,
            state="succeeded",
            response_digest=preparation.preparation_digest,
            provider_reference_digest=_digest(preparation.preparation_ref),
        )

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
            state="succeeded" if placement.state == "succeeded" else "failed",
            response_digest=_digest(placement.model_dump(mode="json")),
            provider_reference_digest=(
                _digest(placement.order_ref) if placement.order_ref else None
            ),
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
