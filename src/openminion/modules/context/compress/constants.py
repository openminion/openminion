from pathlib import Path

CHECKPOINT_ERROR_BUDGET_EXCEEDED = "budget_exceeded"
CHECKPOINT_ERROR_RANGE_INVALID = "range_invalid"
CHECKPOINT_ERROR_STABLE_ID_COLLISION = "stable_id_collision"
CHECKPOINT_ERROR_INVARIANT_VIOLATED = "invariant_violated"
CHECKPOINT_ERROR_PIPELINE_FAILED = "pipeline_failed"

DEFAULT_QUALITY_OVERRIDES: dict[str, str | None] = {
    "GOOD": None,
    "OK": None,
    "BAD": "extractive.v1",
}

# Path Layout
DEFAULT_CONFIG_FILENAME = "compress.yaml"
DEFAULT_INTEGRATED_SQLITE_SUBPATH = Path("compress") / "compress.db"

# Operator preference for the main compression provider.
COMPRESS_PROVIDER_PREFERENCE_ENV: str = "OPENMINION_COMPRESS_PROVIDER_PREFERENCE"
COMPRESS_PROVIDER_PREFERENCE_AUTO: str = "auto"
COMPRESS_PROVIDER_PREFERENCE_LLMLINGUA2: str = "llmlingua2"
COMPRESS_PROVIDER_PREFERENCE_LONGLLMLINGUA: str = "longllmlingua"
COMPRESS_PROVIDER_PREFERENCE_EXTRACTIVE: str = "extractive"
COMPRESS_PROVIDER_PREFERENCE_DEFAULT: str = COMPRESS_PROVIDER_PREFERENCE_AUTO
COMPRESS_PROVIDER_PREFERENCE_ALLOWED: frozenset[str] = frozenset(
    {
        COMPRESS_PROVIDER_PREFERENCE_AUTO,
        COMPRESS_PROVIDER_PREFERENCE_LLMLINGUA2,
        COMPRESS_PROVIDER_PREFERENCE_LONGLLMLINGUA,
        COMPRESS_PROVIDER_PREFERENCE_EXTRACTIVE,
    }
)

# Mapping from operator-preference value to the canonical method_id the
# registry uses. ``auto`` deliberately maps to None — the resolver chain
# handles it without an explicit override.
COMPRESS_PROVIDER_PREFERENCE_TO_METHOD_ID: dict[str, str | None] = {
    COMPRESS_PROVIDER_PREFERENCE_AUTO: None,
    COMPRESS_PROVIDER_PREFERENCE_LLMLINGUA2: "llmlingua2.v1",
    COMPRESS_PROVIDER_PREFERENCE_LONGLLMLINGUA: "longllmlingua.v1",
    COMPRESS_PROVIDER_PREFERENCE_EXTRACTIVE: "extractive.v1",
}
