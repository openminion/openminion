"""Shared direct-injection commerce runtime fixture."""

from __future__ import annotations

import json

from openminion.modules.commerce.fixture import FixtureCommerceProvider
from openminion.modules.commerce.runtime import CommerceRuntime


class FixtureSecretService:
    def __init__(self) -> None:
        self.records = {
            "provider-secret": "fixture-provider-secret",
            "buyer-profile": json.dumps(
                {"name": "Fixture Buyer", "destination": "fixture-destination"}
            ),
            "payment-token": json.dumps({"token": "fixture-payment-token"}),
        }

    def get_secret_sync(self, key: str, *, namespace: str = "default") -> str:
        assert namespace == "commerce"
        return self.records[key]


def build_fixture_commerce_runtime() -> tuple[
    CommerceRuntime, FixtureCommerceProvider
]:
    provider = FixtureCommerceProvider()
    runtime = CommerceRuntime(
        provider=provider,
        merchant_id="merchant-fixture",
        provider_secret_key="provider-secret",
        buyer_profile_record_id="buyer-profile",
        payment_token_record_id="payment-token",
        secret_service=FixtureSecretService(),
    )
    return runtime, provider


__all__ = ["FixtureSecretService", "build_fixture_commerce_runtime"]
