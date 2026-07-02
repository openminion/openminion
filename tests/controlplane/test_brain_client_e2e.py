from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from openminion.modules.controlplane.runtime.audit import AuditLogger
from openminion.modules.controlplane.runtime.auth import AuthEvaluator
from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.runtime.dispatcher import ControlPlaneDispatcher
from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.adapters.client import (
    OpenMinionBrainClient,
)
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore


@dataclass
class _StubMessage:
    body: str
    channel: str = "console"
    target: str = "controlplane"
    metadata: dict[str, Any] | None = None


class _StubGateway:
    def __init__(self, reply_text: str, *, raise_500: bool = False) -> None:
        self.reply_text = reply_text
        self.raise_500 = raise_500
        self.calls: list[dict[str, Any]] = []

    async def run_once(
        self,
        *,
        channel: str,
        target: str,
        message: str,
        session_id: str,
        idempotency_key: str,
        request_id: str,
        inbound_metadata: dict[str, str],
        deliver: bool,
    ) -> _StubMessage:
        self.calls.append(
            {
                "channel": channel,
                "target": target,
                "message": message,
                "session_id": session_id,
                "idempotency_key": idempotency_key,
                "request_id": request_id,
                "inbound_metadata": dict(inbound_metadata),
                "deliver": deliver,
            }
        )
        if self.raise_500:
            raise RuntimeError("brain server returned HTTP 500")
        return _StubMessage(
            body=self.reply_text,
            channel=channel,
            target=target,
            metadata={"trace_id": request_id, "session_id": session_id},
        )


class _MessageMappedGateway(_StubGateway):
    def __init__(self, replies: dict[str, str]) -> None:
        super().__init__(reply_text="")
        self._replies = dict(replies)

    async def run_once(
        self,
        *,
        channel: str,
        target: str,
        message: str,
        session_id: str,
        idempotency_key: str,
        request_id: str,
        inbound_metadata: dict[str, str],
        deliver: bool,
    ) -> _StubMessage:
        await super().run_once(
            channel=channel,
            target=target,
            message=message,
            session_id=session_id,
            idempotency_key=idempotency_key,
            request_id=request_id,
            inbound_metadata=inbound_metadata,
            deliver=deliver,
        )
        return _StubMessage(
            body=self._replies[message],
            channel=channel,
            target=target,
            metadata={"trace_id": request_id, "session_id": session_id},
        )


class _StubRuntimeManager:
    def __init__(self, gateway: _StubGateway) -> None:
        self.gateway = gateway

    def close(self) -> None:  # pragma: no cover - lifecycle only
        pass


class _StubRuntime:
    def __init__(self, gateway: _StubGateway) -> None:
        self.runtime_manager = _StubRuntimeManager(gateway)
        self.resolved_profiles: list[str] = []

    def resolve_gateway(self, profile_id: str) -> _StubGateway:
        self.resolved_profiles.append(profile_id)
        return self.runtime_manager.gateway

    def close(self) -> None:  # pragma: no cover - lifecycle only
        pass


class _CompatRuntime:
    def __init__(self, gateway: _StubGateway) -> None:
        self.runtime_manager = _CompatRuntimeManager(gateway)

    def close(self) -> None:  # pragma: no cover - lifecycle only
        pass


class _CompatRuntimeManager:
    def __init__(self, gateway: _StubGateway) -> None:
        self.gateway = gateway
        self.resolved_profiles: list[str] = []

    def resolve_gateway(self, profile_id: str) -> _StubGateway:
        self.resolved_profiles.append(profile_id)
        return self.gateway

    def close(self) -> None:  # pragma: no cover - lifecycle only
        pass


def _make_runtime_factory(gateway: _StubGateway):
    def _factory(_config_path: str | None) -> _StubRuntime:
        return _StubRuntime(gateway)

    return _factory


def _build_dispatcher(
    db_path: Path,
    gateway: _StubGateway,
) -> tuple[
    ControlPlaneDispatcher,
    SQLiteControlPlaneStore,
    list[dict[str, Any]],
    AuditLogger,
    OpenMinionBrainClient,
]:
    store = SQLiteControlPlaneStore(db_path)
    audit = AuditLogger(sink=store.put_audit)
    brain = OpenMinionBrainClient(
        config_path=None,
        runtime_factory=_make_runtime_factory(gateway),
    )
    outbound: list[dict[str, Any]] = []
    dispatcher = ControlPlaneDispatcher(
        store=store,
        router=Router(store),
        parser=SlashCommandParser(),
        command_registry=CommandRegistry(
            store=store,
            auth=AuthEvaluator(admin_user_keys=[]),
        ),
        brain_client=brain,
        audit_logger=audit,
        outbound_sender=outbound.append,
    )
    return dispatcher, store, outbound, audit, brain


def _telegram_inbound(text: str) -> InboundMessage:
    return InboundMessage(
        user_key="telegram:42",
        chat_key="telegram:100",
        text=text,
        channel="telegram",
        user_id="42",
        chat_id="100",
        metadata={"trace_id": "trace-cpe01"},
    )


def test_brain_client_targets_session_selected_profile() -> None:
    gateway = _StubGateway(reply_text="from selected profile")
    runtime = _StubRuntime(gateway)
    brain = OpenMinionBrainClient(
        config_path=None,
        target="configured-default",
        runtime_factory=lambda _config_path: runtime,
    )

    result = brain.run(
        session_id="sess-profile",
        agent_id="minimax-m2-5",
        user_text="hello",
        attachment_refs=[],
        trace_id="trace-profile",
    )

    assert runtime.resolved_profiles == ["minimax-m2-5"]
    assert gateway.calls[0]["target"] == "minimax-m2-5"
    assert gateway.calls[0]["idempotency_key"] == "trace-profile"
    assert gateway.calls[0]["inbound_metadata"]["caller_handles_delivery"] == "true"
    assert result["target"] == "minimax-m2-5"
    brain.close()


def test_brain_client_uses_configured_target_when_profile_missing() -> None:
    gateway = _StubGateway(reply_text="from fallback profile")
    runtime = _StubRuntime(gateway)
    brain = OpenMinionBrainClient(
        config_path=None,
        target="configured-default",
        runtime_factory=lambda _config_path: runtime,
    )

    result = brain.run(
        session_id="sess-profile",
        agent_id="",
        user_text="hello",
        attachment_refs=[],
        trace_id="trace-profile",
    )

    assert runtime.resolved_profiles == ["configured-default"]
    assert gateway.calls[0]["target"] == "configured-default"
    assert result["target"] == "configured-default"
    brain.close()


def test_brain_client_resolves_profile_gateway_through_runtime_manager() -> None:
    gateway = _StubGateway(reply_text="from compat wrapper")
    runtime = _CompatRuntime(gateway)
    brain = OpenMinionBrainClient(
        config_path=None,
        target="configured-default",
        runtime_factory=lambda _config_path: runtime,
    )

    result = brain.run(
        session_id="sess-profile",
        agent_id="minimax-m2-5",
        user_text="hello",
        attachment_refs=[],
        trace_id="trace-profile",
    )

    assert runtime.runtime_manager.resolved_profiles == ["minimax-m2-5"]
    assert result["text"] == "from compat wrapper"
    brain.close()


def test_brain_client_renders_internal_turn_contract_failure_for_users() -> None:
    gateway = _StubGateway(
        reply_text=(
            "General act work ended without the required typed "
            "finalization_status contract."
        )
    )
    runtime = _StubRuntime(gateway)
    brain = OpenMinionBrainClient(
        config_path=None,
        target="minimax-m2-5",
        runtime_factory=lambda _config_path: runtime,
    )

    result = brain.run(
        session_id="sess-finalization",
        agent_id="minimax-m2-5",
        user_text="research latest news",
        attachment_refs=[],
        trace_id="trace-finalization",
    )

    assert result["text"] == (
        "The model ended the turn without the required completion contract. "
        "Please try again."
    )
    brain.close()


def test_brain_client_e2e_success(tmp_path: Path) -> None:
    gateway = _StubGateway(reply_text="hello from stub brain")
    dispatcher, store, outbound, _audit, brain = _build_dispatcher(
        tmp_path / "cp.db", gateway
    )

    result_holder: dict[str, Any] = {}

    def _worker() -> None:
        result_holder["payload"] = dispatcher.handle_inbound(
            _telegram_inbound("hi brain")
        )

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=5.0)
    assert not t.is_alive(), "dispatcher worker thread did not finish in time"

    payload = result_holder["payload"]
    assert payload["type"] == "chat"
    assert payload["text"] == "hello from stub brain"
    assert len(outbound) == 1
    assert outbound[0]["text"] == "hello from stub brain"
    assert gateway.calls, "stub gateway was not called"

    chat_dispatch_rows = store.list_audit(event_type="cp.chat.dispatched")
    assert len(chat_dispatch_rows) >= 1
    assert store.list_audit(event_type="inbound.received")
    assert store.list_audit(event_type="outbound.sent")

    brain.close()
    store.close()


def test_brain_client_e2e_does_not_replay_previous_turn_for_local_delivery(
    tmp_path: Path,
) -> None:
    gateway = _MessageMappedGateway(
        {
            "what's weather at sf?": "weather answer",
            "check latest news on fifa": "fifa news answer",
            "hi": "fresh greeting",
        }
    )
    dispatcher, store, outbound, _audit, brain = _build_dispatcher(
        tmp_path / "cp.db", gateway
    )

    first = dispatcher.handle_inbound(_telegram_inbound("what's weather at sf?"))
    second = dispatcher.handle_inbound(_telegram_inbound("check latest news on fifa"))
    third = dispatcher.handle_inbound(_telegram_inbound("hi"))

    assert first["text"] == "weather answer"
    assert second["text"] == "fifa news answer"
    assert third["text"] == "fresh greeting"
    assert [call["message"] for call in gateway.calls] == [
        "what's weather at sf?",
        "check latest news on fifa",
        "hi",
    ]
    assert {
        call["inbound_metadata"]["caller_handles_delivery"] for call in gateway.calls
    } == {"true"}
    assert [item["text"] for item in outbound[-3:]] == [
        "weather answer",
        "fifa news answer",
        "fresh greeting",
    ]

    brain.close()
    store.close()


def test_brain_client_e2e_brain_500_surfaces_error_without_crash(
    tmp_path: Path,
) -> None:
    gateway = _StubGateway(reply_text="", raise_500=True)
    dispatcher, store, outbound, _audit, brain = _build_dispatcher(
        tmp_path / "cp.db", gateway
    )

    with pytest.raises(RuntimeError, match="brain server returned HTTP 500"):
        dispatcher.handle_inbound(_telegram_inbound("hi brain"))

    assert outbound == []
    received_rows = store.list_audit(event_type="inbound.received")
    assert len(received_rows) >= 1

    gateway.raise_500 = False
    gateway.reply_text = "recovered"
    payload = dispatcher.handle_inbound(_telegram_inbound("retry now"))
    assert payload["text"] == "recovered"
    assert outbound[-1]["text"] == "recovered"

    brain.close()
    store.close()
