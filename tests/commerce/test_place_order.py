from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

from openminion.base.config.runtime.tool_family import CommerceToolRuntimeConfig
from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.commerce.confirmation import ExactOrderConfirmationPreview
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.modules.tool.plugin_api import stable_invocation_hash
from openminion.tools.commerce import ALL_COMMERCE_TOOLS
from tests.helpers.commerce_runtime import build_fixture_commerce_runtime


def _config(*, enabled: bool, writes_enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(
            tools=SimpleNamespace(
                commerce=CommerceToolRuntimeConfig(
                    enabled=enabled,
                    writes_enabled=writes_enabled,
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


def _bootstrap(tmp_path, *, enabled: bool, writes_enabled: bool):
    return build_runtime_bootstrap(
        config=_config(enabled=enabled, writes_enabled=writes_enabled),
        workspace_root=tmp_path,
        run_root=tmp_path / "run",
        strict=False,
    )


def _commerce_names(bootstrap) -> set[str]:
    return {
        name for name in bootstrap.registry.list() if name.startswith("commerce.")
    }


class _GrantPolicy:
    def __init__(self, *allowed_hashes: str) -> None:
        self.allowed_hashes = list(allowed_hashes)
        self.consumed = 0

    def mode(self) -> str:
        return "enforce"

    def resolve_matching_active_grant_for_use(self, **criteria):
        if not self.allowed_hashes or criteria["invocation_hash"] != self.allowed_hashes[0]:
            return None
        self.allowed_hashes.pop(0)
        self.consumed += 1
        return SimpleNamespace(
            approval_id=f"approval-{self.consumed}",
            grant_id=f"grant-{self.consumed}",
        )


def _prepare(runtime) -> object:
    return runtime.prepare_public(
        {
            "items": [
                {"offer_id": "offer-1", "variant_id": "standard", "quantity": 1}
            ]
        }
    )


def _place_args(preparation) -> dict[str, object]:
    return {
        "preparation_ref": preparation.preparation_ref,
        "preparation": preparation.model_dump(mode="json"),
        "preparation_digest": preparation.preparation_digest,
    }


def _place_command(args: dict[str, object], *, grant_id: str = "grant-1"):
    return {
        "tool_name": "commerce.place_order",
        "args": args,
        "inputs": {
            "confirmation_source": "policy_replay",
            "confirmation_grant_id": grant_id,
        },
    }


def _adapter(tmp_path, runtime, policy_ctl) -> ToolAdapter:
    bootstrap = _bootstrap(tmp_path, enabled=True, writes_enabled=True)
    return ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=bootstrap.registry,
        commerce_runtime=runtime,
        policy_ctl=policy_ctl,
        policy={"tools": {"allow_exact": list(ALL_COMMERCE_TOOLS)}},
    )


def _place_hash(args: dict[str, object]) -> str:
    return stable_invocation_hash(tool="commerce", method="place_order", args=args)


def test_place_order_exposure_tracks_write_phase(tmp_path) -> None:
    disabled = _bootstrap(tmp_path / "disabled", enabled=False, writes_enabled=False)
    read_enabled = _bootstrap(
        tmp_path / "read-enabled", enabled=True, writes_enabled=False
    )
    write_enabled = _bootstrap(
        tmp_path / "write-enabled", enabled=True, writes_enabled=True
    )

    assert _commerce_names(disabled) == set()
    assert _commerce_names(read_enabled) == {
        "commerce.inspect",
        "commerce.prepare_order",
    }
    assert _commerce_names(write_enabled) == set(ALL_COMMERCE_TOOLS)
    assert disabled.contract_drift_report.has_drift is False
    assert read_enabled.contract_drift_report.has_drift is False
    assert write_enabled.contract_drift_report.has_drift is False


def test_place_order_requires_exact_consumed_authorization_before_provider(
    tmp_path,
) -> None:
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    preparation = _prepare(runtime)
    args = _place_args(preparation)

    result = _adapter(tmp_path, runtime, _GrantPolicy("wrong-hash")).execute(
        command=_place_command(args),
        session_id="session-1",
        trace_id="trace-1",
    )

    assert result["error"]["code"] == "CONFIRM_REQUIRED"
    assert [entry.operation for entry in provider.ledger] == ["prepare_order"]


def test_exact_preview_and_two_approvals_produce_one_durable_order(tmp_path) -> None:
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    preparation = _prepare(runtime)
    args = _place_args(preparation)
    invocation_hash = _place_hash(args)
    policy = _GrantPolicy(invocation_hash, invocation_hash)
    adapter = _adapter(tmp_path, runtime, policy)

    preview = runtime.resolve_confirmation_preview(
        tool_name="commerce.place_order",
        args=args,
        subject_id="local",
        session_id="session-1",
    )
    first = adapter.execute(
        command=_place_command(args),
        session_id="session-1",
        trace_id="trace-1",
    )
    recovered = adapter.execute(
        command=_place_command(args, grant_id="grant-2"),
        session_id="session-1",
        trace_id="trace-2",
    )

    assert isinstance(preview, ExactOrderConfirmationPreview)
    assert preview.preparation_digest == preparation.preparation_digest
    assert first["outputs"]["data"]["state"] == "succeeded"
    assert recovered["outputs"]["data"]["order_ref"] == first["outputs"]["data"][
        "order_ref"
    ]
    assert policy.consumed == 2
    assert [entry.operation for entry in provider.ledger].count("place_order") == 1
    order_ref = first["outputs"]["data"]["order_ref"]
    assert runtime.order_store.get_order("local", order_ref) is not None
    place_entry = next(
        entry for entry in provider.ledger if entry.operation == "place_order"
    )
    attempt = runtime.order_store.get_attempt_by_idempotency(
        subject_id="local",
        kind="placement",
        idempotency_key=place_entry.idempotency_key,
    )
    assert attempt.authorization_hash == invocation_hash
    assert attempt.attempt_count == 1


def test_placement_credential_events_are_typed_and_redacted(tmp_path) -> None:
    runtime, _provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    preparation = _prepare(runtime)
    args = _place_args(preparation)
    result = _adapter(tmp_path, runtime, _GrantPolicy(_place_hash(args))).execute(
        command=_place_command(args),
        session_id="session-1",
        trace_id="trace-1",
    )

    events = runtime.credential_audit_log.access_events()
    assert {event.credential_id for event in events} == {
        "provider-secret",
        "buyer-profile",
        "payment-token",
    }
    assert {event.access_site for event in events} == {
        "tools.commerce.place_order"
    }
    observable = repr((events, result, runtime.order_store))
    assert "fixture-provider-secret" not in observable
    assert "fixture-payment-token" not in observable


def test_consumed_approval_before_reservation_needs_a_second_approval(
    tmp_path, monkeypatch
) -> None:
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    preparation = _prepare(runtime)
    args = _place_args(preparation)
    invocation_hash = _place_hash(args)
    policy = _GrantPolicy(invocation_hash, invocation_hash)
    adapter = _adapter(tmp_path, runtime, policy)
    place_public = runtime.place_public

    def crash_before_reservation(*_args, **_kwargs):
        raise RuntimeError("fixture crash before reservation")

    monkeypatch.setattr(runtime, "place_public", crash_before_reservation)
    crashed = adapter.execute(
        command=_place_command(args),
        session_id="session-1",
        trace_id="trace-crash",
    )
    monkeypatch.setattr(runtime, "place_public", place_public)
    placed = adapter.execute(
        command=_place_command(args, grant_id="grant-2"),
        session_id="session-1",
        trace_id="trace-retry",
    )

    assert crashed["error"]["code"] == "EXEC_ERROR"
    assert placed["outputs"]["data"]["state"] == "succeeded"
    assert policy.consumed == 2
    assert [entry.operation for entry in provider.ledger].count("place_order") == 1


def test_copied_preparation_and_configured_merchant_mismatch_stop_before_provider(
    tmp_path,
) -> None:
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    preparation = _prepare(runtime)
    copied_args = {
        **_place_args(preparation),
        "preparation_ref": "copied-preparation",
    }
    copied = _adapter(
        tmp_path / "copied", runtime, _GrantPolicy(_place_hash(copied_args))
    ).execute(
        command=_place_command(copied_args),
        session_id="session-1",
        trace_id="trace-copied",
    )

    args = _place_args(preparation)
    runtime.merchant_id = "merchant-other"
    mismatch = _adapter(
        tmp_path / "mismatch", runtime, _GrantPolicy(_place_hash(args))
    ).execute(
        command=_place_command(args),
        session_id="session-1",
        trace_id="trace-mismatch",
    )

    assert copied["error"]["details"]["commerce_code"] == "ORDER_ACCESS_DENIED"
    assert mismatch["error"]["details"]["commerce_code"] == "MERCHANT_MISMATCH"
    assert [entry.operation for entry in provider.ledger].count("place_order") == 0


def test_stale_checkout_never_crosses_placement_boundary(tmp_path) -> None:
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    preparation = _prepare(runtime)
    args = _place_args(preparation)
    provider.advance_checkout_revision()

    result = _adapter(tmp_path, runtime, _GrantPolicy(_place_hash(args))).execute(
        command=_place_command(args),
        session_id="session-1",
        trace_id="trace-stale",
    )

    assert result["error"]["code"] == "INVALID_REQUEST"
    assert result["error"]["details"]["commerce_code"] == "STALE_PREPARATION"
    assert [entry.operation for entry in provider.ledger].count("place_order") == 0


def test_decline_recovers_dropped_response_without_order_id(tmp_path) -> None:
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    preparation = _prepare(runtime)
    args = _place_args(preparation)
    provider.set_next_placement_state("declined")
    provider.drop_next_response("place_order")

    result = _adapter(tmp_path, runtime, _GrantPolicy(_place_hash(args))).execute(
        command=_place_command(args),
        session_id="session-1",
        trace_id="trace-declined",
    )

    assert result["outputs"]["data"]["state"] == "declined"
    assert result["outputs"]["data"]["order_ref"] is None
    assert [entry.operation for entry in provider.ledger].count("place_order") == 1


def test_action_required_returns_handoff_and_invalidates_preparation(tmp_path) -> None:
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    preparation = _prepare(runtime)
    args = _place_args(preparation)
    provider.set_next_placement_state("action_required")

    result = _adapter(tmp_path, runtime, _GrantPolicy(_place_hash(args))).execute(
        command=_place_command(args),
        session_id="session-1",
        trace_id="trace-handoff",
    )

    assert result["outputs"]["state"] == "handoff_required"
    assert result["outputs"]["data"]["commerce_code"] == "HANDOFF_REQUIRED"
    stored = runtime.order_store.get_preparation("local", preparation.preparation_ref)
    assert stored.invalidated_at is not None
    assert [entry.operation for entry in provider.ledger].count("place_order") == 1


def test_outcome_unknown_is_recovered_without_blind_retry(tmp_path) -> None:
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    preparation = _prepare(runtime)
    args = _place_args(preparation)
    invocation_hash = _place_hash(args)
    provider.set_next_placement_state("outcome_unknown")

    first = _adapter(
        tmp_path / "first", runtime, _GrantPolicy(invocation_hash)
    ).execute(
        command=_place_command(args),
        session_id="session-1",
        trace_id="trace-first",
    )
    recovered = _adapter(
        tmp_path / "recovered", runtime, _GrantPolicy(invocation_hash)
    ).execute(
        command=_place_command(args),
        session_id="session-1",
        trace_id="trace-recovered",
    )

    assert first["outputs"]["data"]["state"] == "outcome_unknown"
    assert recovered["outputs"]["data"]["state"] == "outcome_unknown"
    assert [entry.operation for entry in provider.ledger].count("place_order") == 1


def test_concurrent_approvals_allow_one_provider_mutation(tmp_path, monkeypatch) -> None:
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    preparation = _prepare(runtime)
    args = _place_args(preparation)
    invocation_hash = _place_hash(args)
    entered_provider = Event()
    release_provider = Event()
    place_order = provider.place_order

    def blocked_place(request):
        entered_provider.set()
        assert release_provider.wait(timeout=5)
        return place_order(request)

    monkeypatch.setattr(provider, "place_order", blocked_place)
    adapters = (
        _adapter(tmp_path / "first", runtime, _GrantPolicy(invocation_hash)),
        _adapter(tmp_path / "second", runtime, _GrantPolicy(invocation_hash)),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            adapters[0].execute,
            command=_place_command(args),
            session_id="session-1",
            trace_id="trace-first",
        )
        assert entered_provider.wait(timeout=5)
        second = executor.submit(
            adapters[1].execute,
            command=_place_command(args),
            session_id="session-1",
            trace_id="trace-second",
        )
        concurrent = second.result(timeout=5)
        release_provider.set()
        placed = first.result(timeout=5)

    assert placed["outputs"]["data"]["state"] == "succeeded"
    assert concurrent["outputs"]["state"] == "outcome_unknown"
    assert [entry.operation for entry in provider.ledger].count("place_order") == 1
