# mypy: ignore-errors
from __future__ import annotations

from openminion.modules.controlplane.contracts.models import (
    CommandResult,
    ParsedCommand,
    ResolvedContext,
)


class CommandRegistryPairingMixin:
    def _pair_status(
                self, command: ParsedCommand, ctx: ResolvedContext
            ) -> CommandResult:
                pairing = self._current_pairing(ctx)
                if pairing is None:
                    return CommandResult(
                        ok=True,
                        text=(
                            "No active pairing found for this chat. "
                            "Ask the owner to run `openminion channel telegram pair`."
                        ),
                        data={"paired": False},
                    )
                scopes = pairing.get("scopes") or []
                scope_text = self._describe_scopes(scopes)
                return CommandResult(
                    ok=True,
                    text=(
                        "Pairing active for this chat.\n"
                        f"  pairing_id: {pairing.get('pairing_id', 'unknown')}\n"
                        f"  scopes: {scope_text}\n"
                        "  access: broad non-admin controlplane access until ACL exists"
                    ),
                    data={"paired": True, "pairing": pairing},
                )

    def _pair_revoke(
                self, command: ParsedCommand, ctx: ResolvedContext
            ) -> CommandResult:
                channel, chat_id = self._current_channel_subject(ctx)
                pairing = self._current_pairing(ctx)
                if channel is None or chat_id is None or pairing is None:
                    return CommandResult(
                        ok=True,
                        text="No active pairing found for this chat.",
                        data={"revoked": False},
                    )
                upsert_pairing = getattr(self.store, "upsert_pairing", None)
                if not callable(upsert_pairing):
                    return CommandResult(
                        ok=False,
                        text="Pairing revoke is not available in this backend.",
                        error={"code": "PAIRING_REVOKE_UNAVAILABLE"},
                    )
                upsert_pairing(
                    channel=channel,
                    chat_id=chat_id,
                    user_id=str(pairing.get("user_id") or ctx.user_key),
                    session_id=str(pairing.get("session_id") or ctx.session_id),
                    status="revoked",
                    scopes=pairing.get("scopes") or [],
                    note="revoked_from_controlplane_chat",
                    pairing_id=str(pairing.get("pairing_id") or ""),
                )
                return CommandResult(
                    ok=True,
                    text="Pairing revoked for this chat.",
                    data={"revoked": True, "chat_id": chat_id, "channel": channel},
                )
