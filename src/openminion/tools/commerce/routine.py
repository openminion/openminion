from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openminion.modules.policy.constants import POLICY_SUBJECT_ID_LOCAL
from .models import CommerceLifecycleState

if TYPE_CHECKING:
    from openminion.tools.task.routine.dispatcher import PreTurnContext, PostTurnResult
    from openminion.tools.task.routine.schemas import RoutinePayloadV1

ROUTINE_KIND_COMMERCE_ORDER: Literal["commerce_order"] = "commerce_order"


class CommerceOrderConfigV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: Literal["local"] = POLICY_SUBJECT_ID_LOCAL
    local_order_ref: str = Field(min_length=1)
    expires_at: str = Field(min_length=1)
    terminal_policy: Literal["order_terminal", "fully_settled"] = "fully_settled"
    max_failures: int = Field(default=3, ge=1, le=8)


class CommerceOrderCursorV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_cursor: str | None = Field(default=None, min_length=1)
    open_shipment_ids: tuple[str, ...] = ()
    open_action_ids: tuple[str, ...] = ()
    failure_count: int = Field(default=0, ge=0, le=8)


class CommerceOrderFactsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "paused", "expired", "denied", "failed"]
    material_cursor: str | None = Field(default=None, min_length=1)
    lifecycle: CommerceLifecycleState | None = None
    open_shipment_ids: tuple[str, ...] = ()
    open_action_ids: tuple[str, ...] = ()
    detail: str = ""


class CommerceOrderHandler:
    routine_kind = ROUTINE_KIND_COMMERCE_ORDER
    model_turn_tools: tuple[str, ...] = ()
    finalizer_watch_overrides: Mapping[str, Any] = {
        "stop_on_condition": False,
        "deliver_resolution": False,
        "delivery_cooldown_minutes": 0,
    }
    requires_model_turn = False

    def pre_turn_tools_for(self, routine: RoutinePayloadV1) -> tuple[str, ...]:
        del routine
        return ("commerce.inspect",)

    def pre_turn(
        self,
        *,
        routine: RoutinePayloadV1,
        routine_id: str,
        ctx: PreTurnContext,
    ) -> CommerceOrderFactsV1:
        del routine_id
        config = cast(CommerceOrderConfigV1, routine.config)
        if _is_expired(config.expires_at):
            return CommerceOrderFactsV1(status="expired", detail="routine expired")
        if not ctx.tool_family_enabled(family="commerce"):
            return CommerceOrderFactsV1(status="paused", detail="commerce disabled")
        result = ctx.invoke_tool(
            name="commerce.inspect",
            args={"kind": "order", "local_order_ref": config.local_order_ref},
        )
        if not result.get("ok", False):
            error = result.get("error")
            error_data = dict(error) if isinstance(error, Mapping) else {}
            denied = str(error_data.get("code", "")).strip() == "POLICY_DENIED"
            return CommerceOrderFactsV1(
                status="denied" if denied else "failed",
                detail=str(error_data.get("message", "commerce inspection failed")),
            )
        data = result.get("data")
        payload = dict(data) if isinstance(data, Mapping) else {}
        nested = payload.get("inspection")
        if isinstance(nested, Mapping):
            payload = dict(nested)
        try:
            material_cursor = str(payload["revision"]).strip()
            lifecycle = CommerceLifecycleState.model_validate(payload["lifecycle"])
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            return CommerceOrderFactsV1(status="failed", detail=str(exc))
        if not material_cursor:
            return CommerceOrderFactsV1(
                status="failed", detail="commerce inspection revision is empty"
            )
        open_shipments = tuple(
            sorted(
                shipment_id
                for shipment_id, state in lifecycle.shipments.items()
                if state not in {"delivered", "returned", "lost"}
            )
        )
        open_actions = _string_ids(payload.get("open_action_ids"))
        return CommerceOrderFactsV1(
            status="ok",
            material_cursor=material_cursor,
            lifecycle=lifecycle,
            open_shipment_ids=open_shipments,
            open_action_ids=open_actions,
        )

    def render_turn(self, *, check_instruction: str, facts: BaseModel) -> str:
        del check_instruction, facts
        return ""

    def post_turn(
        self,
        *,
        routine: RoutinePayloadV1,
        routine_id: str,
        facts: BaseModel,
        outcome_text: str,
    ) -> PostTurnResult:
        from openminion.tools.task.routine.dispatcher import PostTurnResult

        del routine_id, outcome_text
        config = cast(CommerceOrderConfigV1, routine.config)
        cursor = cast(CommerceOrderCursorV1, routine.cursor)
        typed_facts = cast(CommerceOrderFactsV1, facts)
        if typed_facts.status == "paused":
            return PostTurnResult(
                ok=True,
                condition_value=None,
                updated_routine=routine,
                metadata={"routine_paused": True},
            )
        if typed_facts.status in {"expired", "denied"}:
            reason = (
                "commerce_order_expired"
                if typed_facts.status == "expired"
                else "commerce_order_subject_denied"
            )
            return PostTurnResult(
                ok=True,
                condition_value=True,
                summary_line=(
                    f"Order monitoring stopped for {config.local_order_ref}: "
                    f"{typed_facts.detail}."
                ),
                updated_routine=routine,
                metadata={"routine_terminal": True, "terminal_policy": reason},
            )
        if typed_facts.status == "failed":
            failure_count = min(config.max_failures, cursor.failure_count + 1)
            terminal = failure_count >= config.max_failures
            updated_cursor = cursor.model_copy(update={"failure_count": failure_count})
            updated = routine.model_copy(update={"cursor": updated_cursor})
            return PostTurnResult(
                ok=True,
                condition_value=True if terminal else None,
                summary_line=(
                    f"Order monitoring stopped for {config.local_order_ref} after "
                    f"{failure_count} failed inspections."
                    if terminal
                    else ""
                ),
                updated_routine=updated,
                metadata={
                    "routine_terminal": terminal,
                    "failure_count": failure_count,
                },
            )
        lifecycle = typed_facts.lifecycle
        if lifecycle is None or typed_facts.material_cursor is None:
            raise ValueError("commerce order facts are incomplete")
        changed = typed_facts.material_cursor != cursor.material_cursor
        order_terminal = lifecycle.order in {"cancelled", "completed", "declined"}
        action_open = lifecycle.action_request in {
            "prepared",
            "submitting",
            "pending",
            "outcome_unknown",
            "provider_unknown",
        }
        terminal = order_terminal and (
            config.terminal_policy == "order_terminal"
            or (
                not typed_facts.open_shipment_ids
                and not typed_facts.open_action_ids
                and not action_open
            )
        )
        updated_cursor = CommerceOrderCursorV1(
            material_cursor=typed_facts.material_cursor,
            open_shipment_ids=typed_facts.open_shipment_ids,
            open_action_ids=typed_facts.open_action_ids,
            failure_count=0,
        )
        return PostTurnResult(
            ok=True,
            condition_value=changed or terminal,
            summary_line=(
                _render_commerce_summary(config.local_order_ref, typed_facts)
                if changed or terminal
                else ""
            ),
            updated_routine=routine.model_copy(update={"cursor": updated_cursor}),
            metadata={
                "routine_terminal": terminal,
                "material_cursor": typed_facts.material_cursor,
                "open_shipment_ids": list(typed_facts.open_shipment_ids),
                "open_action_ids": list(typed_facts.open_action_ids),
            },
        )


def _is_expired(expires_at: str) -> bool:
    try:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires <= datetime.now(timezone.utc)


def _string_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(sorted({str(item).strip() for item in value if str(item).strip()}))


def _render_commerce_summary(order_ref: str, facts: CommerceOrderFactsV1) -> str:
    lifecycle = facts.lifecycle
    if lifecycle is None:
        return ""
    summary = (
        f"Order {order_ref}: {lifecycle.order}; fulfillment {lifecycle.fulfillment}; "
        f"payment {lifecycle.payment}."
    )
    if facts.open_shipment_ids:
        summary += " Open shipments: " + ", ".join(facts.open_shipment_ids) + "."
    if facts.open_action_ids:
        summary += " Open actions: " + ", ".join(facts.open_action_ids) + "."
    return summary
