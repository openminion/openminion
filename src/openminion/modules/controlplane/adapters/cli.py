import argparse
import sys
from dataclasses import dataclass

from openminion.modules.controlplane.adapters.base import Adapter, InboundHandler
from openminion.modules.controlplane.interfaces import CONTROLPLANE_INTERFACE_VERSION
from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.cli_common import add_common_module_root_args


@dataclass
class CLIAdapter(Adapter):
    handler: InboundHandler
    contract_version: str = CONTROLPLANE_INTERFACE_VERSION
    once: bool = False
    input_text: str | None = None

    def start(self) -> None:
        if self.once:
            text = self.input_text or ""
            inbound = InboundMessage(
                user_key="cli:user", chat_key="cli:chat", text=text
            )
            self.handler.handle_inbound(inbound)
            return

        print("openminion-controlplane CLI ready. Type /help or text.")
        for line in sys.stdin:
            line = line.rstrip("\n")
            inbound = InboundMessage(
                user_key="cli:user", chat_key="cli:chat", text=line
            )
            self.handler.handle_inbound(inbound)


def parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="openminion-controlplane CLI")
    parser.add_argument(
        "--once", action="store_true", help="Process a single input and exit"
    )
    parser.add_argument(
        "--input", type=str, default="", help="Text input for --once mode"
    )
    add_common_module_root_args(parser)
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to controlplane configuration file (YAML or JSON)",
    )
    parser.add_argument(
        "--storage-command",
        default=None,
        help="Storage command (status/plan/migrate/backup/restore/verify/export/import)",
    )
    parser.add_argument("--root", default=None, help="Blob root override")
    parser.add_argument(
        "--fallback", default=None, help="Fallback sidecar root override"
    )
    parser.add_argument("--snapshot-root", default=None)
    parser.add_argument("--snapshot-path", default=None)
    parser.add_argument("--mode", default=None)
    parser.add_argument("--level", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--notes", default=None)
    parser.add_argument(
        "--storage-input",
        dest="storage_input",
        default=None,
        help="OMX import input directory (storage import only)",
    )
    parser.add_argument("--skip-checksum", action="store_true")
    return parser.parse_args(argv)
