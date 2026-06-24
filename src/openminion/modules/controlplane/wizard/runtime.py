from enum import Enum
from typing import Any, Dict, Optional
from dataclasses import dataclass
from abc import ABC, abstractmethod

from .store import WizardSession, WizardState, get_wizard_store

# Special constants for wizard control flow
WIZARD_HELP_TOKEN = "?"
WIZARD_CANCEL_TOKENS = {"cancel", "quit", "exit", "stop", "end"}


class WizardAction(Enum):
    """Actions that can be taken during wizard execution."""

    NEXT_STEP = "next_step"
    SHOW_HELP = "show_help"
    CONFIRM_ACTION = "confirm_action"
    PREVIEW_CHANGES = "preview_changes"
    CANCEL_WIZARD = "cancel_wizard"
    COMPLETE_WIZARD = "complete_wizard"
    ERROR_OCCURRED = "error_occurred"


WIZARD_ACTION_TOKENS = {
    "preview": WizardAction.PREVIEW_CHANGES,
    "confirm": WizardAction.CONFIRM_ACTION,
}


@dataclass
class WizardStepConfig:
    """Configuration for a wizard step."""

    step_number: int
    total_steps: int
    prompt: str
    default_value: Optional[str] = None
    allow_cancel: bool = True
    require_input: bool = True
    validator: Optional[callable] = None
    processor: Optional[callable] = None


@dataclass
class WizardResult:
    """Outcome of wizard execution."""

    success: bool
    completed: bool
    canceled: bool
    data: Dict[str, Any]
    error: Optional[str] = None


class BaseWizardStepHandler(ABC):
    """Abstract base for step-specific handler logic."""

    @abstractmethod
    async def process_response(
        self, session: WizardSession, user_input: str
    ) -> WizardAction: ...

    @abstractmethod
    async def get_prompt_for_step(self, session: WizardSession) -> str: ...

    @abstractmethod
    async def get_help_text(self, session: WizardSession) -> str: ...


class DefaultWizardStepHandler(BaseWizardStepHandler):
    """Default handler for generic wizard steps."""

    async def process_response(
        self, session: WizardSession, user_input: str
    ) -> WizardAction:
        """Process user input generically."""
        user_input_lower = user_input.strip().lower()
        if user_input_lower == WIZARD_HELP_TOKEN:
            return WizardAction.SHOW_HELP
        if user_input_lower in WIZARD_CANCEL_TOKENS:
            return WizardAction.CANCEL_WIZARD
        if (action := WIZARD_ACTION_TOKENS.get(user_input_lower)) is not None:
            return action
        step_key = f"step_{session.step}_input"
        session.draft_result[step_key] = user_input

        if session.step >= session.total_steps:
            return WizardAction.COMPLETE_WIZARD

        return WizardAction.NEXT_STEP

    async def get_prompt_for_step(self, session: WizardSession) -> str:
        """Get generic prompt for current step."""
        step_prompts = {
            1: f"What is the first piece of information for {session.command_name}?",
            2: f"What comes next for {session.command_name}?",
            3: f"Finally, what else do you need for {session.command_name}?",
        }

        return step_prompts.get(
            session.step,
            f"Enter information for step {session.step} of {session.total_steps}:",
        )

    async def get_help_text(self, session: WizardSession) -> str:
        """Get generic help text."""
        return f"""Step {session.step}/{session.total_steps} help:
- Enter your response normally and press Enter
- Type '{WIZARD_HELP_TOKEN}' for help with this step
- Type 'cancel' to cancel the operation
- Type 'preview' to see changes so far before confirming
- Type 'confirm' if you're ready to complete
"""


class WizardExecutor:
    """Orchestrates wizard execution using stored state and interaction."""

    def __init__(self, wizard_store=None):
        if wizard_store is None:
            raise TypeError("WizardExecutor requires an async-initialized wizard_store")
        self.store = wizard_store
        self.step_handlers: Dict[str, BaseWizardStepHandler] = {}

    def register_step_handler(self, command_name: str, handler: BaseWizardStepHandler):
        """Register a custom step handler for a specific command."""
        self.step_handlers[command_name] = handler

    async def start_wizard(
        self,
        command_name: str,
        total_steps: int,
        user_key: str,
        chat_key: str,
        initial_state: Dict[str, Any] = None,
    ) -> WizardSession:
        """Start a new wizard session."""
        session = await self.store.create_session(
            command_name=command_name,
            step=1,
            total_steps=total_steps,
            user_key=user_key,
            chat_key=chat_key,
            session_id=chat_key,  # Using chat_key as session_id for correlation
        )

        if initial_state:
            session.session_data.update(initial_state)

        await self.store.save_session(session)
        return session

    async def process_input(self, wizard_id: str, user_input: str) -> WizardResult:
        """Process user input for an ongoing wizard."""
        session = await self.store.get_session(wizard_id)
        if not session or session.state != WizardState.ACTIVE:
            return WizardResult(
                success=False,
                completed=False,
                canceled=False,
                data={},
                error="Wizard session not found or inactive",
            )
        handler = self.step_handlers.get(
            session.command_name, DefaultWizardStepHandler()
        )
        try:
            action = await handler.process_response(session, user_input)
            return await self._result_for_action(
                wizard_id=wizard_id,
                session=session,
                handler=handler,
                action=action,
            )
        except Exception as e:
            await self.store.update_session_state(wizard_id, WizardState.ERROR)
            return WizardResult(
                success=False,
                completed=False,
                canceled=False,
                data={},
                error=f"Wizard execution error: {str(e)}",
            )

    async def _result_for_action(
        self,
        *,
        wizard_id: str,
        session: WizardSession,
        handler: BaseWizardStepHandler,
        action: WizardAction,
    ) -> WizardResult:
        if action == WizardAction.SHOW_HELP:
            help_text = await handler.get_help_text(session)
            session.session_data["current_help"] = help_text
            await self.store.save_session(session)
            return WizardResult(
                success=True,
                completed=False,
                canceled=False,
                data={
                    "action": "show_help",
                    "help_text": help_text,
                    "stay_on_step": True,
                },
            )
        if action == WizardAction.CANCEL_WIZARD:
            await self.store.update_session_state(wizard_id, WizardState.CANCELLED)
            return WizardResult(
                success=True,
                completed=False,
                canceled=True,
                data={"action": "cancelled"},
                error="Wizard was cancelled",
            )
        if action == WizardAction.PREVIEW_CHANGES:
            return self._preview_result(session)
        if action == WizardAction.CONFIRM_ACTION:
            return await self._confirm_result(wizard_id, session)
        if action == WizardAction.NEXT_STEP:
            return await self._next_step_result(wizard_id, session, handler)
        if action == WizardAction.COMPLETE_WIZARD:
            await self.store.update_session_state(wizard_id, WizardState.COMPLETED)
            return WizardResult(
                success=True,
                completed=True,
                canceled=False,
                data=dict(session.draft_result),
            )
        return WizardResult(
            success=False,
            completed=False,
            canceled=False,
            data={},
            error=f"Unknown wizard action: {action}",
        )

    @staticmethod
    def _preview_result(session: WizardSession) -> WizardResult:
        preview_data = {
            k: v for k, v in session.draft_result.items() if k.startswith("step_")
        }
        return WizardResult(
            success=True,
            completed=False,
            canceled=False,
            data={
                "action": "preview",
                "changes": preview_data,
                "completed_steps": len(preview_data),
                "total_required": session.total_steps,
            },
        )

    async def _confirm_result(
        self, wizard_id: str, session: WizardSession
    ) -> WizardResult:
        completed_steps = len(
            [k for k in session.draft_result if k.startswith("step_")]
        )
        if completed_steps < session.total_steps:
            return WizardResult(
                success=True,
                completed=False,
                canceled=False,
                data={
                    "action": "incomplete",
                    "required_more_steps": True,
                    "missing_steps": session.total_steps - completed_steps,
                },
            )
        await self.store.update_session_state(wizard_id, WizardState.COMPLETED)
        return WizardResult(
            success=True,
            completed=True,
            canceled=False,
            data=dict(session.draft_result),
        )

    async def _next_step_result(
        self,
        wizard_id: str,
        session: WizardSession,
        handler: BaseWizardStepHandler,
    ) -> WizardResult:
        new_step = session.step + 1
        updated_session = await self.store.update_session_state(
            wizard_id, WizardState.ACTIVE, step=new_step
        )
        if updated_session.step > updated_session.total_steps:
            await self.store.update_session_state(wizard_id, WizardState.COMPLETED)
            return WizardResult(
                success=True,
                completed=True,
                canceled=False,
                data=dict(updated_session.draft_result),
            )
        next_prompt = await handler.get_prompt_for_step(updated_session)
        return WizardResult(
            success=True,
            completed=False,
            canceled=False,
            data={
                "action": "next_step",
                "current_step": new_step,
                "total_steps": session.total_steps,
                "next_prompt": next_prompt,
                "progress": new_step / session.total_steps,
            },
        )

    async def get_current_prompt(self, wizard_id: str) -> Optional[str]:
        """Get the current step prompt for a wizard."""
        session = await self.store.get_session(wizard_id)
        if not session or session.state != WizardState.ACTIVE:
            return None

        handler = self.step_handlers.get(
            session.command_name, DefaultWizardStepHandler()
        )
        return await handler.get_prompt_for_step(session)

    async def cancel_wizard(self, wizard_id: str, reason: str = None) -> bool:
        """Explicitly cancel an active wizard."""
        session = await self.store.get_session(wizard_id)
        if not session or session.state != WizardState.ACTIVE:
            return False

        await self.store.update_session_state(wizard_id, WizardState.CANCELLED)
        return True

    async def timeout_wizard(self, wizard_id: str) -> bool:
        """Mark a wizard as timed out."""
        session = await self.store.get_session(wizard_id)
        if not session or session.state != WizardState.ACTIVE:
            return False

        await self.store.update_session_state(wizard_id, WizardState.TIMEOUT)
        return True


_wizard_executor: Optional[WizardExecutor] = None


async def get_wizard_executor() -> WizardExecutor:
    """Get the global wizard executor."""
    global _wizard_executor
    if not _wizard_executor:
        wizard_store = await get_wizard_store()
        _wizard_executor = WizardExecutor(wizard_store)
    return _wizard_executor
