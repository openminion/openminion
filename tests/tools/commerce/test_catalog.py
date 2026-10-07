from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openminion.tools.commerce.config import CommerceToolRuntimeConfig
from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.tools.commerce.provider import CommerceProviderError
from openminion.modules.tool.runtime.policy_defaults import DEFAULT_POLICY
from openminion.tools.commerce.models import MerchantIdentity
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.modules.tool.plugin_api import stable_invocation_hash
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.tools.commerce import ALL_COMMERCE_TOOLS, REGISTRAR
from openminion.tools.commerce.family import COMMERCE_FAMILY
from openminion.tools.commerce.plugin import CommerceInspectArgs
from tests.helpers.commerce_runtime import build_fixture_commerce_runtime


def _config(
    *,
    enabled: bool,
    writes_enabled: bool = False,
    order_actions_enabled: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(
            tools=SimpleNamespace(
                commerce=CommerceToolRuntimeConfig(
                    enabled=enabled,
                    provider="fixture",
                    base_url="https://fixture.invalid",
                    merchant_id="merchant-fixture",
                    provider_secret_key="provider-secret",
                    buyer_profile_record_id="buyer-profile",
                    payment_token_record_id="payment-token",
                    writes_enabled=writes_enabled,
                    order_actions_enabled=order_actions_enabled,
                )
            )
        ),
        mcp_servers=None,
        tool_selection=None,
    )


def _bootstrap(
    tmp_path,
    *,
    enabled: bool,
    writes_enabled: bool = False,
    order_actions_enabled: bool = False,
):
    return build_runtime_bootstrap(
        config=_config(
            enabled=enabled,
            writes_enabled=writes_enabled,
            order_actions_enabled=order_actions_enabled,
        ),
        workspace_root=tmp_path,
        run_root=tmp_path / "run",
        strict=False,
    )


def _commerce_names(bootstrap) -> set[str]:
    return {name for name in bootstrap.registry.list() if name.startswith("commerce.")}


def test_default_policy_allows_commerce_family() -> None:
    assert "commerce." in DEFAULT_POLICY["tools"]["allow_prefix"]


def _prepare_args() -> dict[str, object]:
    return {"items": [{"offer_id": "offer-1", "variant_id": "standard", "quantity": 1}]}


def _prepare_command(args: dict[str, object]) -> dict[str, object]:
    return {
        "tool_name": "commerce.prepare_order",
        "args": args,
        "inputs": {
            "confirmation_source": "policy_replay",
            "confirmation_grant_id": "grant-1",
        },
    }


class _GrantPolicy:
    def __init__(self, *, allowed_hash: str | None) -> None:
        self.allowed_hash = allowed_hash
        self.consumed = False

    def mode(self) -> str:
        return "enforce"

    def resolve_matching_active_grant_for_use(self, **criteria):
        if self.consumed or criteria["invocation_hash"] != self.allowed_hash:
            return None
        self.consumed = True
        return SimpleNamespace(approval_id="approval-1", grant_id="grant-1")


def _adapter(tmp_path, runtime, policy_ctl) -> ToolAdapter:
    bootstrap = _bootstrap(tmp_path, enabled=True)
    return ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=bootstrap.registry,
        tool_resources={"commerce": runtime},
        policy_ctl=policy_ctl,
        policy={"tools": {"allow_exact": list(ALL_COMMERCE_TOOLS)}},
    )


def test_commerce_catalog_exposes_zero_disabled_and_exactly_two_enabled(
    tmp_path,
) -> None:
    disabled = _bootstrap(tmp_path / "disabled", enabled=False)
    enabled = _bootstrap(tmp_path / "enabled", enabled=True)

    assert _commerce_names(disabled) == set()
    assert _commerce_names(enabled) == {
        "commerce.inspect",
        "commerce.prepare_order",
    }
    assert enabled.contract_drift_report is not None
    assert enabled.contract_drift_report.has_drift is False
    manifest = REGISTRAR.get_manifest(None)
    assert manifest is not None
    assert {item.model_tool_id for item in manifest.model_tools} == set(
        ALL_COMMERCE_TOOLS
    )
    assert all(not item.aliases for item in manifest.model_tools)


def test_commerce_catalog_exposes_three_write_and_five_action_tools(tmp_path) -> None:
    write_enabled = _bootstrap(
        tmp_path / "write",
        enabled=True,
        writes_enabled=True,
    )
    action_enabled = _bootstrap(
        tmp_path / "actions",
        enabled=True,
        writes_enabled=True,
        order_actions_enabled=True,
    )

    assert len(_commerce_names(write_enabled)) == 3
    assert _commerce_names(action_enabled) == set(ALL_COMMERCE_TOOLS)
    assert action_enabled.contract_drift_report is not None
    assert action_enabled.contract_drift_report.has_drift is False
    [profile] = COMMERCE_FAMILY.exposure_profiles
    assert profile.profile_id == "commerce_order_care"
    assert profile.tool_names == frozenset(ALL_COMMERCE_TOOLS)
    assert profile.risk.tier == "apply"
    assert profile.risk.requires_approval is True


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "product", "product_id": "product-1"},
        {"kind": "checkout", "preparation_ref": "preparation-1"},
        {"kind": "order", "local_order_ref": "order-1"},
        {
            "kind": "shipment",
            "local_order_ref": "order-1",
            "shipment_id": "shipment-1",
        },
        {"kind": "order_actions", "local_order_ref": "order-1"},
    ],
)
def test_inspect_contract_accepts_each_closed_kind(payload) -> None:
    assert CommerceInspectArgs.model_validate(payload).model_dump(
        exclude_none=True
    ) == (payload)


def test_inspect_rejects_wrong_kind_reference_without_provider_access(tmp_path) -> None:
    bootstrap = _bootstrap(tmp_path, enabled=True)
    runtime, provider = build_fixture_commerce_runtime()

    result = execute_tool_spec_call(
        tool=bootstrap.registry.get("commerce.inspect"),
        arguments={"kind": "order", "product_id": "product-1"},
        context=ToolExecutionContext(
            channel="test",
            target="test",
            session_id="session-1",
            subject_id="local",
            tool_resources={"commerce": runtime},
        ),
    )

    assert result.ok is False
    assert result.data["error_code"] == "invalid_arguments"
    assert provider.inspect_calls == []


def test_product_inspection_injects_configured_merchant(tmp_path) -> None:
    bootstrap = _bootstrap(tmp_path, enabled=True)
    runtime, provider = build_fixture_commerce_runtime()

    result = execute_tool_spec_call(
        tool=bootstrap.registry.get("commerce.inspect"),
        arguments={"kind": "product", "product_id": "product-1"},
        context=ToolExecutionContext(
            channel="test",
            target="test",
            session_id="session-1",
            subject_id="local",
            tool_resources={"commerce": runtime},
        ),
    )

    assert result.ok is True
    assert result.data["kind"] == "product"
    assert provider.inspect_calls[0].merchant_id == "merchant-fixture"


def test_private_inspection_rejects_copied_reference_before_provider(tmp_path) -> None:
    bootstrap = _bootstrap(tmp_path, enabled=True)
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )

    result = execute_tool_spec_call(
        tool=bootstrap.registry.get("commerce.inspect"),
        arguments={"kind": "order", "local_order_ref": "copied-order"},
        context=ToolExecutionContext(
            channel="test",
            target="test",
            session_id="session-1",
            subject_id="local",
            tool_resources={"commerce": runtime},
        ),
    )

    assert result.ok is False
    assert result.data == {
        "error_code": "POLICY_DENIED",
        "details": {"commerce_code": "ORDER_ACCESS_DENIED"},
    }
    assert provider.inspect_calls == []


@pytest.mark.parametrize("allowed_hash", [None, "wrong-hash"])
def test_prepare_denied_or_mismatched_approval_never_reaches_provider(
    tmp_path, allowed_hash
) -> None:
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    result = _adapter(
        tmp_path, runtime, _GrantPolicy(allowed_hash=allowed_hash)
    ).execute(
        command=_prepare_command(_prepare_args()),
        session_id="session-1",
        trace_id="trace-1",
    )

    assert result["error"]["code"] == "CONFIRM_REQUIRED"
    assert result["error"]["details"]["commerce_code"] == "CONFIRM_REQUIRED"
    assert provider.ledger == []


@pytest.mark.parametrize("items", ["not-json", "{}", "null", "[]"])
def test_invalid_encoded_items_do_not_consume_approval_or_call_provider(
    tmp_path, items
):
    runtime, provider = build_fixture_commerce_runtime()
    policy = _GrantPolicy(allowed_hash="unused")
    result = _adapter(tmp_path, runtime, policy).execute(
        command=_prepare_command({"items": items}),
        session_id="session-1",
        trace_id="invalid-items",
    )
    assert result["status"] == "error"
    assert policy.consumed is False
    assert provider.ledger == []


@pytest.mark.parametrize("encoded_items", [False, True])
def test_approved_prepare_creates_one_checkout_and_copy_is_rejected(
    tmp_path, encoded_items
) -> None:
    args = _prepare_args()
    expected_hash = stable_invocation_hash(
        tool="commerce", method="prepare_order", args=args
    )
    if encoded_items:
        args["items"] = json.dumps(args["items"])
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    adapter = _adapter(tmp_path, runtime, _GrantPolicy(allowed_hash=expected_hash))

    first = adapter.execute(
        command=_prepare_command(args),
        session_id="session-1",
        trace_id="trace-1",
    )
    copied = adapter.execute(
        command=_prepare_command(args),
        session_id="session-1",
        trace_id="trace-2",
    )

    assert first["status"] == "success"
    assert first["outputs"]["data"]["state"] == "prepared"
    assert [entry.operation for entry in provider.ledger] == ["prepare_order"]
    assert provider._orders == {}
    assert copied["error"]["code"] == "CONFIRM_REQUIRED"
    assert [entry.operation for entry in provider.ledger] == ["prepare_order"]


def test_prepare_reconciles_dropped_response_without_second_checkout(tmp_path) -> None:
    args = _prepare_args()
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    provider.drop_next_response("prepare_order")
    policy = _GrantPolicy(
        allowed_hash=stable_invocation_hash(
            tool="commerce", method="prepare_order", args=args
        )
    )

    result = _adapter(tmp_path, runtime, policy).execute(
        command=_prepare_command(args),
        session_id="session-1",
        trace_id="trace-1",
    )

    assert result["status"] == "success"
    assert result["outputs"]["data"]["state"] == "prepared"
    assert [entry.operation for entry in provider.ledger] == ["prepare_order"]


def test_prepare_rejects_configured_merchant_mismatch(tmp_path, monkeypatch) -> None:
    args = _prepare_args()
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    original = provider.prepare_order

    def mismatched(request, context):
        preparation = original(request, context)
        return preparation.model_copy(
            update={
                "merchant": MerchantIdentity(
                    provider_id="merchant-copied",
                    display_name="Copied merchant",
                )
            }
        )

    monkeypatch.setattr(provider, "prepare_order", mismatched)
    policy = _GrantPolicy(
        allowed_hash=stable_invocation_hash(
            tool="commerce", method="prepare_order", args=args
        )
    )

    result = _adapter(tmp_path, runtime, policy).execute(
        command=_prepare_command(args),
        session_id="session-1",
        trace_id="trace-1",
    )

    assert result["error"]["code"] == "INVALID_RESPONSE"
    assert result["error"]["details"]["commerce_code"] == "MERCHANT_MISMATCH"


@pytest.mark.parametrize(
    ("provider_code", "error_code", "commerce_code"),
    [
        ("NOT_FOUND", "NOT_FOUND", "ORDER_NOT_FOUND"),
        ("STALE_REVISION", "INVALID_REQUEST", "STALE_PREPARATION"),
        ("UNSUPPORTED_ORDER", "INVALID_REQUEST", "UNSUPPORTED_ORDER"),
        ("CHECKOUT_NOT_READY", "INVALID_REQUEST", "CHECKOUT_NOT_READY"),
        ("MERCHANT_MISMATCH", "INVALID_RESPONSE", "MERCHANT_MISMATCH"),
        ("PROVIDER_UNAVAILABLE", "UPSTREAM_ERROR", "PROVIDER_UNAVAILABLE"),
    ],
)
def test_provider_failures_use_closed_envelope_and_commerce_code(
    tmp_path, monkeypatch, provider_code, error_code, commerce_code
) -> None:
    bootstrap = _bootstrap(tmp_path, enabled=True)
    runtime, provider = build_fixture_commerce_runtime()

    def fail(_request):
        raise CommerceProviderError(provider_code, "fixture failure")

    monkeypatch.setattr(provider, "inspect", fail)
    result = execute_tool_spec_call(
        tool=bootstrap.registry.get("commerce.inspect"),
        arguments={"kind": "product", "product_id": "product-1"},
        context=ToolExecutionContext(
            channel="test",
            target="test",
            session_id="session-1",
            subject_id="local",
            tool_resources={"commerce": runtime},
        ),
    )

    assert result.ok is False
    assert result.data == {
        "error_code": error_code,
        "details": {"commerce_code": commerce_code},
    }


def test_inspect_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        CommerceInspectArgs.model_validate({"kind": "merchant", "merchant_id": "m"})
