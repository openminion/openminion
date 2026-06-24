class CompressionError(RuntimeError):
    """Base exception for compression failures."""


class ValidationError(CompressionError):
    """Raised when inbound payloads fail validation."""


class PolicyError(CompressionError):
    """Raised when a compression policy is invalid or unsupported."""


class MethodError(CompressionError):
    """Raised when a compression method fails or is unavailable."""


class BudgetError(CompressionError):
    """Raised when token budgets cannot be satisfied."""
