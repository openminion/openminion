from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.runtime.dispatcher import ControlPlaneDispatcher
from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.runtime import EchoBrain
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
        self.active = True

    async def get_active_sessions_for_chat(
        self, _chat_key: str
    ) -> list[_StubWizardSession]:
        return [self._session] if self.active else []

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


def _build_dispatcher() -> ControlPlaneDispatcher:
    store = InMemoryControlPlaneStore()
    return ControlPlaneDispatcher(
        store=store,
        router=Router(store),
        parser=SlashCommandParser(),
        command_registry=CommandRegistry(store=store, auth=None),
        brain_client=EchoBrain(),
    )


def test_wizard_and_non_wizard_paths_share_router_session_resolution(
    monkeypatch,
) -> None:
    dispatcher = _build_dispatcher()
    inbound = InboundMessage(
        user_key="telegram:user-1",
        chat_key="telegram:chat-1",
        text="continue wizard",
    )
    expected_ctx = dispatcher.router.resolve(inbound)
    assert expected_ctx.session_id != inbound.chat_key

    wizard_session = _StubWizardSession(
        wizard_id="wiz-123",
        updated_at=datetime.now(timezone.utc),
    )
    wizard_store = _StubWizardStore(wizard_session)
    wizard_executor = _StubWizardExecutor()

    async def _get_wizard_store() -> _StubWizardStore:
        return wizard_store

    async def _get_wizard_executor() -> _StubWizardExecutor:
        return wizard_executor

    monkeypatch.setattr(
        "openminion.modules.controlplane.wizard.store.get_wizard_store",
        _get_wizard_store,
    )
    monkeypatch.setattr(
        "openminion.modules.controlplane.wizard.runtime.get_wizard_executor",
        _get_wizard_executor,
    )

    _, wizard_ctx = dispatcher.dispatch(inbound)
    assert wizard_ctx.session_id == expected_ctx.session_id
    assert wizard_ctx.wizard_session_id == "wiz-123"

    wizard_store.active = False
    _, normal_ctx = dispatcher.dispatch(inbound)
    assert normal_ctx.session_id == expected_ctx.session_id
    assert normal_ctx.wizard_session_id is None
