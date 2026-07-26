from typing import Final, Literal

FETCH_ARTIFACTS_SUBDIR = "artifacts/fetch"

# Provider-id constants used by shared routing/hint logic. Provider-only
FETCH_PROVIDER_ID_CORE_HTTP = "core-http"
FETCH_PROVIDER_ID_SCRAPLING = "scrapling"
FETCH_PROVIDER_ID_TINYFISH = "tinyfish"
FETCH_PROVIDER_ID_FIRECRAWL = "firecrawl"
FETCH_BACKEND_AUTO: Final[Literal["auto"]] = "auto"
FETCH_EXTRACT_MODE_NONE: Final[Literal["none"]] = "none"
FETCH_EXTRACT_MODE_TEXT: Final[Literal["text"]] = "text"
FETCH_SCRAPLING_MODE_STATIC: Final[Literal["static"]] = "static"
FETCH_SCRAPLING_MODE_DYNAMIC: Final[Literal["dynamic"]] = "dynamic"
FETCH_SCRAPLING_MODE_STEALTH: Final[Literal["stealth"]] = "stealth"
FETCH_SCRAPLING_MODES: frozenset[str] = frozenset(
    {
        FETCH_SCRAPLING_MODE_STATIC,
        FETCH_SCRAPLING_MODE_DYNAMIC,
        FETCH_SCRAPLING_MODE_STEALTH,
    }
)

__all__ = [
    "FETCH_ARTIFACTS_SUBDIR",
    "FETCH_PROVIDER_ID_CORE_HTTP",
    "FETCH_PROVIDER_ID_SCRAPLING",
    "FETCH_PROVIDER_ID_TINYFISH",
    "FETCH_PROVIDER_ID_FIRECRAWL",
    "FETCH_BACKEND_AUTO",
    "FETCH_EXTRACT_MODE_NONE",
    "FETCH_EXTRACT_MODE_TEXT",
    "FETCH_SCRAPLING_MODE_DYNAMIC",
    "FETCH_SCRAPLING_MODE_STATIC",
    "FETCH_SCRAPLING_MODE_STEALTH",
    "FETCH_SCRAPLING_MODES",
]
