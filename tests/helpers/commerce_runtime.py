"""Shared direct-injection commerce runtime fixture."""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import replace
from typing import Any

from openminion.tools.commerce.fixture import FixtureCommerceProvider
from openminion.tools.commerce.runtime import CommerceRuntime
from openminion.tools.commerce.storage import SQLiteCommerceOrderStore


def build_fixture_api_runtime(
    config_path: str | None,
    *,
    home_root: str | None = None,
    data_root: str | None = None,
    logging_mode: str = "interactive",
    phase: int = 5,
    exposure_session_id: str = "",
) -> Any:
    """Build the ordinary API runtime with a process-local fixture commerce family."""
    from openminion.api.runtime import APIRuntime
    from openminion.modules.policy.runtime.action_policy import derive_tool_risk_spec
    from openminion.modules.tool.framework import derive_manifest, derive_tool_specs
    from openminion.tools.commerce.family import COMMERCE_FAMILY

    if phase not in {0, 2, 3, 5}:
        raise ValueError("commerce fixture phase must be 0, 2, 3, or 5")
    runtime = APIRuntime.from_config_path(
        config_path,
        home_root=home_root,
        data_root=data_root,
        logging_mode=logging_mode,
    )
    data_path = Path(data_root or runtime.data_root)
    commerce_runtime, provider = build_fixture_commerce_runtime(
        store_path=data_path / "commerce" / "commerce.db"
    )
    runtime.tool_resources = {"commerce": commerce_runtime}
    runtime._commerce_fixture_provider = provider
    family = replace(
        COMMERCE_FAMILY,
        tools=COMMERCE_FAMILY.tools[:phase],
        exposure_profiles=(COMMERCE_FAMILY.exposure_profiles if phase == 5 else ()),
    )
    for profile in family.exposure_profiles:
        runtime.tools.exposure_service.register_profiles((profile,))
    for spec in derive_tool_specs(family):
        runtime.tools.add(spec)
        runtime.action_policy.register_risk(
            spec.name,
            derive_tool_risk_spec(tool_name=spec.name, tool=spec),
        )
    binding_manager = runtime.tools._binding_manager()
    visible = binding_manager.model_provider_specs(set(runtime.tools.list()))
    if not any(spec.name.startswith("commerce.") for spec in visible):
        binding_manager.register_manifest(derive_manifest(family))
        binding_manager.set_runtime_tool_schemas(
            {spec.name: spec.parameters for spec in runtime.tools.provider_specs()}
        )
    if phase == 5 and exposure_session_id:
        session_ids = (
            exposure_session_id,
            f"{exposure_session_id}::conv:focus-{exposure_session_id}",
        )
        for session_id in session_ids:
            runtime.activate_tool_profile(
                "commerce_order_care",
                session_id=session_id,
                approved=True,
                activation_reason="commerce Focus fixture",
                approved_by="e2e",
                policy_source="commerce-focus-runner",
            )
    inventory = {name for name in runtime.tools.list() if name.startswith("commerce.")}
    if len(inventory) != phase:
        runtime.close()
        raise RuntimeError(
            f"commerce fixture phase {phase} exposed {len(inventory)} tools"
        )
    return runtime


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
) -> tuple[CommerceRuntime, FixtureCommerceProvider]:
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


__all__ = [
    "FixtureSecretService",
    "build_fixture_api_runtime",
    "build_fixture_commerce_runtime",
]
