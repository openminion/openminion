from typing import Any

from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION
from openminion.modules.brain.schemas.base import new_uuid

from ..session.local_store import LocalSessionStore


class LocalContextAdapter:
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, *, session_store: LocalSessionStore) -> None:
        self.session_store = session_store

    def build(
        self,
        *,
        session_id: str,
        agent_id: str,
        purpose: str,
        budget: dict[str, Any],
        hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        turns = self.session_store.list_turns(session_id)[-10:]
        events = self.session_store.list_events(session_id)[-25:]
        normalized_hints = {} if hints is None else dict(hints)
        context_budget_tier = normalized_hints.get("context_budget_tier")
        context_manifest = (
            {"context_budget_tier": context_budget_tier.strip()}
            if isinstance(context_budget_tier, str) and context_budget_tier.strip()
            else {}
        )
        return {
            "session_id": session_id,
            "agent_id": agent_id,
            "purpose": purpose,
            "budget": budget,
            "hints": normalized_hints,
            "turns": turns,
            "events": events,
            "context_manifest": context_manifest,
        }

    def make_delta(self, *, session_id: str, agent_id: str, content: str = "") -> str:
        del content
        return f"delta://{session_id}/{agent_id}/{new_uuid()}"

    def maybe_compact(self, *, session_id: str, agent_id: str) -> bool:
        return False
