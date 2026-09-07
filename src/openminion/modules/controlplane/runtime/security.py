from dataclasses import dataclass
from typing import Protocol

from ..constants import (
    AUTH_ROLE_UNPAIRED,
    DEFAULT_MINIMAL_SCOPES,
    PRINCIPAL_BINDING_STATUS_ACTIVE,
)
from ..contracts.models import AuthContext, InboundMessage, ParsedCommand

_COMMAND_SCOPE_OVERRIDES: dict[str, tuple[str, ...]] = {
    "cancel": ("run.cancel",),
    "approve": ("policy.approve",),
    "deny": ("policy.approve",),
    "grants": ("policy.approve",),
}


class CommandScopeRegistry(Protocol):
    def get_command_required_scopes(
        self, command_name: str
    ) -> tuple[str, ...] | None: ...


def is_pair_command(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    return stripped.split(maxsplit=1)[0].casefold() == "/pair"


@dataclass
class ScopeAuthorizer:
    store: object | None = None
    identity_api: object | None = None
    command_registry: CommandScopeRegistry | None = None
    default_scopes: tuple[str, ...] = DEFAULT_MINIMAL_SCOPES

    def _auth_from_principal_mapping(
        self, *, channel: str, chat_id: str
    ) -> AuthContext | None:
        if self.identity_api is not None:
            auth_context = getattr(self.identity_api, "auth_context", None)
            if callable(auth_context):
                auth = auth_context(
                    channel=channel,
                    subject_id=chat_id,
                    default_scopes=self.default_scopes,
                )
                if auth is not None:
                    self._touch_channel_subject(channel=channel, subject_id=chat_id)
                    return auth
        if self.store is None:
            return None
        resolve_principal = getattr(self.store, "resolve_principal", None)
        get_channel_subject = getattr(self.store, "get_channel_subject", None)
        if not callable(resolve_principal) or not callable(get_channel_subject):
            return None
        principal_id = resolve_principal(channel=channel, subject_id=chat_id)
        if not principal_id:
            return None
        binding = get_channel_subject(channel=channel, subject_id=chat_id) or {}
        status = (
            str(binding.get("status") or PRINCIPAL_BINDING_STATUS_ACTIVE)
            .strip()
            .lower()
        )
        if status != PRINCIPAL_BINDING_STATUS_ACTIVE:
            return None
        scopes = binding.get("scopes") or list(self.default_scopes)
        self._touch_channel_subject(channel=channel, subject_id=chat_id)
        return AuthContext(
            role="paired",
            scopes=tuple(str(scope) for scope in scopes),
            principal_id=str(principal_id),
            metadata={
                "principal_id": str(principal_id),
                "principal_binding": {
                    "channel": channel,
                    "subject_id": chat_id,
                    "status": status,
                },
            },
        )

    def auth_for_inbound(self, inbound: InboundMessage) -> AuthContext:
        if inbound.auth is not None:
            return inbound.auth

        channel = str(inbound.channel or "")
        chat_id = str(inbound.chat_id or inbound.chat_key or "")
        principal_auth = self._auth_from_principal_mapping(
            channel=channel, chat_id=chat_id
        )
        if principal_auth is not None:
            return principal_auth
        if self.store is not None and hasattr(self.store, "get_pairing"):
            pairing = self.store.get_pairing(channel=channel, chat_id=chat_id)
            if pairing:
                scopes = pairing.get("scopes") or list(self.default_scopes)
                return AuthContext(
                    role="paired",
                    scopes=tuple(str(scope) for scope in scopes),
                    principal_id=str(pairing.get("pairing_id") or ""),
                    metadata={"pairing": pairing},
                )

        return AuthContext(role=AUTH_ROLE_UNPAIRED, scopes=())

    def command_allowed(
        self, command: ParsedCommand, auth: AuthContext
    ) -> tuple[bool, str]:
        return self._scopes_allowed(self.required_scopes(command), auth)

    def message_allowed(self, auth: AuthContext) -> tuple[bool, str]:
        if "chat.interact" in auth.scopes:
            return True, "ok"
        return self._scopes_allowed(("cp.message.write", "run.start"), auth)

    def required_scopes(self, command: ParsedCommand) -> tuple[str, ...]:
        if self.command_registry is not None:
            configured = self.command_registry.get_command_required_scopes(
                command.canonical
            )
            if configured is not None:
                return configured
        return _COMMAND_SCOPE_OVERRIDES.get(command.canonical, self.default_scopes)

    @staticmethod
    def _scopes_allowed(
        required: tuple[str, ...], auth: AuthContext
    ) -> tuple[bool, str]:
        granted = set(auth.scopes)
        missing = [scope for scope in required if scope not in granted]
        if missing:
            return False, f"missing scopes: {', '.join(missing)}"
        return True, "ok"

    def _touch_channel_subject(self, *, channel: str, subject_id: str) -> None:
        if self.store is None:
            return
        touch_channel_subject = getattr(self.store, "touch_channel_subject", None)
        if callable(touch_channel_subject):
            touch_channel_subject(channel=channel, subject_id=subject_id)
