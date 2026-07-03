from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest

from openminion.modules.controlplane.contracts.outbound import to_legacy_payload
from openminion.modules.controlplane.runtime.audit import AuditLogger
from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.runtime.auth import AuthEvaluator
from openminion.modules.controlplane.runtime.dispatcher import ControlPlaneDispatcher
from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore
from openminion.modules.controlplane.wizard.runtime import WizardResult


@dataclass
class _StubWizardSession:
    wizard_id: str
    updated_at: datetime
    step: int = 1
    total_steps: int = 2
    command_name: str = "test.wizard"


class _StubWizardStore:
    def __init__(self, session: _StubWizardSession) -> None:
        self._session = session

    async def get_active_sessions_for_chat(
        self, _chat_key: str
    ) -> list[_StubWizardSession]:
        return [self._session]

    async def get_active_sessions_for_user(
        self, _user_key: str
    ) -> list[_StubWizardSession]:
        return []


class _StubWizardExecutor:
    async def process_input(self, _wizard_id: str, _user_text: str) -> WizardResult:
        return WizardResult(
            success=True,
            completed=False,
            canceled=False,
            data={"action": "next_step", "next_prompt": "Next step"},
        )


class _RaisingWizardExecutor:
    async def process_input(self, _wizard_id: str, _user_text: str) -> WizardResult:
        raise ValueError("boom")


class _EchoBrain:
    contract_version = "v1"

    def run(self, **_: Any) -> dict[str, Any]:
        return {"text": "ok", "status": "completed"}


def _build_dispatcher() -> tuple[ControlPlaneDispatcher, AuditLogger]:
    store = InMemoryControlPlaneStore()
    audit = AuditLogger()
    dispatcher = ControlPlaneDispatcher(
        store=store,
        router=Router(store),
        parser=SlashCommandParser(),
        command_registry=CommandRegistry(
            store=store, auth=AuthEvaluator(admin_user_keys=[])
        ),
        brain_client=_EchoBrain(),
        audit_logger=audit,
    )
    return dispatcher, audit


def _wire_stubs(monkeypatch, *, executor: Any) -> None:
    wizard_session = _StubWizardSession(
        wizard_id="wiz-async-1",
        updated_at=datetime.now(timezone.utc),
    )
    wizard_store = _StubWizardStore(wizard_session)

    async def _get_wizard_store() -> _StubWizardStore:
        return wizard_store

    async def _get_wizard_executor() -> Any:
        return executor

    monkeypatch.setattr(
        "openminion.modules.controlplane.wizard.store.get_wizard_store",
        _get_wizard_store,
    )
    monkeypatch.setattr(
        "openminion.modules.controlplane.wizard.runtime.get_wizard_executor",
        _get_wizard_executor,
    )


def test_wizard_dispatch_standalone_no_running_loop(monkeypatch) -> None:
    dispatcher, _audit = _build_dispatcher()
    _wire_stubs(monkeypatch, executor=_StubWizardExecutor())

    with pytest.raises(RuntimeError):
        asyncio.get_running_loop()

    inbound = InboundMessage(
        user_key="telegram:user-1",
        chat_key="telegram:chat-1",
        text="continue wizard",
    )
    payload, ctx = dispatcher.dispatch(inbound)

    legacy = to_legacy_payload(payload)
    assert ctx.wizard_session_id == "wiz-async-1"
    assert legacy.get("type") == "wizard_result"
    assert legacy.get("ok") is True


def test_wizard_dispatch_inside_running_loop(monkeypatch) -> None:
    dispatcher, _audit = _build_dispatcher()
    _wire_stubs(monkeypatch, executor=_StubWizardExecutor())

    inbound = InboundMessage(
        user_key="telegram:user-2",
        chat_key="telegram:chat-2",
        text="continue wizard",
    )

    async def _drive() -> tuple[Any, Any]:
        asyncio.get_running_loop()
        return dispatcher.dispatch(inbound)

    loop = asyncio.new_event_loop()
    try:
        payload, ctx = loop.run_until_complete(_drive())
    finally:
        loop.close()

    legacy = to_legacy_payload(payload)
    assert ctx.wizard_session_id == "wiz-async-1"
    assert legacy.get("type") == "wizard_result"
    assert legacy.get("ok") is True


def test_wizard_step_failure_emits_audit_and_propagates(monkeypatch) -> None:
    dispatcher, audit = _build_dispatcher()
    _wire_stubs(monkeypatch, executor=_RaisingWizardExecutor())

    inbound = InboundMessage(
        user_key="telegram:user-3",
        chat_key="telegram:chat-3",
        text="continue wizard",
    )

    with pytest.raises(ValueError, match="boom"):
        dispatcher.dispatch(inbound)

    failed = audit.list_events(event_type="cp.wizard.step.failed")
    assert len(failed) == 1
    details = failed[0].details
    assert details.get("exc_type") == "ValueError"
    assert details.get("message") == "boom"
    assert isinstance(details.get("session_id"), str)
    assert details.get("wizard_id") == "wiz-async-1"


def test_wizard_step_failure_increments_health_and_logs_warning(
    monkeypatch, caplog
) -> None:
    dispatcher, audit = _build_dispatcher()
    _wire_stubs(monkeypatch, executor=_RaisingWizardExecutor())

    inbound = InboundMessage(
        user_key="telegram:user-4",
        chat_key="telegram:chat-4",
        text="continue wizard",
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(ValueError, match="boom"):
            dispatcher.dispatch(inbound)

    status = audit.health_status()
    assert status["wizard_step_failures"] == 1
    warnings = [r for r in caplog.records if r.message == "cp.wizard.step.failure"]
    assert len(warnings) == 1
    record = warnings[0]
    assert getattr(record, "exc_type", None) == "ValueError"
    assert isinstance(getattr(record, "session_id", None), str)
    assert getattr(record, "wizard_id", None) == "wiz-async-1"
