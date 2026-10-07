from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import ValidationError

from openminion.modules.commerce.models import CommerceLifecycleState
from openminion.tools.task.routine.dispatcher import CommerceOrderHandler
from openminion.tools.task.routine.schemas import (
    CommerceOrderConfigV1,
    CommerceOrderCursorV1,
    RoutinePayloadV1,
)


class _Context:
    def __init__(
        self, responses: list[Mapping[str, Any]], *, enabled: bool = True
    ) -> None:
        self.responses = list(responses)
        self.enabled = enabled
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def tool_family_enabled(self, *, family: str) -> bool:
        assert family == "commerce"
        return self.enabled

    def exact_provider_enabled(self, *, family: str, provider_id: str) -> bool:
        del family, provider_id
        return False

    def invoke_tool(self, *, name: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((name, dict(args)))
        return self.responses.pop(0)


def _routine(
    *,
    expires_at: str = "2099-01-01T00:00:00Z",
    max_failures: int = 3,
    cursor: CommerceOrderCursorV1 | None = None,
) -> RoutinePayloadV1:
    return RoutinePayloadV1(
        routine_kind="commerce_order",
        config=CommerceOrderConfigV1(
            subject_id="local",
            local_order_ref="order-local-1",
            expires_at=expires_at,
            max_failures=max_failures,
        ),
        cursor=cursor or CommerceOrderCursorV1(),
    )


def _inspection(
    revision: str,
    *,
    order: str = "accepted",
    shipments: dict[str, str] | None = None,
    action_request: str | None = None,
    open_action_ids: list[str] | None = None,
) -> dict[str, Any]:
    lifecycle = CommerceLifecycleState.model_validate(
        {
            "order": order,
            "fulfillment": "unfulfilled",
            "payment": "authorized",
            "shipments": shipments or {},
            "action_request": action_request,
        }
    )
    return {
        "ok": True,
        "data": {
            "kind": "order",
            "reference": "order-local-1",
            "revision": revision,
            "lifecycle": lifecycle.model_dump(mode="json"),
            "open_action_ids": open_action_ids or [],
        },
    }


def _run(
    handler: CommerceOrderHandler,
    routine: RoutinePayloadV1,
    response: Mapping[str, Any],
):
    facts = handler.pre_turn(
        routine=routine,
        routine_id="routine-1",
        ctx=_Context([response]),
    )
    return handler.post_turn(
        routine=routine,
        routine_id="routine-1",
        facts=facts,
        outcome_text="",
    )


def test_payload_round_trips_subject_order_cursor_and_terminal_policy() -> None:
    routine = _routine(
        cursor=CommerceOrderCursorV1(
            material_cursor="revision-1",
            open_shipment_ids=("shipment-out", "shipment-return"),
            open_action_ids=("action-return",),
            failure_count=2,
        )
    )

    revived = RoutinePayloadV1.model_validate(routine.model_dump(mode="json"))

    assert revived.routine_kind == "commerce_order"
    assert revived.config.subject_id == "local"
    assert revived.config.local_order_ref == "order-local-1"
    assert revived.config.expires_at == "2099-01-01T00:00:00Z"
    assert revived.config.terminal_policy == "fully_settled"
    assert revived.cursor.material_cursor == "revision-1"
    assert revived.cursor.open_shipment_ids == (
        "shipment-out",
        "shipment-return",
    )


def test_handler_calls_only_inspect_and_notifies_once_per_material_revision() -> None:
    handler = CommerceOrderHandler()
    routine = _routine()
    ctx = _Context([_inspection("revision-1")])

    facts = handler.pre_turn(routine=routine, routine_id="routine-1", ctx=ctx)
    first = handler.post_turn(
        routine=routine,
        routine_id="routine-1",
        facts=facts,
        outcome_text="",
    )

    assert ctx.calls == [
        (
            "commerce.inspect",
            {"kind": "order", "order_ref": "order-local-1"},
        )
    ]
    assert first.condition_value is True
    assert first.updated_routine is not None
    second = _run(handler, first.updated_routine, _inspection("revision-1"))
    assert second.condition_value is False
    assert second.summary_line == ""
    assert second.updated_routine is not None
    third = _run(handler, second.updated_routine, _inspection("revision-2"))
    assert third.condition_value is True
    assert "Order order-local-1" in third.summary_line


def test_split_outbound_and_return_shipments_continue_until_settled() -> None:
    handler = CommerceOrderHandler()
    first = _run(
        handler,
        _routine(),
        _inspection(
            "revision-1",
            order="completed",
            shipments={
                "shipment-out": "in_transit",
                "shipment-return": "return_in_transit",
            },
            open_action_ids=["action-return"],
        ),
    )

    assert first.metadata["routine_terminal"] is False
    assert first.updated_routine.cursor.open_shipment_ids == (
        "shipment-out",
        "shipment-return",
    )
    second = _run(
        handler,
        first.updated_routine,
        _inspection(
            "revision-2",
            order="completed",
            shipments={
                "shipment-out": "delivered",
                "shipment-return": "return_in_transit",
            },
        ),
    )
    assert second.metadata["routine_terminal"] is False
    assert second.updated_routine.cursor.open_shipment_ids == ("shipment-return",)
    third = _run(
        handler,
        second.updated_routine,
        _inspection(
            "revision-3",
            order="completed",
            shipments={
                "shipment-out": "delivered",
                "shipment-return": "returned",
            },
        ),
    )
    assert third.metadata["routine_terminal"] is True


def test_completed_order_with_pending_action_remains_active() -> None:
    result = _run(
        CommerceOrderHandler(),
        _routine(),
        _inspection(
            "revision-1",
            order="completed",
            action_request="pending",
        ),
    )

    assert result.metadata["routine_terminal"] is False


def test_failure_bound_expiry_cancellation_and_subject_denial() -> None:
    handler = CommerceOrderHandler()
    failure = {"ok": False, "error": {"code": "UPSTREAM", "message": "down"}}
    first = _run(handler, _routine(max_failures=2), failure)
    assert first.condition_value is None
    assert first.updated_routine.cursor.failure_count == 1
    second = _run(handler, first.updated_routine, failure)
    assert second.condition_value is True
    assert second.metadata["routine_terminal"] is True

    expired_ctx = _Context([])
    expired_facts = handler.pre_turn(
        routine=_routine(expires_at="2020-01-01T00:00:00Z"),
        routine_id="routine-1",
        ctx=expired_ctx,
    )
    assert expired_ctx.calls == []
    assert expired_facts.status == "expired"
    cancelled = _run(
        handler,
        _routine(),
        _inspection("revision-cancelled", order="cancelled"),
    )
    assert cancelled.metadata["routine_terminal"] is True
    denied = _run(
        handler,
        _routine(),
        {
            "ok": False,
            "error": {"code": "POLICY_DENIED", "message": "subject mismatch"},
        },
    )
    assert denied.metadata["routine_terminal"] is True
    assert denied.metadata["terminal_policy"] == "commerce_order_subject_denied"

    with pytest.raises(ValidationError):
        CommerceOrderConfigV1(
            subject_id="remote",
            local_order_ref="order-local-1",
            expires_at="2099-01-01T00:00:00Z",
        )


def test_disabled_commerce_quietly_pauses_without_tool_call() -> None:
    handler = CommerceOrderHandler()
    ctx = _Context([], enabled=False)

    facts = handler.pre_turn(routine=_routine(), routine_id="routine-1", ctx=ctx)
    post = handler.post_turn(
        routine=_routine(),
        routine_id="routine-1",
        facts=facts,
        outcome_text="",
    )

    assert ctx.calls == []
    assert facts.status == "paused"
    assert post.condition_value is None
    assert post.metadata["routine_paused"] is True
    assert post.summary_line == ""
