from typing import Final, Literal

GWS_DEFAULT_EXECUTABLE = "gws"
GWS_WRITE_METHODS: frozenset[str] = frozenset(
    {"create", "update", "patch", "delete", "modify", "batchupdate", "send", "insert"}
)
GWS_READ_METHOD_HINTS: frozenset[str] = frozenset(
    {"get", "list", "search", "lookup", "query", "watch", "check", "read", "retrieve"}
)
GWS_SECRET_ENV_PREFIX = "OPENMINION_SECRET_"
GWS_RISK_READ: Final[Literal["read"]] = "read"
GWS_RISK_WRITE: Final[Literal["write"]] = "write"
GWS_RISK_ADMIN: Final[Literal["admin"]] = "admin"
GWS_REDACTION_NONE: Final[Literal["none"]] = "none"
GWS_REDACTION_BASIC: Final[Literal["basic"]] = "basic"
GWS_REDACTION_STRICT: Final[Literal["strict"]] = "strict"
GWS_REDACTION_MODES: frozenset[str] = frozenset(
    {GWS_REDACTION_NONE, GWS_REDACTION_BASIC, GWS_REDACTION_STRICT}
)

__all__ = [
    "GWS_DEFAULT_EXECUTABLE",
    "GWS_READ_METHOD_HINTS",
    "GWS_REDACTION_BASIC",
    "GWS_REDACTION_MODES",
    "GWS_REDACTION_NONE",
    "GWS_REDACTION_STRICT",
    "GWS_RISK_ADMIN",
    "GWS_RISK_READ",
    "GWS_RISK_WRITE",
    "GWS_SECRET_ENV_PREFIX",
    "GWS_WRITE_METHODS",
]
