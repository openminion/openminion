from typing import Any

from openminion.modules.brain.interfaces import RLMAPI, BRAIN_ADAPTER_INTERFACE_VERSION


class RLMAdapter(RLMAPI):
    """Wraps the recursive family service to satisfy the brain RLMAPI protocol."""

    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, service: Any) -> None:
        self._service = service
        self.recursive_source = "real_rlm"

    def generate(
        self,
        *,
        session_id: str,
        agent_id: str,
        purpose: str,
        query: str,
        ts: dict[str, Any] | None = None,
        budgets: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
        meta_directive: dict[str, Any] | None = None,
        agent_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the recursive loop for a single agent turn."""
        response = self._service.generate(
            session_id=session_id,
            agent_id=agent_id,
            purpose=purpose,
            query=query,
            ts=ts,
            budgets=budgets,
            constraints=constraints,
            meta_directive=meta_directive,
            agent_policy=agent_policy,
        )
        tick_report = (
            response.telemetry.tick_reports[-1]
            if response.telemetry.tick_reports
            else None
        )
        return {
            "status": "completed",
            "final_text": response.final_text,
            "structured_output": response.structured_output,
            "ticks_used": response.telemetry.ticks_used,
            "stop_reason": response.telemetry.stop_reason,
            "evidence_refs": [
                e.model_dump(mode="json") for e in response.evidence_refs
            ],
            "write_intents": [
                w.model_dump(mode="json") for w in response.memory_write_intents
            ],
            "total_input_tokens": tick_report.input_tokens if tick_report else 0,
            "total_output_tokens": tick_report.output_tokens if tick_report else 0,
        }
