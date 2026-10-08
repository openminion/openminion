from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from openminion.tools.commerce.constants import COMMERCE_LOCAL_SUBJECT_ID
from openminion.tools.commerce.models import CommerceLifecycleState
from openminion.modules.tool import ToolRegistry, ToolSpec
from openminion.tools.commerce.plugin import resolve_commerce_runtime
from openminion.tools.commerce.provider import PlaceOrderRequest
from openminion.tools.commerce.registrar import REGISTRAR
from openminion.tools.commerce.routine import CommerceOrderHandler
from openminion.services.runtime.cron.delivery import CronDeliveryBridge
from openminion.services.runtime.cron.executor import CronTurnExecutor
from openminion.services.runtime.routine_context import (
    ToolRegistryPreTurnContext,
    build_routine_pre_turn_context,
)
from openminion.tools.task.constants import WATCH_PAYLOAD_KEY
from openminion.tools.task.routine.schemas import (
    CommerceOrderConfigV1,
    CommerceOrderCursorV1,
    RoutinePayloadV1,
)
from tests.helpers.commerce_runtime import build_fixture_commerce_runtime


class _Context:
    def __init__(
        self, responses: list[dict[str, Any]], *, enabled: bool = True
    ) -> None:
        self.responses = responses
        self.enabled = enabled
        self.calls = 0

    def tool_family_enabled(self, *, family: str) -> bool:
        assert family == "commerce"
        return self.enabled

    def exact_provider_enabled(self, *, family: str, provider_id: str) -> bool:
        del family, provider_id
        return False

    def invoke_tool(self, *, name: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
        assert name == "commerce.inspect"
        assert args == {"kind": "order", "local_order_ref": "order-local-1"}
        self.calls += 1
        return self.responses.pop(0)


class _CronStore:
    def __init__(self) -> None:
        self.replaced: list[tuple[str, dict[str, Any]]] = []

    def replace_cron_job_payload(self, job_id: str, payload: dict[str, Any]) -> bool:
        self.replaced.append((job_id, payload))
        return True


class _Sessions:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    def append_message(self, **payload: Any) -> None:
        self.messages.append(payload)

    def append_event(self, **payload: Any) -> None:
        self.events.append(payload)


def _response(
    revision: str,
    shipment_state: str = "in_transit",
    *,
    order: str = "accepted",
) -> dict[str, Any]:
    lifecycle = CommerceLifecycleState(
        order=order,
        fulfillment="unfulfilled",
        payment="authorized",
        shipments={"shipment-out": shipment_state},
    )
    return {
        "ok": True,
        "data": {
            "revision": revision,
            "lifecycle": lifecycle.model_dump(mode="json"),
            "open_action_ids": [],
        },
    }


def _job(routine: RoutinePayloadV1 | None = None) -> dict[str, Any]:
    routine = routine or RoutinePayloadV1(
        routine_kind="commerce_order",
        config=CommerceOrderConfigV1(
            subject_id="local",
            local_order_ref="order-local-1",
            expires_at="2099-01-01T00:00:00Z",
        ),
        cursor=CommerceOrderCursorV1(),
    )
    return {
        "job_id": "job-commerce-1",
        "agent_id": "agent-1",
        "payload": {
            "kind": "agentTurn",
            "session_id": "session-1",
            WATCH_PAYLOAD_KEY: {
                "checks_completed": 0,
                "max_checks": 20,
                "ttl_minutes": 1440,
                "stop_on_condition": False,
                "routine": routine.model_dump(mode="json"),
            },
            "_openminion_origin": {"session_id": "session-1"},
        },
        "delivery": {"mode": "announce", "to": "last"},
    }


def _executor(
    cron_store: _CronStore,
    *,
    tool_resources: Mapping[str, Any] | None = None,
) -> CronTurnExecutor:
    return CronTurnExecutor(
        runtime=SimpleNamespace(
            runtime_manager=object(),
            tool_resources=dict(tool_resources or {}),
        ),
        cron_store=cron_store,
        request_builder=lambda payload, agent_id: (payload, agent_id),
        timeout_s=10,
        max_attempts=1,
    )


def test_restart_preserves_cursor_and_unchanged_revision_stays_silent(
    monkeypatch,
) -> None:
    context = _Context([_response("revision-1"), _response("revision-1")])
    monkeypatch.setattr(
        "openminion.services.runtime.cron.executor.build_routine_pre_turn_context",
        lambda **_kwargs: context,
    )
    first_store = _CronStore()
    first = _executor(first_store).execute(_job(), {"run_id": "run-1"})

    assert first["output"]["watch_delivery_requested"] is True
    persisted = first_store.replaced[-1][1][WATCH_PAYLOAD_KEY]["routine"]
    revived = RoutinePayloadV1.model_validate(persisted)
    assert revived.cursor.material_cursor == "revision-1"

    restarted_store = _CronStore()
    second = _executor(restarted_store).execute(_job(revived), {"run_id": "run-2"})

    assert second["output"]["watch_delivery_requested"] is False
    assert second["summary"] == "routine=commerce_order | no-op"
    assert context.calls == 2

    sessions = _Sessions()
    CronDeliveryBridge(runtime=SimpleNamespace(sessions=sessions)).deliver(
        "announce",
        "last",
        _job(revived),
        {"run_id": "run-2"},
        second,
    )
    assert sessions.messages == []
    assert sessions.events == []


def test_disabled_family_pauses_without_inspection_or_model_turn(monkeypatch) -> None:
    context = _Context([], enabled=False)
    commerce_runtime = object()
    context_args: dict[str, Any] = {}

    def build_context(**kwargs):
        context_args.update(kwargs)
        return context

    monkeypatch.setattr(
        "openminion.services.runtime.cron.executor.build_routine_pre_turn_context",
        build_context,
    )
    cron_store = _CronStore()

    result = _executor(
        cron_store, tool_resources={"commerce": commerce_runtime}
    ).execute(_job(), {"run_id": "run-1"})

    assert result["summary"] == ""
    assert result["output"]["watch_delivery_requested"] is False
    assert result["output"]["watch_checks_completed"] == 0
    assert context.calls == 0
    assert cron_store.replaced == []
    assert context_args["subject_id"] == COMMERCE_LOCAL_SUBJECT_ID
    assert context_args["tool_resources"]["commerce"] is commerce_runtime


def test_cancelled_order_stops_the_routine(monkeypatch) -> None:
    context = _Context(
        [_response("revision-cancelled", "delivered", order="cancelled")]
    )
    monkeypatch.setattr(
        "openminion.services.runtime.cron.executor.build_routine_pre_turn_context",
        lambda **_kwargs: context,
    )

    result = _executor(_CronStore()).execute(_job(), {"run_id": "run-cancelled"})

    assert result["output"]["watch_terminal"] is True
    assert result["output"]["watch_delivery_requested"] is True


def test_routine_inspects_owned_order_through_registered_commerce_tool(
    tmp_path,
) -> None:
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    try:
        preparation = runtime.prepare_public(
            {
                "items": [
                    {"offer_id": "offer-1", "variant_id": "standard", "quantity": 1}
                ]
            }
        )
        order = runtime.place_order(
            PlaceOrderRequest(
                idempotency_key="routine-fixture-order",
                preparation_ref=preparation.preparation_ref,
                preparation_digest=preparation.preparation_digest,
            )
        )
        routine = RoutinePayloadV1(
            routine_kind="commerce_order",
            config=CommerceOrderConfigV1(
                local_order_ref=order.order_ref,
                expires_at="2099-01-01T00:00:00Z",
            ),
            cursor=CommerceOrderCursorV1(),
        )
        registry = ToolRegistry()
        REGISTRAR.register(registry)
        handler = CommerceOrderHandler()
        context = ToolRegistryPreTurnContext(
            registry=registry,
            session_id="session-1",
            subject_id="local",
            allowed_tools=handler.pre_turn_tools_for(routine),
            metadata={"runtime_tools": {"commerce": {"enabled": True}}},
            tool_resources={"commerce": runtime},
        )
        ledger_before = list(provider.ledger)

        facts = handler.pre_turn(routine=routine, routine_id="routine-1", ctx=context)

        assert facts.status == "ok", facts.detail
        assert facts.material_cursor
        assert facts.lifecycle == order.lifecycle
        assert len(provider.inspect_calls) == 1
        assert provider.inspect_calls[0].order_ref == order.order_ref
        assert provider.ledger == ledger_before
    finally:
        runtime.order_store.close()


def test_routine_tool_context_uses_trusted_subject_and_runtime_service(
    monkeypatch,
) -> None:
    commerce_runtime = object()
    captured: list[tuple[object, str, str]] = []

    def inspect(_args, context):
        captured.append(
            (
                resolve_commerce_runtime(context),
                context.subject_id,
                str(context.policy.raw["context_metadata"]["subject_id"]),
            )
        )
        return {"ok": True}

    registry = ToolRegistry()
    registry.add(
        ToolSpec(
            name="commerce.inspect",
            args_model=dict,
            min_scope="READ_ONLY",
            handler=inspect,
        )
    )
    monkeypatch.setattr(
        "openminion.services.runtime.routine_context.build_runtime_tool_routing_metadata",
        lambda _config: {"subject_id": "remote"},
    )
    context = build_routine_pre_turn_context(
        runtime=SimpleNamespace(
            tools=registry,
            config=SimpleNamespace(runtime=SimpleNamespace(tools=None)),
            tool_resources={"commerce": commerce_runtime},
        ),
        routine_id="routine-1",
        session_id="session-1",
        agent_id="agent-1",
        allowed_tools=("commerce.inspect",),
        subject_id=COMMERCE_LOCAL_SUBJECT_ID,
        tool_resources={"commerce": commerce_runtime},
    )

    assert context is not None
    assert (
        context.invoke_tool(
            name="commerce.inspect",
            args={"kind": "order", "local_order_ref": "order-local-1"},
        )["ok"]
        is True
    )
    assert captured == [
        (commerce_runtime, COMMERCE_LOCAL_SUBJECT_ID, "remote"),
    ]


def test_non_commerce_watch_without_delivery_flag_keeps_existing_delivery() -> None:
    sessions = _Sessions()
    job = _job()
    job["payload"][WATCH_PAYLOAD_KEY]["routine"] = {"routine_kind": "github_pr_review"}

    CronDeliveryBridge(runtime=SimpleNamespace(sessions=sessions)).deliver(
        "announce",
        "last",
        job,
        {"run_id": "run-existing"},
        {"summary": "existing routine result", "output": {}},
    )

    assert len(sessions.messages) == 1
    assert sessions.messages[0]["body"] == "existing routine result"
