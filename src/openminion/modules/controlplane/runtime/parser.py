from ..interfaces import CONTROLPLANE_INTERFACE_VERSION
from ..contracts.models import CommandParser, ParsedCommand


class SlashCommandParser(CommandParser):
    """Minimal parser for CLI demo supporting `/command arg1 arg2` syntax."""

    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def parse(self, text: str) -> ParsedCommand | None:
        stripped = (text or "").strip()
        if not (stripped.startswith("/") or stripped.startswith("!")):
            return None
        body = stripped[1:].strip()
        if not body:
            return None
        head, *args = body.split()
        canonical = head.lower()
        if "." not in head and args:
            canonical = f"{canonical}.{args.pop(0).lower()}"

        return ParsedCommand(canonical=canonical, original_text=stripped, args=args)
