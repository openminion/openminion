import sys
from asyncio import get_event_loop, AbstractEventLoop

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


class TerminalInteractionChannel(InteractionChannel):
    """Implementation of InteractionChannel for use in terminal CLI contexts."""

    def __init__(
        self,
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
        loop: AbstractEventLoop | None = None,
    ):
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.loop = loop or get_event_loop()
        self._cancel_requested = False
        self._cancel_tokens = {"cancel", "quit", "exit", "stop", "end"}

    def _write(self, text: str, *, flush: bool = True) -> None:
        self.stdout.write(text)
        if flush:
            self.stdout.flush()

    def _is_cancel_token(self, text: str) -> bool:
        """Check if input text is a cancellation signal."""
        return text.strip().lower() in self._cancel_tokens

    def is_cancel_requested(self) -> bool:
        """Check if user requested cancellation."""
        return self._cancel_requested

    async def cancel_wizard(self, message: str | None = None) -> bool:
        """Cancel the current wizard with optional message."""
        self._cancel_requested = True
        if message:
            self._write(f"{message}\n")
        return True

    def start_input_capture(self):
        """Begin monitoring terminal for cancellation signals or interruption."""
        import signal

        def cancel_handler(_signum, _frame):
            self._cancel_requested = True
            raise KeyboardInterrupt("Wizard cancelled")

        signal.signal(signal.SIGINT, cancel_handler)
        signal.signal(signal.SIGTERM, cancel_handler)

    async def prompt(
        self,
        message: str,
        default_value: str | None = None,
        hint: str | None = None,
    ) -> PromptResponse:
        """Prompt for text input in terminal."""
        if self._cancel_requested:
            return PromptResponse(value="", cancelled=True)

        full_prompt = message
        if default_value:
            full_prompt += f" (default: {default_value})"
        full_prompt += ": "

        if hint:
            self._write(f"[HINT] {hint}\n")

        self._write(full_prompt)

        line = self.stdin.readline()

        if line is None or line == "":
            if self._cancel_requested:
                return PromptResponse(value="", cancelled=True)
            if default_value is not None:
                return PromptResponse(value=default_value)
            return PromptResponse(value="", cancelled=True)

        line = line.rstrip("\n\r")

        if self._is_cancel_token(line):
            return PromptResponse(value=line, cancelled=True)

        if not line.strip():
            if default_value is not None:
                return PromptResponse(value=default_value)
            return PromptResponse(value=line)

        return PromptResponse(value=line)

    async def choose(
        self,
        message: str,
        options: list[str | Option],
        default_index: int | None = None,
        allow_multiple: bool = False,
    ) -> ChoiceResponse:
        """Prompt for selection from options in terminal."""
        if self._cancel_requested:
            return ChoiceResponse(value="", index=None, cancelled=True)

        self._write(f"{message}\n", flush=False)

        mapped_options = []
        for i, opt in enumerate(options):
            if isinstance(opt, str):
                option_obj = Option(value=opt, label=opt)
            else:
                option_obj = opt
            mapped_options.append(option_obj)
            disp = f"[{i + 1}] {option_obj.label}"
            if option_obj.description:
                disp += f" - {option_obj.description}"
            self._write(f"  {disp}\n", flush=False)

        if allow_multiple:
            idx_range = f"1-{len(options)}"
            default_text = f" (comma-separated numbers, e.g., '{idx_range}' or enter individual values)"
        else:
            idx_list = ", ".join(str(i + 1) for i in range(len(options)))
            default_text = f" (enter {idx_list}): "

        if default_index is not None and 0 <= default_index < len(options):
            default_text += f" (default: {default_index + 1})"

        self._write(f"Select option{default_text}")

        line = self.stdin.readline()
        if line is None:
            return ChoiceResponse(value="", index=None, cancelled=True)

        line = line.strip()

        if self._is_cancel_token(line):
            return ChoiceResponse(value=line, index=None, cancelled=True)

        if line == "" and default_index is not None:
            sel_opt = mapped_options[default_index]
            return ChoiceResponse(
                value=sel_opt.value, index=default_index, cancelled=False
            )

        try:
            num = int(line)
            actual_idx = num - 1

            if 0 <= actual_idx < len(mapped_options):
                selected_option = mapped_options[actual_idx]
                return ChoiceResponse(
                    value=selected_option.value, index=actual_idx, cancelled=False
                )
            return ChoiceResponse(
                value=line,
                index=None,
                cancelled=False,
                error=f"Invalid option number. Must be 1-{len(options)}.",
            )
        except ValueError:
            if allow_multiple and "," in line:
                indices = []
                for idx_str in line.split(","):
                    try:
                        idx_num = int(idx_str.strip())
                        actual_idx = idx_num - 1
                        if 0 <= actual_idx < len(mapped_options):
                            indices.append(actual_idx)
                        else:
                            return ChoiceResponse(
                                value=line,
                                index=None,
                                cancelled=False,
                                error=f"Invalid option number: {idx_num}",
                            )
                    except ValueError:
                        return ChoiceResponse(
                            value=line,
                            index=None,
                            cancelled=False,
                            error=f"Invalid number format: {idx_str.strip()}",
                        )

                if indices:
                    first_selected = indices[0]
                    val = mapped_options[first_selected].value
                    return ChoiceResponse(
                        value=val, index=first_selected, cancelled=False
                    )

            return ChoiceResponse(
                value=line,
                index=None,
                cancelled=False,
                error="Invalid selection format",
            )

    async def confirm(
        self, message: str, default: bool = True, danger: bool = False
    ) -> ConfirmResponse:
        """Prompt for yes/no confirmation in terminal."""
        if self._cancel_requested:
            return ConfirmResponse(confirmed=False, cancelled=True)

        suffix = " [Y/n]" if default else " [y/N]"
        prompt_text = f"{message}{suffix}: "

        if danger:
            self._write("! DANGER MODE ! ", flush=False)

        self._write(prompt_text)

        line = self.stdin.readline()
        if not line:
            return ConfirmResponse(confirmed=False, cancelled=True)

        line = line.strip().lower()

        if self._is_cancel_token(line):
            return ConfirmResponse(confirmed=False, cancelled=True)

        if line == "":
            return ConfirmResponse(confirmed=default, cancelled=False)

        if line in {"y", "yes", "true", "1"}:
            return ConfirmResponse(confirmed=True, cancelled=False)
        if line in {"n", "no", "false", "0"}:
            return ConfirmResponse(confirmed=False, cancelled=False)
        return ConfirmResponse(
            confirmed=False,
            cancelled=False,
            error=f"Invalid response: '{line}'. Must be yes/no.",
        )

    async def message(
        self, content: str, title: str | None = None, style: str | None = None
    ) -> MessageResponse:
        """Send non-interactive message to terminal."""
        try:
            if title:
                self._write(f"== {title} ==\n", flush=False)
            self._write(f"{content}\n", flush=False)
            if title:
                self._write("=" * (len(title) + 4) + "\n", flush=False)
            self.stdout.flush()
            return MessageResponse(delivered=True)
        except Exception as e:
            return MessageResponse(delivered=False, error=str(e))

    async def diff(
        self, original: str, modified: str, title: str | None = None
    ) -> MessageResponse:
        """Show differences in terminal (simple text comparison)."""
        try:
            if title:
                self._write(f"== {title} (DIFF) ==\n", flush=False)

            self._write("Changes made:\n", flush=False)
            orig_lines = original.split("\n")
            mod_lines = modified.split("\n")
            orig_set = set(orig_lines)
            mod_set = set(mod_lines)

            removed = orig_set - mod_set
            added = mod_set - orig_set

            for line in removed:
                if line.strip():
                    self._write(f"- {line}\n", flush=False)

            for line in added:
                if line.strip():
                    self._write(f"+ {line}\n", flush=False)

            if not added and not removed:
                self._write("(no changes)\n", flush=False)

            self.stdout.flush()
            return MessageResponse(delivered=True)
        except Exception as e:
            return MessageResponse(delivered=False, error=str(e))

    async def progress(
        self, description: str, percent: float, details: str | None = None
    ) -> MessageResponse:
        """Render progress bar in terminal."""
        try:
            filled_length = int(50 * percent)
            bar = "█" * filled_length + "░" * (50 - filled_length)
            percent_display = f"{percent * 100:.1f}%"

            progress_line = f"\r{description} |{bar}| {percent_display}"
            if details:
                progress_line += f" ({details})"

            self._write(progress_line)

            if percent >= 1.0:
                self._write("\n")

            return MessageResponse(delivered=True)
        except Exception as e:
            return MessageResponse(delivered=False, error=str(e))

    def get_interaction_mode(self) -> InteractionMode:
        """Get terminal interaction mode."""
        return resolve_interaction_mode("terminal")

    def supports_advanced_ui(self) -> bool:
        """Check if advanced UI features are supported."""
        return False

    async def start_wizard_context(self, wizard_session_id: str) -> bool:
        """Start context for wizard interaction session in terminal."""
        try:
            self._write(f"[WIZARD.START] Session: {wizard_session_id}\n")
            self.start_input_capture()
            return True
        except Exception:
            return False

    async def end_wizard_context(self, wizard_session_id: str) -> bool:
        """End context for wizard interaction session in terminal."""
        try:
            self._write(f"[WIZARD.END] Session: {wizard_session_id}\n")
            return True
        except Exception:
            return False
