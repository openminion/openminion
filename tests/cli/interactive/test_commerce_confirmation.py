from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openminion.cli.interactive.terminal.shell.approval import (
    build_terminal_approval_callback,
)
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
)
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


def test_typed_previews_bind_order_and_refund_facts() -> None:
    order = ExactOrderConfirmationPreview(
        merchant="Fixture Merchant",
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
    assert await callback("commerce.place_order", {"preparation_ref": "prep-1"}, "a")
    assert await callback("commerce.place_order", {"preparation_ref": "prep-1"}, "b")
    assert len(prompts) == 2
    assert session_grants == set()
