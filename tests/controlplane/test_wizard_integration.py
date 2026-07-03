import asyncio
from unittest.mock import MagicMock
import pytest

from openminion.modules.controlplane.contracts.models import ResolvedContext
from openminion.modules.controlplane.wizard.store import InMemoryWizardStore
from openminion.modules.controlplane.wizard.runtime import WizardExecutor, WizardResult
from openminion.modules.controlplane.wizard.terminal import (
    TerminalInteractionChannel,
)
from openminion.modules.controlplane.wizard.telegram import (
    TelegramInteractionChannel,
)
@pytest.mark.asyncio
async def test_wizard_session_lifecycle():
    store = InMemoryWizardStore()
    executor = WizardExecutor(store)

    session = await executor.start_wizard(
        command_name="test.lifecycle",
        total_steps=2,
        user_key="test_user",
        chat_key="test_chat",
    )

    assert session.wizard_id
    assert session.command_name == "test.lifecycle"
    assert session.state.name == "ACTIVE"
    assert session.step == 1
    assert session.total_steps == 2

    result1 = await executor.process_input(session.wizard_id, "step1 answer")
    assert not result1.canceled
    assert not result1.completed

    updated_session = await store.get_session(session.wizard_id)
    assert "step_1_input" in str(updated_session.draft_result)

    result2 = await executor.process_input(session.wizard_id, "step2 answer")
    assert not result2.canceled
    assert result2.completed

    final_session = await store.get_session(session.wizard_id)
    assert final_session.state.name in ["COMPLETED", "CANCELLED", "TIMEOUT"]


@pytest.mark.asyncio
async def test_wizard_cancellation():
    store = InMemoryWizardStore()
    executor = WizardExecutor(store)

    session = await executor.start_wizard(
        command_name="test.cancel",
        total_steps=3,
        user_key="cancel_user",
        chat_key="cancel_chat",
    )

    cancel_result = await executor.cancel_wizard(session.wizard_id)
    assert cancel_result is True

    cancelled_session = await store.get_session(session.wizard_id)
    assert cancelled_session.state.name == "CANCELLED"


@pytest.mark.asyncio
async def test_wizard_timeout_handling():
    store = InMemoryWizardStore()

    from datetime import timedelta

    timed_out_session = await store.create_session(
        command_name="test.timeout",
        step=1,
        total_steps=2,
        user_key="timeout_user",
        chat_key="timeout_chat",
        timeout_duration=timedelta(milliseconds=1),
    )

    await asyncio.sleep(0.1)

    retrieved = await store.get_session(timed_out_session.wizard_id)
    assert retrieved is None


@pytest.mark.asyncio
async def test_terminal_adapter_implementation():
    adapter = TerminalInteractionChannel()

    assert hasattr(adapter, "prompt")
    assert hasattr(adapter, "choose")
    assert hasattr(adapter, "confirm")
    assert hasattr(adapter, "message")
    assert hasattr(adapter, "diff")
    assert hasattr(adapter, "get_interaction_mode")
    assert hasattr(adapter, "is_cancel_requested")
    assert hasattr(adapter, "cancel_wizard")

    import inspect

    for method_name in ["prompt", "choose", "confirm"]:
        method = getattr(adapter, method_name)
        assert inspect.iscoroutinefunction(method)


@pytest.mark.asyncio
async def test_telegram_adapter_implementation():
    adapter = TelegramInteractionChannel()

    assert hasattr(adapter, "prompt")
    assert hasattr(adapter, "choose")
    assert hasattr(adapter, "confirm")
    assert hasattr(adapter, "message")
    assert hasattr(adapter, "diff")
    assert hasattr(adapter, "get_interaction_mode")
    assert hasattr(adapter, "is_cancel_requested")
    assert hasattr(adapter, "cancel_wizard")


@pytest.mark.asyncio
async def test_cross_platform_semantic_equivalence():
    from openminion.modules.controlplane.runtime.interaction import InteractionMode

    term_adapter = TerminalInteractionChannel()
    mock_bot = MagicMock()
    tg_adapter = TelegramInteractionChannel(telegram_bot=mock_bot, chat_id="test_chat")

    term_mode = term_adapter.get_interaction_mode()
    tg_mode = tg_adapter.get_interaction_mode()

    assert isinstance(term_mode, InteractionMode)
    assert isinstance(tg_mode, InteractionMode)

    term_advanced_ui = term_adapter.supports_advanced_ui()
    tg_advanced_ui = tg_adapter.supports_advanced_ui()

    assert isinstance(term_advanced_ui, bool)
    assert isinstance(tg_advanced_ui, bool)


def test_interaction_mode_resolution_uses_dynamic_channel_mapping() -> None:
    from openminion.modules.controlplane.runtime.interaction import (
        InteractionMode,
        resolve_interaction_mode,
    )

    assert resolve_interaction_mode("terminal") == InteractionMode.TERMINAL
    assert resolve_interaction_mode("telegram") == InteractionMode.TELEGRAM
    assert resolve_interaction_mode("slack") == InteractionMode.CHAT
    assert resolve_interaction_mode("unknown-channel") == InteractionMode.UNKNOWN


@pytest.mark.asyncio
async def test_negative_paths_handling():
    store = InMemoryWizardStore()
    executor = WizardExecutor(store)

    session = await executor.start_wizard(
        command_name="test.negative",
        total_steps=2,
        user_key="neg_user",
        chat_key="neg_chat",
    )

    result_empty = await executor.process_input(session.wizard_id, "")
    assert isinstance(result_empty, WizardResult)

    long_input = "x" * 10000
    result_long = await executor.process_input(session.wizard_id, long_input)
    assert isinstance(result_long, WizardResult)
    nonexistent = await store.get_session("does_not_exist")
    assert nonexistent is None


@pytest.mark.asyncio
async def test_context_extension_with_ui_and_wizard_session():
    ctx = ResolvedContext(
        session_id="test_sess",
        user_key="test_user",
        chat_key="test_chat",
        agent_id="test_agent",
        role="test_role",
        trace_id="test_trace",
        span_id="test_span",
        ui=None,
        wizard_session_id="test_wiz_id",
    )

    assert hasattr(ctx, "ui")
    assert hasattr(ctx, "wizard_session_id")
    assert ctx.wizard_session_id == "test_wiz_id"
    assert ctx.ui is None


@pytest.mark.asyncio
async def test_wizard_store_operations_comprehensive():
    store = InMemoryWizardStore()

    session1 = await store.create_session("cmd1", 1, 3, "user1", "chat1")
    await store.create_session("cmd2", 1, 2, "user1", "chat2")

    user_active = await store.get_active_sessions_for_user("user1")
    assert len(user_active) == 2

    chat_active = await store.get_active_sessions_for_chat("chat1")
    assert len(chat_active) == 1
    assert chat_active[0].wizard_id == session1.wizard_id

    updated = await store.update_session_state(session1.wizard_id, "COMPLETED")
    if updated:
        await store.get_active_sessions_for_chat("chat1")

    deleted = await store.delete_session(session1.wizard_id)
    assert deleted is True

    not_found = await store.get_session(session1.wizard_id)
    assert not_found is None
