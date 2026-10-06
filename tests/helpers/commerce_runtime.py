"""Shared direct-injection commerce runtime fixture."""

from __future__ import annotations

import json
from pathlib import Path

from openminion.modules.commerce.fixture import FixtureCommerceProvider
from openminion.modules.commerce.runtime import CommerceRuntime
from openminion.modules.commerce.storage import SQLiteCommerceOrderStore


class FixtureSecretService:
    def __init__(self) -> None:
        self.records = {
            "provider-secret": "fixture-provider-secret",
            "buyer-profile": json.dumps(
                {
                    "name": "Fixture Buyer",
                    "destination": "fixture-destination",
                    "buyer_label": "Fixture buyer",
                    "destination_label": "Home ending 42",
                }
            ),
            "payment-token": json.dumps(
                {
                    "token": "fixture-payment-token",
                    "payment_label": "Visa ending 4242",
                }
            ),
        }

    def get_secret_sync(self, key: str, *, namespace: str = "default") -> str:
        assert namespace == "commerce"
        return self.records[key]


def build_fixture_commerce_runtime(
    *, store_path: Path | None = None
) -> tuple[
    CommerceRuntime, FixtureCommerceProvider
]:
    provider = FixtureCommerceProvider()
    runtime = CommerceRuntime(
        provider=provider,
        base_url="https://fixture.invalid",
        merchant_id="merchant-fixture",
        provider_secret_key="provider-secret",
        buyer_profile_record_id="buyer-profile",
        payment_token_record_id="payment-token",
        order_store=(
            SQLiteCommerceOrderStore(store_path) if store_path is not None else None
        ),
        secret_service=FixtureSecretService(),
    )
    return runtime, provider


__all__ = ["FixtureSecretService", "build_fixture_commerce_runtime"]
