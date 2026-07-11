"""Ephemeral smoke memory provider used for runtime and API contract tests.

This provider is intentionally non-durable. It lives under the memory module
owner so demo/smoke behavior does not grow inside service-layer agent wiring.
"""

import hashlib
import logging
from dataclasses import dataclass

from openminion.base.time import utc_now_iso as _utc_now_iso
from openminion.modules.memory.errors import MemoryQueryUnavailableError
from openminion.modules.memory.interfaces import ListQueryOptions, SearchQueryOptions
from openminion.modules.memory.models import MemoryPatchResult

SMOKE_MEMORY_ENVELOPE_VERSION = "memory_envelope.v1"


@dataclass
class _SessionState:
    generation: int
    last_patch_id: str
    updated_at: str


class EphemeralMemorySmokeProvider:
    contract_version = "v1"

    def __init__(
        self,
        *,
        agent_id: str,
        logger: logging.Logger | None = None,
        enabled: bool = True,
    ) -> None:
        self._agent_id = str(agent_id or "").strip() or "openminion"
        self._logger = logger or logging.getLogger(__name__)
        self._enabled = bool(enabled)
        self._sessions: dict[str, _SessionState] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def list_records(self, options: ListQueryOptions) -> list[object]:
        del options
        raise MemoryQueryUnavailableError(
            "ephemeral smoke memory does not expose durable record queries"
        )

    def search_records(self, options: SearchQueryOptions) -> list[object]:
        del options
        raise MemoryQueryUnavailableError(
            "ephemeral smoke memory does not expose durable record queries"
        )

    def _state(self, session_id: str) -> _SessionState:
        session_key = str(session_id or "").strip() or "default"
        current = self._sessions.get(session_key)
        if current is None:
            current = _SessionState(
                generation=0,
                last_patch_id="",
                updated_at=_utc_now_iso(),
            )
            self._sessions[session_key] = current
        return current

    def _build_metadata(
        self,
        *,
        lane: str,
        text: str,
        facts_before: int,
        facts_after: int,
    ) -> dict[str, str]:
        return {
            "memory_envelope_version": SMOKE_MEMORY_ENVELOPE_VERSION,
            "memory_envelope_lane": lane,
            "memory_envelope_limit_chars": str(max(0, len(text))),
            "memory_envelope_chars_before": str(max(0, len(text))),
            "memory_envelope_chars_after": str(max(0, len(text))),
            "memory_envelope_truncated": "false",
            "memory_envelope_truncation_reasons": "",
            "memory_envelope_state_chars": "0",
            "memory_envelope_facts_before": str(max(0, facts_before)),
            "memory_envelope_facts_after": str(max(0, facts_after)),
            "memory_envelope_tasks_before": "0",
            "memory_envelope_tasks_after": "0",
        }

    def build_context(self, *, session_id: str, user_message: str) -> str:
        text, _ = self.build_context_with_metadata(
            session_id=session_id,
            user_message=user_message,
        )
        return text

    def build_context_with_metadata(
        self, *, session_id: str, user_message: str
    ) -> tuple[str, dict[str, str]]:
        del user_message
        if not self._enabled:
            return "", self._build_metadata(
                lane="capsule", text="", facts_before=0, facts_after=0
            )
        del session_id
        lines = [
            "Agent canonical memory (cross-session):",
            "",
            "Relevant facts:",
            "- ephemeral-memory-smoke provider is active",
        ]
        text = "\n".join(lines).strip()
        return text, self._build_metadata(
            lane="capsule",
            text=text,
            facts_before=1,
            facts_after=1,
        )

    def build_retrieval_context(
        self,
        *,
        session_id: str,
        user_message: str,
        max_chars: int | None = None,
    ) -> str:
        text, _ = self.build_retrieval_context_with_metadata(
            session_id=session_id,
            user_message=user_message,
            max_chars=max_chars,
        )
        return text

    def build_retrieval_context_with_metadata(
        self,
        *,
        session_id: str,
        user_message: str,
        max_chars: int | None = None,
    ) -> tuple[str, dict[str, str]]:
        del user_message, max_chars
        if not self._enabled:
            return "", self._build_metadata(
                lane="retrieval", text="", facts_before=0, facts_after=0
            )
        del session_id
        lines = [
            "Agent memory (dynamic retrieval):",
            "",
            "Relevant facts:",
            "- retrieval channel from ephemeral-memory-smoke provider",
        ]
        text = "\n".join(lines).strip()
        return text, self._build_metadata(
            lane="retrieval",
            text=text,
            facts_before=1,
            facts_after=1,
        )

    def derive_patch_id(
        self,
        *,
        session_id: str,
        run_id: str,
        request_id: str,
        user_message: str,
    ) -> str:
        raw = f"{session_id}|{run_id}|{request_id}|{(user_message or '').strip()}"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        return f"patch-{digest}"

    def record_turn(
        self,
        *,
        session_id: str,
        run_id: str,
        request_id: str,
        channel: str,
        target: str,
        user_message: str,
        assistant_message: str,
    ) -> MemoryPatchResult:
        del channel, target, assistant_message
        if not self._enabled:
            return MemoryPatchResult(facts_added=0, todos_added=0, todos_completed=0)
        state = self._state(session_id)
        patch_id = self.derive_patch_id(
            session_id=session_id,
            run_id=run_id,
            request_id=request_id,
            user_message=user_message,
        )
        state.generation += 1
        state.last_patch_id = patch_id
        state.updated_at = _utc_now_iso()
        return MemoryPatchResult(
            facts_added=0,
            todos_added=0,
            todos_completed=0,
            patch_id=patch_id,
            generation=state.generation,
            replayed_patches=0,
            lock_recovered=False,
        )
