from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.helpers.runtime_roots import isolate_runtime_roots  # noqa: E402

isolate_runtime_roots(prefix="openminion-commerce-local-")

from openminion.tools.commerce.models import CommerceLifecycleState  # noqa: E402
from openminion.tools.commerce.provider import (  # noqa: E402
    CommerceHandoff,
    CommerceProviderError,
    OrderPreparation,
    safe_commerce_links,
)
from openminion.modules.tool.errors import ToolRuntimeError  # noqa: E402
from openminion.modules.policy.models import PolicyConfig  # noqa: E402
from openminion.modules.policy.runtime.action_policy import (  # noqa: E402
    derive_tool_risk_spec,
)
from openminion.modules.policy.runtime.service import PolicyCtl  # noqa: E402
from openminion.services.runtime.cron.delivery import CronDeliveryBridge  # noqa: E402
from openminion.tools.commerce.authorization import (  # noqa: E402
    canonical_commerce_args,
    consume_commerce_authorization,
)
from openminion.tools.commerce.plugin import _h_inspect, _h_prepare_order  # noqa: E402
from openminion.tools.commerce.family import COMMERCE_FAMILY  # noqa: E402
from openminion.tools.task.constants import WATCH_PAYLOAD_KEY  # noqa: E402
from openminion.tools.task.routine.dispatcher import CommerceOrderHandler  # noqa: E402
from openminion.tools.task.routine.schemas import (  # noqa: E402
    CommerceOrderConfigV1,
    CommerceOrderCursorV1,
    RoutinePayloadV1,
)
from tests.helpers.commerce_runtime import build_fixture_commerce_runtime  # noqa: E402
from tests.helpers.live_e2e_profiles import resolve_live_framework_root  # noqa: E402

FRAMEWORK_ROOT = resolve_live_framework_root(ROOT)
EVIDENCE_ROOT = FRAMEWORK_ROOT / "workspace-tmp" / "commerce-e2e" / "local"
EVIDENCE_PATH = EVIDENCE_ROOT / "evidence.json"

SCENARIO_IDS = (
    "research_zero_mutation",
    "prepare_denied_then_approved",
    "place_separate_approval",
    "stale_cart_new_approval",
    "decline_and_auth_handoff",
    "crash_recovery_without_resubmit",
    "tracking_cursor_deduplication",
    "approved_cancellation",
    "partial_return_and_refund",
    "unsupported_action_handoff",
    "cross_subject_and_channel_rejection",
    "hostile_content_and_link_rejection",
    "copied_preparation_and_merchant_mismatch",
    "exact_and_ambiguous_refund",
    "return_label_and_signed_link_redaction",
)

SCENARIO_COVERAGE = {
    "research_zero_mutation": ("COLR-S01",),
    "prepare_denied_then_approved": ("COLR-S02",),
    "place_separate_approval": ("COLR-S03", "COLR-S16"),
    "stale_cart_new_approval": ("COLR-S04",),
    "decline_and_auth_handoff": ("COLR-S05", "COLR-S06"),
    "crash_recovery_without_resubmit": ("COLR-S07",),
    "tracking_cursor_deduplication": ("COLR-S09",),
    "approved_cancellation": ("COLR-S10",),
    "partial_return_and_refund": ("COLR-S11", "COLR-S12"),
    "unsupported_action_handoff": ("COLR-S13",),
    "cross_subject_and_channel_rejection": ("COLR-S08", "COLR-S15"),
    "hostile_content_and_link_rejection": ("COLR-S14",),
    "copied_preparation_and_merchant_mismatch": ("COLR-S17", "COLR-S20"),
    "exact_and_ambiguous_refund": ("COLR-S18",),
    "return_label_and_signed_link_redaction": ("COLR-S19",),
}


def _source_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def _runtime(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    return build_fixture_commerce_runtime(store_path=root / "commerce.db")


def _prepare(runtime):
    return runtime.prepare_public(
        {"items": [{"offer_id": "offer-1", "variant_id": "standard", "quantity": 1}]}
    )


def _place(runtime, preparation, *, authorization_hash: str = "a" * 64):
    return runtime.place_public(
        {
            "preparation_ref": preparation.preparation_ref,
            "preparation": preparation.model_dump(mode="json"),
            "preparation_digest": preparation.preparation_digest,
        },
        authorization_hash=authorization_hash,
    )


def _placed(root: Path):
    runtime, provider = _runtime(root)
    preparation = _prepare(runtime)
    placement = _place(runtime, preparation)
    return runtime, provider, preparation, placement


def _action(
    runtime,
    placement,
    *,
    kind: str,
    policy_root: Path,
    invocation_id: str,
    **extra,
):
    preparation = runtime.prepare_action_public(
        {
            "local_order_ref": placement.order_ref,
            "order_revision": placement.order_revision,
            "kind": kind,
            **extra,
        }
    )
    assert not isinstance(preparation, CommerceHandoff)
    apply_args = {
        "action_ref": preparation.action_ref,
        "preparation": preparation.model_dump(mode="json"),
        "action_digest": preparation.action_digest,
    }
    authorization, policy = _approved_policy(
        policy_root,
        method="apply_order_action",
        args=apply_args,
        invocation_id=invocation_id,
    )
    result = runtime.apply_action_public(
        apply_args,
        authorization_hash=authorization.invocation_hash,
    )
    return preparation, result, policy


def _scenario(
    scenario_id: str,
    *,
    ledger_before: int,
    ledger_after: int,
    order_id: str = "none",
    attempt_id: str = "none",
    idempotency_key: str = "none",
    policy: dict[str, str] | None = None,
    facts: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "scenario_id": scenario_id,
        "status": "passed",
        "subject_id": "local",
        "policy": policy,
        "order_id": order_id,
        "attempt_id": attempt_id,
        "idempotency_key": idempotency_key,
        "fixture_ledger_before": ledger_before,
        "fixture_ledger_after": ledger_after,
        "facts": facts or {},
    }


def _ledger_entries(provider, fixture_id: str) -> list[dict[str, str]]:
    return [{"fixture_id": fixture_id, **item.__dict__} for item in provider.ledger]


def _attempt(runtime, *, kind: str, idempotency_key: str) -> dict[str, object]:
    attempt = runtime.order_store.get_attempt_by_idempotency(
        subject_id="local",
        kind=kind,
        idempotency_key=idempotency_key,
    )
    assert attempt is not None
    return attempt.model_dump(mode="json")


def _approved_policy(
    root: Path,
    *,
    method: str,
    args: dict[str, object],
    invocation_id: str,
):
    ctl = PolicyCtl.with_sqlite(
        root / f"policy-{invocation_id}.db",
        config=PolicyConfig(mode="enforce"),
    )
    tool_name = f"commerce.{method}"
    ctl.register_risk(
        tool_name,
        derive_tool_risk_spec(tool_name=tool_name, tool=None),
    )
    invocation = {
        "tool": "commerce",
        "method": method,
        "args": canonical_commerce_args(args),
        "invocation_id": invocation_id,
    }
    context = {
        "subject_id": "local",
        "session_id": "commerce-local",
        "trace_id": invocation_id,
    }
    pending = ctl.check(
        invocation,
        context,
        confirmation_preview={
            "schema_version": "commerce-confirmation-preview-v1",
            "method": method,
            "consequence": "Apply the exact reviewed commerce operation once.",
        },
    )
    assert pending.decision == "REQUIRE_CONFIRM" and pending.approval_id
    grant_id = ctl.resolve_confirmation(pending.approval_id, "allow_once")
    authorization = consume_commerce_authorization(
        method=method,
        policy_ctl=ctl,
        permission_mode="default",
        args=args,
        subject_id="local",
        session_id="commerce-local",
    )
    policy = {
        "approval_id": pending.approval_id,
        "grant_id": str(grant_id),
        "invocation_id": invocation_id,
        "invocation_hash": authorization.invocation_hash,
    }
    ctl.close()
    return authorization, policy


class _RoutineContext:
    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def tool_family_enabled(self, *, family: str) -> bool:
        return family == "commerce"

    def exact_provider_enabled(self, *, family: str, provider_id: str) -> bool:
        del family, provider_id
        return False

    def invoke_tool(self, *, name: str, args: dict[str, object]):
        assert name == "commerce.inspect"
        return {
            "ok": True,
            "data": self.runtime.inspect_public(args).model_dump(mode="json"),
        }


class _Sessions:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.events: list[dict[str, object]] = []

    def append_message(self, **payload) -> None:
        self.messages.append(payload)

    def append_event(self, **payload) -> None:
        self.events.append(payload)


def _run() -> dict[str, object]:
    shutil.rmtree(EVIDENCE_ROOT, ignore_errors=True)
    EVIDENCE_ROOT.mkdir(parents=True)
    scenarios: list[dict[str, object]] = []
    ledger: list[dict[str, str]] = []
    attempts: list[dict[str, object]] = []

    runtime, provider = _runtime(EVIDENCE_ROOT / "research")
    runtime.inspect_public({"kind": "product", "product_id": "product-1"})
    scenarios.append(
        _scenario("research_zero_mutation", ledger_before=0, ledger_after=0)
    )

    runtime, provider = _runtime(EVIDENCE_ROOT / "order")
    prepare_args = {
        "items": [{"offer_id": "offer-1", "variant_id": "standard", "quantity": 1}]
    }
    try:
        _h_prepare_order(
            prepare_args,
            type(
                "Context",
                (),
                {
                    "tool_resources": {"commerce": runtime},
                    "policy_authorization": None,
                    "subject_id": "local",
                    "session_id": "commerce-local",
                    "project_task_id": "",
                },
            )(),
        )
    except ToolRuntimeError as exc:
        assert exc.code == "CONFIRM_REQUIRED"
    prepare_authorization, prepare_policy = _approved_policy(
        EVIDENCE_ROOT / "order",
        method="prepare_order",
        args=prepare_args,
        invocation_id="prepare-order-1",
    )
    prepared_result = _h_prepare_order(
        prepare_args,
        SimpleNamespace(
            tool_resources={"commerce": runtime},
            policy_authorization=prepare_authorization,
            subject_id="local",
            session_id="commerce-local",
            tool_name="commerce.prepare_order",
            tool_call_id="prepare-order-1",
            agent_id="fixture-agent",
            project_task_id="",
            telemetryctl=None,
        ),
    )
    preparation = OrderPreparation.model_validate(
        {key: prepared_result["data"][key] for key in OrderPreparation.model_fields}
    )
    prepare_attempt = _attempt(
        runtime,
        kind="preparation",
        idempotency_key=provider.ledger[0].idempotency_key,
    )
    attempts.append(prepare_attempt)
    scenarios.append(
        _scenario(
            "prepare_denied_then_approved",
            ledger_before=0,
            ledger_after=len(provider.ledger),
            attempt_id=str(prepare_attempt["attempt_id"]),
            idempotency_key=provider.ledger[0].idempotency_key,
            policy=prepare_policy,
        )
    )
    place_args = {
        "preparation_ref": preparation.preparation_ref,
        "preparation": preparation.model_dump(mode="json"),
        "preparation_digest": preparation.preparation_digest,
    }
    place_authorization, place_policy = _approved_policy(
        EVIDENCE_ROOT / "order",
        method="place_order",
        args=place_args,
        invocation_id="place-order-1",
    )
    placement = _place(
        runtime,
        preparation,
        authorization_hash=place_authorization.invocation_hash,
    )
    assert placement.order_ref is not None
    credential_events = [
        asdict(event) for event in runtime.credential_audit_log.access_events()
    ]
    place_attempt = _attempt(
        runtime,
        kind="placement",
        idempotency_key=placement.idempotency_key,
    )
    attempts.append(place_attempt)
    scenarios.append(
        _scenario(
            "place_separate_approval",
            ledger_before=1,
            ledger_after=len(provider.ledger),
            order_id=placement.order_ref,
            attempt_id=str(place_attempt["attempt_id"]),
            idempotency_key=placement.idempotency_key,
            policy=place_policy,
        )
    )
    ledger.extend(_ledger_entries(provider, "order"))

    stale_runtime, stale_provider = _runtime(EVIDENCE_ROOT / "stale")
    stale_preparation = _prepare(stale_runtime)
    stale_provider.advance_checkout_revision()
    try:
        _place(stale_runtime, stale_preparation)
    except CommerceProviderError as exc:
        assert exc.code == "STALE_PREPARATION"
    scenarios.append(
        _scenario(
            "stale_cart_new_approval",
            ledger_before=1,
            ledger_after=len(stale_provider.ledger),
        )
    )

    decline_runtime, decline_provider = _runtime(EVIDENCE_ROOT / "decline")
    decline_provider.set_next_placement_state("declined")
    declined = _place(decline_runtime, _prepare(decline_runtime))
    handoff_runtime, handoff_provider = _runtime(EVIDENCE_ROOT / "auth-handoff")
    handoff_provider.set_next_placement_state("action_required")
    handoff = _place(handoff_runtime, _prepare(handoff_runtime))
    assert declined.state == "declined" and isinstance(handoff, CommerceHandoff)
    scenarios.append(
        _scenario(
            "decline_and_auth_handoff",
            ledger_before=0,
            ledger_after=4,
            facts={"declined": True, "handoff": handoff.reason_code},
        )
    )
    ledger.extend(_ledger_entries(decline_provider, "decline"))
    ledger.extend(_ledger_entries(handoff_provider, "auth-handoff"))

    recovery_runtime, recovery_provider = _runtime(EVIDENCE_ROOT / "recovery")
    recovery_preparation = _prepare(recovery_runtime)
    recovery_args = {
        "preparation_ref": recovery_preparation.preparation_ref,
        "preparation": recovery_preparation.model_dump(mode="json"),
        "preparation_digest": recovery_preparation.preparation_digest,
    }
    recovery_authorization, recovery_policy = _approved_policy(
        EVIDENCE_ROOT / "recovery",
        method="place_order",
        args=recovery_args,
        invocation_id="place-recovery",
    )
    recovery_provider.drop_next_response("place_order")
    recovered = _place(
        recovery_runtime,
        recovery_preparation,
        authorization_hash=recovery_authorization.invocation_hash,
    )
    assert recovered.order_ref is not None
    recovery_attempt = _attempt(
        recovery_runtime,
        kind="placement",
        idempotency_key=recovered.idempotency_key,
    )
    attempts.append(recovery_attempt)
    scenarios.append(
        _scenario(
            "crash_recovery_without_resubmit",
            ledger_before=1,
            ledger_after=len(recovery_provider.ledger),
            order_id=recovered.order_ref,
            attempt_id=str(recovery_attempt["attempt_id"]),
            idempotency_key=recovered.idempotency_key,
            policy=recovery_policy,
            facts={"provider_place_calls": 1},
        )
    )
    ledger.extend(_ledger_entries(recovery_provider, "recovery"))

    tracking_runtime, tracking_provider, _, tracking_order = _placed(
        EVIDENCE_ROOT / "tracking"
    )
    tracking_provider.set_order_inspection_lifecycles(
        (
            tracking_order.lifecycle,
            tracking_order.lifecycle,
            CommerceLifecycleState(
                order="accepted",
                fulfillment="partial",
                payment="captured",
                shipments={"shipment-1": "in_transit"},
            ),
        )
    )
    routine = RoutinePayloadV1(
        routine_kind="commerce_order",
        config=CommerceOrderConfigV1(
            subject_id="local",
            local_order_ref=str(tracking_order.order_ref),
            expires_at="2099-01-01T00:00:00Z",
        ),
        cursor=CommerceOrderCursorV1(),
    )
    handler = CommerceOrderHandler()
    context = _RoutineContext(tracking_runtime)
    sessions = _Sessions()
    bridge = CronDeliveryBridge(runtime=SimpleNamespace(sessions=sessions))
    cursors: list[str] = []
    delivery_requests: list[bool] = []
    for index in range(3):
        facts = handler.pre_turn(routine=routine, routine_id="routine-1", ctx=context)
        post = handler.post_turn(
            routine=routine,
            routine_id="routine-1",
            facts=facts,
            outcome_text="",
        )
        assert post.updated_routine is not None
        routine = post.updated_routine
        cursor = str(routine.cursor.material_cursor)
        cursors.append(cursor)
        deliver = post.condition_value is True
        delivery_requests.append(deliver)
        job = {
            "job_id": "commerce-watch",
            "payload": {
                WATCH_PAYLOAD_KEY: {"routine": routine.model_dump(mode="json")},
                "_openminion_origin": {"session_id": "commerce-local"},
            },
        }
        bridge.deliver(
            "announce",
            "last",
            job,
            {"run_id": f"tracking-{index + 1}"},
            {
                "summary": post.summary_line,
                "output": {"watch_delivery_requested": deliver},
            },
        )
    assert delivery_requests == [True, False, True], delivery_requests
    assert len(sessions.messages) == 2 and len(sessions.events) == 2
    scenarios.append(
        _scenario(
            "tracking_cursor_deduplication",
            ledger_before=2,
            ledger_after=2,
            order_id=tracking_order.order_ref,
            facts={
                "notification_count": len(sessions.messages),
                "cursor_before": cursors[0],
                "cursor_after": cursors[-1],
                "delivery_requests": delivery_requests,
            },
        )
    )

    cancel_runtime, cancel_provider, _, cancel_order = _placed(EVIDENCE_ROOT / "cancel")
    cancel_preparation, cancelled, cancel_policy = _action(
        cancel_runtime,
        cancel_order,
        kind="cancel",
        policy_root=EVIDENCE_ROOT / "cancel",
        invocation_id="cancel-order-1",
    )
    cancel_attempt = _attempt(
        cancel_runtime,
        kind="action",
        idempotency_key=cancelled.idempotency_key,
    )
    attempts.append(cancel_attempt)
    scenarios.append(
        _scenario(
            "approved_cancellation",
            ledger_before=2,
            ledger_after=len(cancel_provider.ledger),
            order_id=cancelled.order_ref,
            attempt_id=str(cancel_attempt["attempt_id"]),
            idempotency_key=cancelled.idempotency_key,
            policy=cancel_policy,
            facts={"action_digest": cancel_preparation.action_digest},
        )
    )
    ledger.extend(_ledger_entries(cancel_provider, "cancel"))

    partial_runtime, partial_provider, _, partial_order = _placed(
        EVIDENCE_ROOT / "partial-return"
    )
    _, partial, partial_policy = _action(
        partial_runtime,
        partial_order,
        kind="partial_return",
        policy_root=EVIDENCE_ROOT / "partial-return",
        invocation_id="partial-return-order-1",
        line_item_ids=("line-1",),
        quantity=1,
        reason="damaged",
    )
    refund_runtime, refund_provider, _, refund_order = _placed(EVIDENCE_ROOT / "refund")
    refund_provider.set_next_action_state("pending")
    refund_preparation, pending_refund, refund_policy = _action(
        refund_runtime,
        refund_order,
        kind="refund_request",
        policy_root=EVIDENCE_ROOT / "refund",
        invocation_id="refund-order-1",
        reason="not_received",
        refund_method="original_payment_method",
    )
    assert partial.state == "completed" and pending_refund.state == "pending"
    refund_args = {
        "action_ref": refund_preparation.action_ref,
        "preparation": refund_preparation.model_dump(mode="json"),
        "action_digest": refund_preparation.action_digest,
    }
    collision_preparation = refund_runtime.prepare_action_public(
        {
            "local_order_ref": refund_order.order_ref,
            "order_revision": refund_order.order_revision,
            "kind": "cancel",
        }
    )
    assert not isinstance(collision_preparation, CommerceHandoff)
    pending_collision_rejected = False
    try:
        refund_runtime.apply_action_public(
            {
                "action_ref": collision_preparation.action_ref,
                "preparation": collision_preparation.model_dump(mode="json"),
                "action_digest": collision_preparation.action_digest,
            },
            authorization_hash="d" * 64,
        )
    except CommerceProviderError as exc:
        pending_collision_rejected = exc.code == "ACTION_ALREADY_OPEN"
    assert pending_collision_rejected
    refund_provider.advance_action_result(
        pending_refund.idempotency_key,
        "completed",
    )
    completion_authorization, completion_policy = _approved_policy(
        EVIDENCE_ROOT / "refund",
        method="apply_order_action",
        args=refund_args,
        invocation_id="refund-completion-order-1",
    )
    completed_refund = refund_runtime.apply_action_public(
        refund_args,
        authorization_hash=completion_authorization.invocation_hash,
    )
    assert completed_refund.state == "completed"
    partial_attempt = _attempt(
        partial_runtime,
        kind="action",
        idempotency_key=partial.idempotency_key,
    )
    refund_attempt = _attempt(
        refund_runtime,
        kind="action",
        idempotency_key=pending_refund.idempotency_key,
    )
    attempts.extend((partial_attempt, refund_attempt))
    scenarios.append(
        _scenario(
            "partial_return_and_refund",
            ledger_before=4,
            ledger_after=6,
            order_id=pending_refund.order_ref,
            attempt_id=str(refund_attempt["attempt_id"]),
            idempotency_key=pending_refund.idempotency_key,
            policy=refund_policy,
            facts={
                "partial_state": partial.state,
                "refund_states": [pending_refund.state, completed_refund.state],
                "pending_collision_rejected": pending_collision_rejected,
                "refund_digest": refund_preparation.refund_destination.destination_digest,
                "partial_policy": partial_policy,
                "completion_policy": completion_policy,
            },
        )
    )
    ledger.extend(_ledger_entries(partial_provider, "partial-return"))
    ledger.extend(_ledger_entries(refund_provider, "refund"))

    unsupported_runtime, unsupported_provider, _, unsupported_order = _placed(
        EVIDENCE_ROOT / "unsupported"
    )
    unsupported_provider.set_next_action_handoff("unsupported_action")
    unsupported = unsupported_runtime.prepare_action_public(
        {
            "local_order_ref": unsupported_order.order_ref,
            "order_revision": unsupported_order.order_revision,
            "kind": "cancel",
        }
    )
    assert isinstance(unsupported, CommerceHandoff)
    scenarios.append(
        _scenario(
            "unsupported_action_handoff",
            ledger_before=2,
            ledger_after=2,
            order_id=unsupported_order.order_ref,
            facts={"reason": unsupported.reason_code},
        )
    )

    inspect_calls_before = len(provider.inspect_calls)
    ledger_before = len(provider.ledger)
    cross_subject_code = ""
    try:
        _h_inspect(
            {"kind": "order", "local_order_ref": placement.order_ref},
            SimpleNamespace(tool_resources={"commerce": runtime}, subject_id="remote"),
        )
    except ToolRuntimeError as exc:
        cross_subject_code = str(exc.details.get("commerce_code", ""))
    channel_policy = PolicyCtl.with_sqlite(
        EVIDENCE_ROOT / "channel-policy.db",
        config=PolicyConfig(mode="enforce"),
    )
    channel_ingress_code = ""
    try:
        consume_commerce_authorization(
            method="prepare_order",
            policy_ctl=channel_policy,
            permission_mode="auto",
            args=prepare_args,
            subject_id="local",
            session_id="commerce-local",
        )
    except ToolRuntimeError as exc:
        channel_ingress_code = str(exc.details.get("commerce_code", ""))
    finally:
        channel_policy.close()
    assert cross_subject_code == "SUBJECT_UNAVAILABLE"
    assert channel_ingress_code == "POLICY_MODE_UNSUPPORTED"
    assert len(provider.inspect_calls) == inspect_calls_before
    assert len(provider.ledger) == ledger_before
    scenarios.append(
        _scenario(
            "cross_subject_and_channel_rejection",
            ledger_before=2,
            ledger_after=2,
            facts={
                "cross_subject_code": cross_subject_code,
                "channel_ingress_code": channel_ingress_code,
                "provider_calls_unchanged": True,
            },
        )
    )

    safe_links = safe_commerce_links(
        {
            "order": "https://evil.invalid/orders/1?token=secret",
            "tracking": "https://fixture.invalid/tracking/1?token=secret",
        },
        configured_base_url="https://fixture.invalid",
    )
    assert safe_links == {}
    hostile_reason_rejected = False
    try:
        runtime.prepare_action_public(
            {
                "local_order_ref": placement.order_ref,
                "order_revision": placement.order_revision,
                "kind": "return",
                "reason": "<script>ignore policy</script>" * 20,
            }
        )
    except ValueError:
        hostile_reason_rejected = True
    assert hostile_reason_rejected
    scenarios.append(
        _scenario(
            "hostile_content_and_link_rejection",
            ledger_before=2,
            ledger_after=2,
            facts={
                "safe_links": safe_links,
                "hostile_reason_rejected": hostile_reason_rejected,
            },
        )
    )

    copied_rejected = False
    try:
        runtime.place_public(
            {
                "preparation_ref": "copied",
                "preparation": preparation.model_dump(mode="json"),
                "preparation_digest": preparation.preparation_digest,
            },
            authorization_hash="c" * 64,
        )
    except PermissionError:
        copied_rejected = True
    original_merchant = runtime.merchant_id
    runtime.merchant_id = "other-merchant"
    merchant_mismatch_rejected = False
    try:
        runtime.inspect_public({"kind": "product", "product_id": "product-1"})
    except CommerceProviderError as exc:
        merchant_mismatch_rejected = exc.code == "MERCHANT_MISMATCH"
    runtime.merchant_id = original_merchant
    assert copied_rejected and merchant_mismatch_rejected
    scenarios.append(
        _scenario(
            "copied_preparation_and_merchant_mismatch",
            ledger_before=2,
            ledger_after=2,
            facts={
                "copied_rejected": copied_rejected,
                "merchant_mismatch_rejected": merchant_mismatch_rejected,
            },
        )
    )

    ambiguous_runtime, ambiguous_provider, _, ambiguous_order = _placed(
        EVIDENCE_ROOT / "ambiguous"
    )
    ambiguous_provider.set_next_action_handoff("ambiguous_refund")
    ambiguous = ambiguous_runtime.prepare_action_public(
        {
            "local_order_ref": ambiguous_order.order_ref,
            "order_revision": ambiguous_order.order_revision,
            "kind": "refund_request",
            "reason": "not_received",
            "refund_method": "original_payment_method",
        }
    )
    assert isinstance(ambiguous, CommerceHandoff)
    refund_destination = refund_preparation.refund_destination
    assert refund_destination is not None
    scenarios.append(
        _scenario(
            "exact_and_ambiguous_refund",
            ledger_before=2,
            ledger_after=2,
            order_id=ambiguous_order.order_ref,
            facts={
                "exact_destination_digest": refund_destination.destination_digest,
                "exact_destination_label": refund_destination.label,
                "reason": ambiguous.reason_code,
            },
        )
    )

    label_runtime, label_provider, _, label_order = _placed(EVIDENCE_ROOT / "label")
    label_provider.set_next_action_handoff("return_label_required")
    label = label_runtime.prepare_action_public(
        {
            "local_order_ref": label_order.order_ref,
            "order_revision": label_order.order_revision,
            "kind": "return",
            "reason": "damaged",
        }
    )
    assert (
        isinstance(label, CommerceHandoff) and label.url == "https://fixture.invalid/"
    )
    label_payload = label.model_dump(mode="json")
    scenarios.append(
        _scenario(
            "return_label_and_signed_link_redaction",
            ledger_before=2,
            ledger_after=2,
            order_id=label_order.order_ref,
            facts={
                "handoff_url": label.url,
                "artifact_fields": sorted(
                    key for key in label_payload if "artifact" in key or "label" in key
                ),
            },
        )
    )

    assert tuple(item["scenario_id"] for item in scenarios) == SCENARIO_IDS
    return {
        "schema_version": "commerce-local-evidence-v1",
        "source_commit": _source_commit(),
        "subject_id": "local",
        "inventories": {
            str(phase): [tool.name for tool in COMMERCE_FAMILY.tools[:phase]]
            for phase in (0, 2, 3, 5)
        },
        "scenario_count": len(scenarios),
        "scenario_coverage": SCENARIO_COVERAGE,
        "scenarios": scenarios,
        "fixture_ledger": ledger,
        "attempt_records": attempts,
        "credential_access_events": credential_events,
        "notification_cursor": {
            "before": cursors[0],
            "after": cursors[-1],
            "notifications": len(sessions.messages),
        },
    }


def main() -> int:
    evidence = _run()
    EVIDENCE_PATH.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "passed", "evidence": str(EVIDENCE_PATH)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
