from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.pairing import (
    ControlPlanePairingService,
    ControlPlanePairingStore,
    PairingAttempt,
    PairingPolicy,
)
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore


class NonTelegramPairingAdapter:
    channel_id = "fakechat"
    account_namespace = "fakechat-app"

    def extract_pairing_attempt(
        self,
        inbound: InboundMessage,
        *,
        channel_context: dict[str, Any] | None = None,
    ) -> PairingAttempt | None:
        if not inbound.text.startswith("/openminion pair "):
            return None
        return PairingAttempt(
            channel="fakechat",
            token=inbound.text.rsplit(" ", 1)[-1],
            account_id=inbound.user_key,
            chat_key=inbound.chat_key,
            chat_type="private",
            extra={"subject_id": inbound.chat_key, "user_id": inbound.user_key},
        )

    def format_pairing_hint(self, token: str, *, ttl_seconds: int) -> str:
        return f"use {token} within {ttl_seconds}s"

    def format_success_reply(self) -> str:
        return "paired"

    def format_failure_reply(self, reason: str) -> str:
        return reason


def test_non_telegram_pairing_adapter_consumes_without_channel_imports() -> None:
    store = InMemoryControlPlaneStore()
    service = ControlPlanePairingService(
        policy=PairingPolicy(default_scopes=["chat.interact"]),
        store=ControlPlanePairingStore(store),
        adapter=NonTelegramPairingAdapter(),
        bridge_store=store,
    )
    issued = service.issue_token(
        expected_account_id="fakechat:user:U1",
        expected_chat_key="fakechat:chat:C1",
        token_ttl_seconds=60,
        scopes=["chat.interact"],
        token="fake_token",
    )
    result = service.handle_pairing_attempt(
        InboundMessage(
            channel="fakechat",
            user_key="fakechat:user:U1",
            chat_key="fakechat:chat:C1",
            text=f"/openminion pair {issued.token}",
        )
    )
    assert result.reply_text == "paired"
    assert store.get_pairing(channel="fakechat", chat_id="fakechat:chat:C1")


def test_generic_pairing_core_imports_no_channel_modules() -> None:
    root = Path(__file__).parents[2] / "src/openminion/modules/controlplane/pairing"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        ]
        assert not any(".channels.telegram" in item for item in imports), path
        assert not any(".channels.slack" in item for item in imports), path
