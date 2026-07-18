from __future__ import annotations

from dataclasses import dataclass, field

from openminion.modules.controlplane.constants import DEFAULT_MINIMAL_SCOPES

PAIRING_MODE_OFF = "off"


@dataclass(frozen=True)
class PairingPolicy:
    """Channel-neutral pairing policy used by ControlPlanePairingService."""

    enabled: bool = True
    mode: str = "required"
    token_ttl_seconds: int = 600
    attempt_window_seconds: int = 60
    max_attempts_per_user: int = 6
    max_attempts_per_chat: int = 20
    hash_pepper: str | None = None
    allow_in_groups: bool = False
    default_scopes: list[str] = field(
        default_factory=lambda: list(DEFAULT_MINIMAL_SCOPES)
    )

    @classmethod
    def from_config(cls, config: object) -> "PairingPolicy":
        return cls(
            enabled=bool(getattr(config, "enabled", True)),
            mode=str(getattr(config, "mode", "required") or "required"),
            token_ttl_seconds=int(getattr(config, "token_ttl_seconds", 600)),
            attempt_window_seconds=int(getattr(config, "attempt_window_seconds", 60)),
            max_attempts_per_user=int(getattr(config, "max_attempts_per_user", 6)),
            max_attempts_per_chat=int(getattr(config, "max_attempts_per_chat", 20)),
            hash_pepper=getattr(config, "hash_pepper", None),
            allow_in_groups=bool(getattr(config, "allow_in_groups", False)),
            default_scopes=list(
                getattr(config, "default_scopes", DEFAULT_MINIMAL_SCOPES)
                or DEFAULT_MINIMAL_SCOPES
            ),
        )
