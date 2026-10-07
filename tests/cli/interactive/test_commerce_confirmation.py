from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openminion.cli.interactive.terminal.shell.approval import (
    build_terminal_approval_callback,
)
from openminion.cli.presentation.tool.progress import build_tool_event_from_progress
from openminion.modules.brain.adapters.tool.policy_context import (
    _resolve_auto_confirm,
)
from openminion.modules.brain.loop.tools.confirmation import (
    confirmation_required_user_message,
    requires_individual_confirmation,
)
from openminion.modules.brain.schemas import ToolCommand
from openminion.modules.commerce.confirmation import (
    ExactOrderConfirmationPreview,
    OrderActionConfirmationPreview,
    PreparationIntentConfirmationPreview,
    commerce_confirmation_payload,
    commerce_confirmation_lines,
)
from openminion.modules.commerce.models import CommerceHandoff, CommerceLifecycleState
from openminion.modules.commerce.provider import OrderPlacement
from openminion.modules.policy.adapters.brain import PolicyCtlBrainAdapter
from openminion.modules.policy.models import PolicyConfig, RiskSpec
from openminion.modules.policy.runtime.action_policy import derive_tool_risk_spec
from openminion.modules.policy.runtime.service import PolicyCtl


_DIGEST = "sha256:" + "a" * 64
_PREPARATION_EFFECT = (
    "Create or refresh one merchant checkout without placing an order or "
    "capturing payment."
)


def _preparation_preview(
    *, merchant: str = "Fixture Merchant"
) -> PreparationIntentConfirmationPreview:
    return PreparationIntentConfirmationPreview(
        merchant=merchant,
        items=(
            {
                "offer_id": "offer-1",
                "variant_id": "blue-medium",
                "quantity": 2,
            },
        ),
        promotion_code="SAVE10",
        buyer_label="Saved buyer",
        destination_label="Home ending 42",
        payment_label="Visa ending 4242",
        subject_id="local",
        session_id="session-1",
        expires_at="2026-10-06T20:00:00+00:00",
        consequence=_PREPARATION_EFFECT,
    )


def _order_preview(
    *, merchant: str = "Fixture Merchant"
) -> ExactOrderConfirmationPreview:
    return ExactOrderConfirmationPreview(
        merchant=merchant,
        seller="Fixture Seller",
        preparation_ref="prep-1",
        items=(
            {
                "offer_id": "offer-1",
                "variant_id": "blue-medium",
                "quantity": 2,
                "line_total_minor": 2400,
                "returnable": True,
                "final_sale": False,
            },
        ),
        discount_minor=100,
        tax_minor=200,
        shipping_minor=300,
        fees_minor=0,
        total_minor=2800,
        currency="USD",
        destination_label="Home ending 42",
        buyer_profile_digest=_DIGEST,
        destination_digest=_DIGEST,
        payment_label="Visa ending 4242",
        payment_destination_digest=_DIGEST,
        recurring=False,
        warnings=("Return within 30 days",),
        policy_links=({"kind": "returns", "url": "https://shop.test/returns"},),
        checkout_revision="checkout-r1",
        expires_at="2026-10-06T20:00:00+00:00",
        preparation_digest=_DIGEST,
        subject_id="local",
        session_id="session-1",
    )


def test_typed_previews_bind_order_and_refund_facts() -> None:
    order = _order_preview()
    action = OrderActionConfirmationPreview(
        order_id="order-1",
        order_revision="order-r2",
        action_kind="partial_return",
        affected_items=({"line_item_id": "line-1", "quantity": 1},),
        fees_minor=0,
        refund_minor=1200,
        currency="USD",
        refund_method="original_payment_method",
        refund_destination_digest=_DIGEST,
        refund_destination_label="Visa ending 4242",
        deadlines=("Ship by 2026-10-20",),
        consequence="Request one partial return.",
        action_revision="action-r1",
        expires_at="2026-10-06T20:00:00+00:00",
        action_digest=_DIGEST,
        subject_id="local",
        session_id="session-1",
    )

    order_payload = commerce_confirmation_payload(order)
    action_payload = commerce_confirmation_payload(action)
    assert order_payload["total_minor"] == 2800
    assert order_payload["recurring"] is False
    assert action_payload["refund_method"] == "original_payment_method"
    assert action_payload["refund_destination_digest"] == _DIGEST
    assert (
        "Refund consent: 1200 USD minor units to original payment method at Visa ending 4242."
        in commerce_confirmation_lines(action)
    )
    with pytest.raises(ValidationError):
        OrderActionConfirmationPreview.model_validate(
            {**action_payload, "refund_method": "merchant_choice"}
        )


def test_prepare_risk_is_exact_external_reversible_confirmation() -> None:
    risk = derive_tool_risk_spec(
        tool_name="commerce.prepare_order",
        tool=SimpleNamespace(
            min_scope="WRITE_SAFE",
            dangerous=False,
            idempotent=True,
            policy=None,
        ),
    )
    assert risk == RiskSpec(
        risk_class="state_change",
        side_effects="external_account",
        reversibility="reversible",
        default_confirm=True,
    )


def test_policy_adapter_uses_trusted_resolver_and_returns_typed_preview(
    tmp_path: Path,
) -> None:
    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    ctl.register_risk(
        "commerce.prepare_order",
        derive_tool_risk_spec(
            tool_name="commerce.prepare_order",
            tool=SimpleNamespace(),
        ),
    )
    seen: dict[str, object] = {}

    def resolve_preview(**facts: object) -> PreparationIntentConfirmationPreview:
        seen.update(facts)
        return _preparation_preview()

    adapter = PolicyCtlBrainAdapter(
        ctl,
        commerce_confirmation_resolver=resolve_preview,
    )
    command = ToolCommand(
        kind="tool",
        title="Prepare checkout",
        tool_name="commerce.prepare_order",
        args={"items": [{"offer_id": "offer-1", "quantity": 2}]},
        inputs={},
    )
    state = SimpleNamespace(
        session_id="session-1",
        agent_id="agent-1",
        trace_id="trace-1",
        session_action_policy_mode_override=None,
    )
    try:
        decision = adapter.evaluate(
            command=command,
            working_state=state,
            session_context={"subject_id": "local", "mode_name": "act"},
        )
        assert decision.outcome == "REQUIRE_CONFIRMATION"
        assert decision.confirmation_preview == commerce_confirmation_payload(
            _preparation_preview()
        )
        assert seen["subject_id"] == "local"
        assert seen["session_id"] == "session-1"
    finally:
        adapter.close()


@pytest.mark.parametrize(
    "tool_name",
    [
        "commerce.prepare_order",
        "commerce.place_order",
        "commerce.apply_order_action",
    ],
)
def test_commerce_confirmation_is_individual_and_never_auto(
    tool_name: str,
) -> None:
    command = ToolCommand(
        kind="tool",
        title="Commerce action",
        tool_name=tool_name,
        args={},
        inputs={},
    )
    assert requires_individual_confirmation(command) is True
    for permission_mode in ("auto", "bypass"):
        assert (
            _resolve_auto_confirm(
                tool_name=tool_name,
                args={},
                permission_mode=permission_mode,
                replay_confirmed=False,
                background_write_authorized=True,
            )
            is False
        )
    assert (
        _resolve_auto_confirm(
            tool_name=tool_name,
            args={},
            permission_mode="ask",
            replay_confirmed=True,
            background_write_authorized=False,
        )
        is True
    )


def test_shared_confirmation_message_is_one_time_and_escapes_merchant_text() -> None:
    command = ToolCommand(
        kind="tool",
        title="Prepare checkout",
        tool_name="commerce.prepare_order",
        args={},
        inputs={},
    )
    rendered = confirmation_required_user_message(
        command,
        commerce_confirmation_payload(
            _preparation_preview(merchant="[red]Fake total\n\x1b[2J$0")
        ),
    )
    assert "Merchant: \\[red]Fake total $0" in rendered
    assert "without placing an order or capturing payment" in rendered
    assert rendered.splitlines()[-1] == (
        "Reply exactly yes to allow once, or no to cancel."
    )
    assert "allow this tool for the session" not in rendered


def test_preparation_and_exact_order_approvals_are_separate_snapshots() -> None:
    hostile = "[red]Merchant\n\x1b[2J" + "x" * 400
    prepare = confirmation_required_user_message(
        ToolCommand(
            kind="tool",
            title="Prepare checkout",
            tool_name="commerce.prepare_order",
            args={},
            inputs={},
        ),
        commerce_confirmation_payload(_preparation_preview(merchant=hostile)),
    )
    place = confirmation_required_user_message(
        ToolCommand(
            kind="tool",
            title="Place order",
            tool_name="commerce.place_order",
            args={},
            inputs={},
        ),
        commerce_confirmation_payload(_order_preview(merchant=hostile)),
    )

    assert "Commerce checkout preparation" in prepare
    assert "Exact total:" not in prepare
    assert "Exact order review" in place
    assert "Exact total: 2800 USD minor units" in place
    assert "\x1b" not in prepare + place
    assert "Merchant\n" not in prepare + place
    assert "x" * 241 not in prepare + place
    for rendered in (prepare, place):
        assert rendered.endswith("Reply exactly yes to allow once, or no to cancel.")
        assert "session" not in rendered.splitlines()[-1].lower()
        assert "always" not in rendered.lower()


def test_receipt_unknown_outcome_and_handoff_have_clear_next_actions() -> None:
    lifecycle = CommerceLifecycleState(
        order="outcome_unknown",
        fulfillment="provider_unknown",
        payment="provider_unknown",
    )
    receipt = OrderPlacement(
        idempotency_key="place-1",
        preparation_ref="prep-1",
        state="outcome_unknown",
        lifecycle=lifecycle,
    )
    receipt_text = build_tool_event_from_progress(
        {
            "tool_name": "commerce.place_order",
            "content": receipt.model_dump_json(),
        }
    ).content
    handoff_text = build_tool_event_from_progress(
        {
            "tool_name": "commerce.place_order",
            "content": CommerceHandoff(
                reason_code="merchant_support_required",
                message="Call [red]support\n\x1b[2J before continuing",
                url="https://shop.test/support",
            ).model_dump_json(),
        }
    ).content

    assert "Order receipt" in receipt_text
    assert "State: outcome_unknown" in receipt_text
    assert "do not place it again yet" in receipt_text
    assert "Merchant handoff required" in handoff_text
    assert "Message: Call \\[red]support before continuing" in handoff_text
    assert "inspect again, and request a new approval" in handoff_text
    assert "\x1b" not in handoff_text


@pytest.mark.asyncio
async def test_terminal_commerce_callback_offers_allow_once_only() -> None:
    prompts: list[str] = []

    class _Overlay:
        async def present_confirm_async(self, prompt: str) -> bool:
            prompts.append(prompt)
            return True

        async def present_approval_async(
            self, *_args: object, **_kwargs: object
        ) -> str:
            raise AssertionError("commerce must not offer session approval")

    session_grants: set[str] = set()
    callback = build_terminal_approval_callback(
        overlay=_Overlay(),
        session_grants=session_grants,
    )
    assert await callback("commerce.prepare_order", {"items": []}, "a")
    assert await callback("commerce.place_order", {"preparation_ref": "prep-1"}, "b")
    assert len(prompts) == 2
    assert "commerce.prepare_order" in prompts[0]
    assert "commerce.place_order" in prompts[1]
    assert all("allow once or deny" in prompt.lower() for prompt in prompts)
    assert all("session" not in prompt.lower() for prompt in prompts)
    assert all("always" not in prompt.lower() for prompt in prompts)
    assert session_grants == set()
