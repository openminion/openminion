from __future__ import annotations

import asyncio
import shlex

from openminion.cli.interactive.widgets import FocusTranscript
from openminion.cli.presentation.models import ChatMessage, MessageKind, ToolEvent


class FocusShellMixin:
    async def _run_shell_escape(self, command: str) -> None:
        if self._busy:
            return
        self._set_busy(True)
        chat = self.query_one(FocusTranscript)
        chat.push_message(
            ChatMessage(
                kind=MessageKind.USER,
                sender="you",
                body=f"!{command}",
            )
        )
        try:
            try:
                argv = shlex.split(command)
            except ValueError as exc:
                chat.push_message(
                    ChatMessage(
                        kind=MessageKind.ERROR,
                        sender="error",
                        body=f"Could not parse `!{command}`: {exc}",
                    )
                )
                return
            if not argv:
                return
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=self._working_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                chat.push_message(
                    ChatMessage(
                        kind=MessageKind.ERROR,
                        sender="error",
                        body=f"Command not found: {exc.filename or argv[0]}",
                    )
                )
                return
            except Exception as exc:
                chat.push_message(
                    ChatMessage(
                        kind=MessageKind.ERROR,
                        sender="error",
                        body=f"Could not run `!{command}`: {exc}",
                    )
                )
                return
            stdout_b, stderr_b = await proc.communicate()
            stdout = (stdout_b or b"").decode("utf-8", errors="replace")
            stderr = (stderr_b or b"").decode("utf-8", errors="replace")
            combined = stdout
            if stderr:
                combined = (combined + ("\n" if combined else "") + stderr).rstrip()
            event = ToolEvent(
                tool_name="bash",
                args={"cmd": command},
                content=combined or "(no output)",
                full_content=combined,
                duration_ms=0,
                exit_code=int(proc.returncode or 0),
            )
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.TOOL,
                    sender="bash",
                    body="",
                    tool_event=event,
                    tool_result=combined,
                )
            )
        finally:
            self._set_busy(False)


__all__ = ["FocusShellMixin"]
