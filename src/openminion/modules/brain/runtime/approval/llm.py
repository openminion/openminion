"""Small-model implementation of the action approval verifier."""

import time
from dataclasses import dataclass
from typing import Any, Protocol

from openminion.modules.brain.constants import (
    VGD_DEFAULT_TIMEOUT_SECONDS,
    VGD_TIMEOUT_ESCALATE_RATIONALE,
)
from .protocol import ApprovalCriteria, ApprovalVerdict


class _SmallModelClient(Protocol):
    """Small-model client protocol used by the verifier."""

    def call(
        self, *, prompt: str, timeout_seconds: int
    ) -> dict[str, Any]:  # pragma: no cover - structural
        ...


def _format_prompt(
    *, action: dict[str, Any], state: dict[str, Any], criteria: ApprovalCriteria
) -> str:
    return (
        "[ACTION APPROVAL VERIFIER]\n"
        f"tool_id: {criteria.tool_id}\n"
        f"action: {criteria.action}\n"
        "criteria:\n"
        f"{criteria.criteria_text}\n"
        "[REQUESTED ACTION]\n"
        f"{action}\n"
        "[STATE]\n"
        f"{state}\n"
        "Return one of: approve | reject | escalate with rationale."
    )


@dataclass
class LLMActionApprovalVerifier:
    """Call a small-model client and enforce a hard timeout."""

    client: _SmallModelClient
    model_name: str = "claude-haiku-3.5"
    timeout_seconds: int = VGD_DEFAULT_TIMEOUT_SECONDS
    escalate_on_timeout: bool = True

    def verify(
        self,
        *,
        action: dict[str, Any],
        state: dict[str, Any],
        criteria: ApprovalCriteria,
    ) -> ApprovalVerdict:
        prompt = _format_prompt(action=action, state=state, criteria=criteria)
        start = time.monotonic()
        try:
            response = self.client.call(
                prompt=prompt, timeout_seconds=self.timeout_seconds
            )
        except TimeoutError:
            return ApprovalVerdict(
                decision="escalate" if self.escalate_on_timeout else "reject",
                rationale=VGD_TIMEOUT_ESCALATE_RATIONALE,
                model=self.model_name,
                latency_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            return ApprovalVerdict(
                decision="escalate",
                rationale=f"verifier_error:{exc}",
                model=self.model_name,
                latency_ms=int((time.monotonic() - start) * 1000),
            )

        decision = str(response.get("decision", "")).strip().lower()
        rationale = str(response.get("rationale", "")).strip()
        if decision not in {"approve", "reject", "escalate"}:
            decision = "escalate"
            if not rationale:
                rationale = "verifier_returned_invalid_decision"
        return ApprovalVerdict(
            decision=decision,  # type: ignore[arg-type]
            rationale=rationale,
            model=self.model_name,
            latency_ms=int((time.monotonic() - start) * 1000),
        )


__all__ = ["LLMActionApprovalVerifier"]
