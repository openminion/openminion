"""RMP-internal constants."""

RMP_PARSER_VERSION_AST_V1 = "ast-1"
RMP_DEFAULT_PROFILE_GATE = ("coding",)
RMP_DEFAULT_TOKEN_BUDGET = 1500
# Used for output-budget enforcement, not prompt accounting.
RMP_CHARS_PER_TOKEN_HEURISTIC = 4


__all__ = [
    "RMP_CHARS_PER_TOKEN_HEURISTIC",
    "RMP_DEFAULT_PROFILE_GATE",
    "RMP_DEFAULT_TOKEN_BUDGET",
    "RMP_PARSER_VERSION_AST_V1",
]
