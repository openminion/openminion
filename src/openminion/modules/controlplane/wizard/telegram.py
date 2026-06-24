import asyncio

from ..runtime.interaction import (
    ChoiceResponse,
    ConfirmResponse,
    InteractionChannel,
    InteractionMode,
    MessageResponse,
    Option,
    PromptResponse,
    resolve_interaction_mode,
)


class TelegramInteractionChannel(InteractionChannel):
    """Interaction channel for Telegram-backed wizard flows."""

    def __init__(
        self,
        telegram_bot=None,
        chat_id: str | None = None,
        message_queue=None,
        storage_backend=None,
    ):
        self.bot = telegram_bot
        self.chat_id = chat_id
        self.message_queue = message_queue  # Usually an asyncio.Queue or similar
        self.storage = storage_backend  # For storing intermediate wizard state
        self._cancel_requested = False
        self._cancel_command_types = {
            "cancel",
            "quit",
            "exit",
            "/cancel",
            "/quit",
            "/exit",
        }
        self.timeout_seconds = 300  # 5 minute default timeout

    def is_cancel_requested(self) -> bool:
        """Check if user sent a cancel command."""
        return self._cancel_requested

    async def _send_text(self, text: str) -> None:
        await self.bot.send_message(chat_id=self.chat_id, text=text)

    async def cancel_wizard(self, message: str | None = None) -> bool:
        """Send a cancellation message on Telegram."""
        try:
            if self.bot and self.chat_id:
                await self._send_text(message or "Operation cancelled.")
            self._cancel_requested = True
            return True
        except Exception:
            return False

    async def prompt(
        self,
        message: str,
        default_value: str | None = None,
        hint: str | None = None,
    ) -> PromptResponse:
        """Send a prompt and await the next reply for this chat."""
        try:
            full_message = message
            if hint:
                full_message = f"{full_message}\n\n💡 {hint}"
            if default_value:
                full_message = f"{full_message}\n(default: {default_value})"

            await self._send_text(full_message)

            user_response = await self._wait_for_user_input()
            if user_response is None:
                return PromptResponse(
                    value="", cancelled=True, error="Timeout waiting for input"
                )

            user_text_lower = user_response.strip().lower()
            if user_text_lower in self._cancel_command_types:
                return PromptResponse(value=user_response, cancelled=True)

            if not user_response.strip() and default_value is not None:
                return PromptResponse(value=default_value)

            return PromptResponse(value=user_response)
        except Exception as e:
            return PromptResponse(
                value="", cancelled=False, error=f"Error processing prompt: {str(e)}"
            )

    async def _wait_for_user_input(self) -> str | None:
        """Wait for a message from the user in the correct chat."""
        try:
            start_time = asyncio.get_event_loop().time()

            while True:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > self.timeout_seconds:
                    return None

                if self.message_queue:
                    try:
                        message = self.message_queue.get_nowait()

                        if (
                            hasattr(message, "chat")
                            and str(message.chat.id) == self.chat_id
                        ):
                            if hasattr(message, "text"):
                                input_text = message.text.strip()
                                if input_text.lower() in self._cancel_command_types:
                                    self._cancel_requested = True
                                return input_text
                    except asyncio.QueueEmpty:
                        await asyncio.sleep(0.1)
                        continue
                else:
                    await asyncio.sleep(0.5)
                    return None

        except Exception:
            return None

    async def choose(
        self,
        message: str,
        options: list[str | Option],
        default_index: int | None = None,
        allow_multiple: bool = False,
    ) -> ChoiceResponse:
        """Send options to Telegram and wait for user selection."""
        try:
            option_texts = []
            for i, opt in enumerate(options):
                if isinstance(opt, str):
                    label = opt
                else:
                    label = opt.label
                option_texts.append(f"{i + 1}. {label}")

            full_message = f"{message}\n\n"
            full_message += "\n".join(option_texts)
            if default_index is not None:
                full_message += f"\n(default: {default_index + 1})"

            await self._send_text(full_message)

            user_input = await self._wait_for_user_input()
            if user_input is None:
                return ChoiceResponse(value="", cancelled=True, error="Timeout")

            if user_input.lower() in self._cancel_command_types:
                return ChoiceResponse(value=user_input, cancelled=True)

            try:
                selected_index = int(user_input) - 1

                if 0 <= selected_index < len(options):
                    selected_item = options[selected_index]
                    selected_value = (
                        selected_item.value
                        if hasattr(selected_item, "value")
                        else selected_item
                    )
                    return ChoiceResponse(
                        value=selected_value, index=selected_index, cancelled=False
                    )
                return ChoiceResponse(
                    value=user_input,
                    index=None,
                    cancelled=False,
                    error=f"Invalid choice. Please select a number between 1 and {len(options)}.",
                )
            except ValueError:
                return ChoiceResponse(
                    value=user_input,
                    index=None,
                    cancelled=False,
                    error=f"Please enter a number from 1 to {len(options)}.",
                )
        except Exception as e:
            return ChoiceResponse(value="", index=None, cancelled=False, error=str(e))

    async def confirm(
        self, message: str, default: bool = True, danger: bool = False
    ) -> ConfirmResponse:
        """Send confirmation prompt to Telegram with Yes/No options."""
        try:
            full_message = f"{message}\n\n"
            if danger:
                full_message += "⚠️ Danger operation! "
            full_message += f"[Y] Yes / [N] No (default: {'Yes' if default else 'No'})"

            await self.bot.send_message(chat_id=self.chat_id, text=full_message)

            user_input = await self._wait_for_user_input()
            if user_input is None:
                return ConfirmResponse(confirmed=False, cancelled=True, error="Timeout")

            if user_input.lower() in self._cancel_command_types:
                return ConfirmResponse(confirmed=False, cancelled=True)

            user_input_lower = user_input.strip().lower()
            if user_input_lower in {"y", "yes", "1", "ok", "sure"}:
                return ConfirmResponse(confirmed=True, cancelled=False)
            if user_input_lower in {"n", "no", "0", "nope"}:
                return ConfirmResponse(confirmed=False, cancelled=False)
            if user_input_lower == "":
                return ConfirmResponse(confirmed=default, cancelled=False)
            return ConfirmResponse(
                confirmed=False,
                cancelled=False,
                error="Please respond with Y(es) or N(o).",
            )
        except Exception as e:
            return ConfirmResponse(confirmed=False, cancelled=False, error=str(e))

    async def message(
        self, content: str, title: str | None = None, style: str | None = None
    ) -> MessageResponse:
        """Send a non-interactive message to Telegram."""
        try:
            formatted_content = content
            if title:
                formatted_content = f"*{title}*\n{content}"
                # Convert * to bold markdown format

            await self._send_text(formatted_content)
            return MessageResponse(delivered=True)
        except Exception as e:
            return MessageResponse(delivered=False, error=str(e))

    async def diff(
        self, original: str, modified: str, title: str | None = None
    ) -> MessageResponse:
        """Show differences in Telegram format (simplified)."""
        try:
            original_lines = original.split("\n")
            modified_lines = modified.split("\n")

            deleted = [
                line
                for line in original_lines
                if line not in modified_lines and line.strip()
            ]
            added = [
                line
                for line in modified_lines
                if line not in original_lines and line.strip()
            ]

            diff_content = ""
            if title:
                diff_content += f"*Diff: {title}*\n"

            if not deleted and not added:
                diff_content += "No changes detected."
            else:
                if deleted:
                    diff_content += f"Deleted ({len(deleted)}):\n"
                    diff_content += "- " + "\n- ".join(deleted[:5])
                    if len(deleted) > 5:
                        diff_content += f"\n... and {len(deleted) - 5} more changes"

                if added:
                    diff_content += f"\n\nAdded ({len(added)}):\n"
                    diff_content += "+ " + "\n+ ".join(added[:5])
                    if len(added) > 5:
                        diff_content += f"\n... and {len(added) - 5} more additions"

            await self._send_text(diff_content)
            return MessageResponse(delivered=True)
        except Exception as e:
            return MessageResponse(delivered=False, error=str(e))

    async def progress(
        self, description: str, percent: float, details: str | None = None
    ) -> MessageResponse:
        """Update progress indicator as a message in Telegram."""
        try:
            bar_length = 20
            filled_blocks = int(bar_length * percent)
            bar = "█" * filled_blocks + "░" * (bar_length - filled_blocks)
            percent_text = f"{percent * 100:.1f}%"

            progress_msg = f"{description}\n{bar} {percent_text}"
            if details:
                progress_msg += f"\n{details}"

            await self._send_text(progress_msg)
            return MessageResponse(delivered=True)
        except Exception as e:
            return MessageResponse(delivered=False, error=str(e))

    def get_interaction_mode(self) -> InteractionMode:
        """Get Telegram interaction mode."""
        return resolve_interaction_mode("telegram")

    def supports_advanced_ui(self) -> bool:
        """Check if advanced UI features are supported."""
        return True  # Telegram supports rich formatting

    # Context management for wizards
    async def start_wizard_context(self, wizard_session_id: str) -> bool:
        """Record the active wizard session for this Telegram chat."""
        try:
            if self.storage:
                await self.storage.set(
                    f"wizard:{self.chat_id}",
                    {
                        "session_id": wizard_session_id,
                        "start_time": asyncio.get_event_loop().time(),
                    },
                )
            else:
                self._current_wizard = wizard_session_id

            return True
        except Exception:
            return False

    async def end_wizard_context(self, wizard_session_id: str) -> bool:
        """Clear the active wizard session for this Telegram chat."""
        try:
            if self.storage:
                await self.storage.delete(f"wizard:{self.chat_id}")
            else:
                self._current_wizard = None

            await self._send_text("Wizard session completed!")

            return True
        except Exception:
            return False
