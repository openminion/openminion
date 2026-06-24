from typing import Any

from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION


class LocalRLMAdapter:
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION
    recursive_source = "local_mock"

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
        del (
            session_id,
            agent_id,
            purpose,
            ts,
            budgets,
            constraints,
            meta_directive,
            agent_policy,
        )
        return {
            "status": "completed",
            "final_text": f"MOCK RLM output for {query[:20]}...",
            "structured_output": None,
            "ticks_used": 1,
            "stop_reason": "completed",
            "evidence_refs": [],
            "write_intents": [],
            "total_input_tokens": 10,
            "total_output_tokens": 10,
        }
