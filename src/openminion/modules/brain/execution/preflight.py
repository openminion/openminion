from dataclasses import dataclass, field
from typing import Any

from .loop_contracts import ExecutionResult
from ..schemas import Plan


@dataclass(slots=True)
class ModePreparation:
    mode_result: ExecutionResult | None = None
    consume_user_input_for_command: bool = False
    candidate_plan: Plan | None = None


@dataclass(slots=True)
class ValidationResult:
    passed: bool
    feedback: str | None = None
    should_retry: bool = False
    redirect_mode: str | None = None
    code: str = ""
    details: dict[str, Any] = field(default_factory=dict)


__all__ = ["ModePreparation", "ValidationResult"]
