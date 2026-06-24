from dataclasses import dataclass, field

from .errors import BudgetError
from .schemas import CompressionBudgets


@dataclass(frozen=True)
class BudgetEnvelope:
    total_cap: int
    reserve_tokens: int
    per_type_caps: dict[str, int]
    hard_cap: bool

    def remaining_total(self, used_tokens: int) -> int:
        return self.total_cap - used_tokens

    def remaining_for_type(self, block_type: str, used_tokens: int) -> int:
        cap = self.per_type_caps.get(block_type, self.total_cap)
        return cap - used_tokens


@dataclass
class BudgetState:
    envelope: BudgetEnvelope
    used_total: int = 0
    used_by_type: dict[str, int] = field(default_factory=dict)

    def try_allocate(self, block_type: str, tokens: int) -> None:
        next_total = self.used_total + tokens
        if self.envelope.hard_cap and next_total > self.envelope.total_cap:
            raise BudgetError("total token cap exceeded")

        current_type = self.used_by_type.get(block_type, 0)
        next_type = current_type + tokens
        type_cap = self.envelope.per_type_caps.get(block_type, self.envelope.total_cap)
        if self.envelope.hard_cap and next_type > type_cap:
            raise BudgetError(f"per-type token cap exceeded for {block_type}")

        self.used_total = next_total
        self.used_by_type[block_type] = next_type


class BudgetPlanner:
    """Creates deterministic budget envelopes and allocation state."""

    def plan(self, budgets: CompressionBudgets) -> BudgetEnvelope:
        if budgets.max_output_tokens_total <= 0:
            raise BudgetError("total token budget must be positive")

        total_cap = budgets.max_output_tokens_total - budgets.reserve_tokens_for_headers
        if total_cap <= 0:
            raise BudgetError("reserve consumes entire budget")

        per_type_caps = {}
        for key, value in budgets.max_output_tokens_by_type.items():
            if value <= 0:
                raise BudgetError(f"invalid per-type cap for {key}")
            per_type_caps[key] = value

        return BudgetEnvelope(
            total_cap=total_cap,
            reserve_tokens=budgets.reserve_tokens_for_headers,
            per_type_caps=per_type_caps,
            hard_cap=budgets.hard_cap,
        )

    def new_state(self, envelope: BudgetEnvelope) -> BudgetState:
        return BudgetState(envelope=envelope)
