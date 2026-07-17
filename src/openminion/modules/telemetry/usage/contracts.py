"""Versioned token usage export contract constants."""

TOKEN_USAGE_SCHEMA_VERSION = "openminion.token_usage.v1"

TOTAL_SOURCE_PROVIDER = "provider"
TOTAL_SOURCE_DERIVED = "derived"
TOKEN_TOTAL_SOURCES = frozenset({TOTAL_SOURCE_PROVIDER, TOTAL_SOURCE_DERIVED})

__all__ = [
    "TOKEN_TOTAL_SOURCES",
    "TOKEN_USAGE_SCHEMA_VERSION",
    "TOTAL_SOURCE_DERIVED",
    "TOTAL_SOURCE_PROVIDER",
]
