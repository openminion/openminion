from datetime import datetime
from dataclasses import dataclass
import traceback
from collections.abc import Callable

from ..contracts.models import CommandResult, ParsedCommand, ResolvedContext


@dataclass
class BrokenModuleInfo:
    name: str
    error_message: str
    error_type: str
    timestamp: datetime
    traceback: str | None = None
    failed_commands: list[str] | None = None


class BrokenModuleTracker:
    def __init__(self) -> None:
        self.broken_modules: dict[str, BrokenModuleInfo] = {}

    def register_broken_module(
        self,
        module_name: str,
        error: Exception,
        failed_commands: list[str] | None = None,
    ) -> None:
        self.broken_modules[module_name] = BrokenModuleInfo(
            name=module_name,
            error_message=str(error),
            error_type=type(error).__name__,
            timestamp=datetime.now(),
            traceback=traceback.format_exc() if failed_commands is None else None,
            failed_commands=failed_commands,
        )

    def is_broken_module(self, module_name: str) -> bool:
        return module_name in self.broken_modules

    def get_broken_modules(self) -> dict[str, BrokenModuleInfo]:
        return self.broken_modules.copy()

    def get_broken_module(self, module_name: str) -> BrokenModuleInfo | None:
        return self.broken_modules.get(module_name)


def make_broken_command_handler(
    module_name: str,
    command_name: str,
    error_desc: str,
) -> Callable[[ParsedCommand, ResolvedContext], CommandResult]:
    """Create a handler that reports safe error for broken commands."""

    def broken_command_handler(
        command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        return CommandResult(
            ok=False,
            text=f"Command '{command_name}' from module '{module_name}' is unavailable due to module failure: {error_desc}",
            error={
                "code": "BROKEN_MODULE",
                "module": module_name,
                "command": command_name,
                "error": error_desc,
            },
        )

    return broken_command_handler
