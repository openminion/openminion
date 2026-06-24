GWS_DEFAULT_EXECUTABLE = "gws"
GWS_WRITE_METHODS: frozenset[str] = frozenset(
    {"create", "update", "patch", "delete", "modify", "batchupdate", "send", "insert"}
)
GWS_READ_METHOD_HINTS: frozenset[str] = frozenset(
    {"get", "list", "search", "lookup", "query", "watch", "check", "read", "retrieve"}
)
GWS_SECRET_ENV_PREFIX = "OPENMINION_SECRET_"

__all__ = [
    "GWS_DEFAULT_EXECUTABLE",
    "GWS_READ_METHOD_HINTS",
    "GWS_SECRET_ENV_PREFIX",
    "GWS_WRITE_METHODS",
]
