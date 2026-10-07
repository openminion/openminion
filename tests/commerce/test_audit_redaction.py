from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.commerce.config import CommerceToolRuntimeConfig
from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.commerce.provider import (
    CommerceOutcomeUnknown,
    CommerceProviderError,
)
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.modules.tool.plugin_api import stable_invocation_hash
from openminion.tools.commerce import ALL_COMMERCE_TOOLS
from tests.helpers.commerce_runtime import build_fixture_commerce_runtime


class _Telemetry:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit_canonical_event(
        self,
        _session_id: str,
        _turn_id: str,
        event_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> None:
        self.events.append((event_type, payload))


class _GrantPolicy:
    def __init__(self, allowed_hash: str | None) -> None:
        self.allowed_hash = allowed_hash

    def mode(self) -> str:
        return "enforce"

    def resolve_matching_active_grant_for_use(self, **criteria):
        if criteria["invocation_hash"] != self.allowed_hash:
            return None
        return SimpleNamespace(approval_id="approval-1", grant_id="grant-1")


def _adapter(tmp_path, runtime, policy, telemetry):
    config = SimpleNamespace(
        runtime=SimpleNamespace(
            tools=SimpleNamespace(
                commerce=CommerceToolRuntimeConfig(
                    enabled=True,
                    provider="fixture",
                    base_url="https://fixture.invalid",
                    merchant_id="merchant-fixture",
                    provider_secret_key="provider-secret",
                    buyer_profile_record_id="buyer-profile",
                    payment_token_record_id="payment-token",
                )
            )
        ),
        mcp_servers=None,
        tool_selection=None,
    )
    bootstrap = build_runtime_bootstrap(
        config=config,
        workspace_root=tmp_path,
        run_root=tmp_path / "run",
        strict=False,
    )
    return ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=bootstrap.registry,
        commerce_runtime=runtime,
        policy_ctl=policy,
        telemetryctl=telemetry,
        policy={"tools": {"allow_exact": list(ALL_COMMERCE_TOOLS)}},
    )


def _command(args: dict[str, object]) -> dict[str, object]:
    return {
        "command_id": "call-1",
        "tool_name": "commerce.prepare_order",
        "args": args,
        "inputs": {
            "confirmation_source": "policy_replay",
            "confirmation_grant_id": "grant-1",
        },
        "meta": {"orchestration": {"task_backed_task_id": "task-1"}},
    }


def _action_events(telemetry: _Telemetry) -> list[dict[str, object]]:
    return [
        payload for kind, payload in telemetry.events if kind == "tool.commerce.action"
    ]


def test_denied_prepare_emits_no_commerce_action(tmp_path) -> None:
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    telemetry = _Telemetry()
    args = {"items": [{"offer_id": "offer-1", "variant_id": "standard", "quantity": 1}]}

    result = _adapter(tmp_path, runtime, _GrantPolicy(None), telemetry).execute(
        command=_command(args), session_id="session-1", trace_id="turn-1"
    )

    assert result["error"]["code"] == "CONFIRM_REQUIRED"
    assert provider.ledger == []
    assert _action_events(telemetry) == []


def test_accepted_prepare_emits_one_joined_redacted_action(tmp_path) -> None:
    sentinel_secret = "PAYMENT_SECRET_SENTINEL"
    runtime, _provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    runtime.secret_service.records["payment-token"] = (
        '{"token":"' + sentinel_secret + '","payment_label":"Visa ending 4242"}'
    )
    telemetry = _Telemetry()
    args = {"items": [{"offer_id": "offer-1", "variant_id": "standard", "quantity": 1}]}
    invocation_hash = stable_invocation_hash(
        tool="commerce", method="prepare_order", args=args
    )

    result = _adapter(
        tmp_path, runtime, _GrantPolicy(invocation_hash), telemetry
    ).execute(command=_command(args), session_id="session-1", trace_id="turn-1")

    [event] = _action_events(telemetry)
    assert event == {
        "tool_name": "commerce.prepare_order",
        "action": "prepare_order",
        "outcome": "accepted",
        "subject_id": "local",
        "policy_approval_id": "approval-1",
        "policy_grant_id": "grant-1",
        "invocation_id": invocation_hash,
        "order_id": event["order_id"],
        "attempt_id": event["attempt_id"],
        "task_id": "task-1",
    }
    assert str(event["order_id"]).startswith("sha256:")
    assert str(event["attempt_id"]).startswith("sha256:")
    observable = repr((telemetry.events, result, runtime.order_store))
    assert sentinel_secret not in observable


def test_provider_exception_sentinels_stay_out_of_all_observable_surfaces(
    tmp_path, monkeypatch, caplog
) -> None:
    sentinel_secret = "PAYMENT_SECRET_SENTINEL"
    sentinel_url = "https://merchant.invalid/checkout?signature=SIGNED_URL_SENTINEL"
    database_path = tmp_path / "commerce.db"
    runtime, provider = build_fixture_commerce_runtime(store_path=database_path)
    runtime.secret_service.records["payment-token"] = (
        '{"token":"' + sentinel_secret + '","payment_label":"Visa ending 4242"}'
    )

    def fail_after_secret_access(*_args) -> None:
        raise CommerceProviderError(
            "PROVIDER_UNAVAILABLE", f"{sentinel_secret} {sentinel_url}"
        )

    monkeypatch.setattr(provider, "prepare_order", fail_after_secret_access)
    telemetry = _Telemetry()
    args = {"items": [{"offer_id": "offer-1", "variant_id": "standard", "quantity": 1}]}
    invocation_hash = stable_invocation_hash(
        tool="commerce", method="prepare_order", args=args
    )

    result = _adapter(
        tmp_path, runtime, _GrantPolicy(invocation_hash), telemetry
    ).execute(command=_command(args), session_id="session-1", trace_id="turn-1")

    [event] = _action_events(telemetry)
    assert event["outcome"] == "failed"
    observables = repr(
        {
            "events_and_traces": telemetry.events,
            "logs": caplog.records,
            "exceptions_channels_and_artifacts": result,
        }
    )
    assert sentinel_secret not in observables
    assert sentinel_url not in observables
    database = database_path.read_bytes()
    assert sentinel_secret.encode() not in database
    assert sentinel_url.encode() not in database


def test_unknown_prepare_still_emits_one_action(tmp_path, monkeypatch) -> None:
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    monkeypatch.setattr(
        provider,
        "prepare_order",
        lambda *_args: (_ for _ in ()).throw(
            CommerceOutcomeUnknown("provider response lost")
        ),
    )
    monkeypatch.setattr(provider, "recover_preparation", lambda _locator: None)
    telemetry = _Telemetry()
    args = {"items": [{"offer_id": "offer-1", "variant_id": "standard", "quantity": 1}]}
    invocation_hash = stable_invocation_hash(
        tool="commerce", method="prepare_order", args=args
    )

    result = _adapter(
        tmp_path, runtime, _GrantPolicy(invocation_hash), telemetry
    ).execute(command=_command(args), session_id="session-1", trace_id="turn-1")

    assert result["outputs"]["state"] == "outcome_unknown"
    [event] = _action_events(telemetry)
    assert event["outcome"] == "outcome_unknown"
