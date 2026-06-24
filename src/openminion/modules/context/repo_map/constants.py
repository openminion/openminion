"""RMP-internal constants."""

RMP_PARSER_VERSION_AST_V1 = "ast-1"

# Default coding-profile gate token.  Operators can extend via config.
RMP_DEFAULT_PROFILE_GATE = ("coding",)

# Default token budget for serialized repo-map output.
RMP_DEFAULT_TOKEN_BUDGET = 1500

# Approximate chars-per-token (cheap heuristic — matches typical English
# code+prose density; used only for budget enforcement in the
# serializer, not for prompt accounting).
RMP_CHARS_PER_TOKEN_HEURISTIC = 4


__all__ = [
    "RMP_CHARS_PER_TOKEN_HEURISTIC",
    "RMP_DEFAULT_PROFILE_GATE",
    "RMP_DEFAULT_TOKEN_BUDGET",
    "RMP_PARSER_VERSION_AST_V1",
]
