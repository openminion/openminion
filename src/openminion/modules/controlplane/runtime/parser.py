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
        parts = body.split()
        head = parts[0].lower()
        rest = parts[1:]

        canonical = head
        args = rest

        if "." in head:
            canonical = head
        elif rest:
            canonical = f"{head}.{rest[0].lower()}"
            args = rest[1:]

        return ParsedCommand(canonical=canonical, original_text=stripped, args=args)
