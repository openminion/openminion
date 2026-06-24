import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Dict, List

from openminion.services.agent.memory import (
    MEMORY_ENVELOPE_VERSION,
    MemoryPatchResult,
)

from openminion.base.time import utc_now_iso as _utc_now_iso

_FACT_PREFIX_RE = re.compile(r"^\s*(?:remember|fact)\s*:\s*(.+)$", flags=re.IGNORECASE)
_FACT_INLINE_RE = re.compile(r"^\s*remember\s+(.+)$", flags=re.IGNORECASE)


@dataclass
class _SessionState:
    generation: int
    facts: List[str]
    last_patch_id: str
    updated_at: str


class HelloWorldMemoryService:
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
        self._sessions: Dict[str, _SessionState] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _state(self, session_id: str) -> _SessionState:
        session_key = str(session_id or "").strip() or "default"
        current = self._sessions.get(session_key)
        if current is None:
            current = _SessionState(
                generation=0,
                facts=[],
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
            "memory_envelope_version": MEMORY_ENVELOPE_VERSION,
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
        state = self._state(session_id)
        lines = [
            "Agent canonical memory (cross-session):",
            "",
            "Relevant facts:",
            "- hello-world-memory-v2 is active",
        ]
        for item in state.facts[-5:]:
            lines.append(f"- {item}")
        text = "\n".join(lines).strip()
        return text, self._build_metadata(
            lane="capsule",
            text=text,
            facts_before=max(0, len(state.facts) + 1),
            facts_after=max(0, len(state.facts[-5:]) + 1),
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
        state = self._state(session_id)
        lines = [
            "Agent memory (dynamic retrieval):",
            "",
            "Relevant facts:",
            "- retrieval channel from hello-world-memory-v2",
        ]
        for item in state.facts[-3:]:
            lines.append(f"- {item}")
        text = "\n".join(lines).strip()
        return text, self._build_metadata(
            lane="retrieval",
            text=text,
            facts_before=max(0, len(state.facts) + 1),
            facts_after=max(0, len(state.facts[-3:]) + 1),
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

    def _extract_facts(self, user_message: str) -> list[str]:
        values: list[str] = []
        for raw in str(user_message or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            prefixed = _FACT_PREFIX_RE.match(line)
            if prefixed is not None:
                text = str(prefixed.group(1) or "").strip()
                if text:
                    values.append(text)
                continue
            inline = _FACT_INLINE_RE.match(line)
            if inline is not None:
                text = str(inline.group(1) or "").strip()
                if text:
                    values.append(text)
        return values

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
        facts = self._extract_facts(user_message)
        if facts:
            state.facts.extend(facts)
            state.facts = state.facts[-50:]
        state.generation += 1
        state.last_patch_id = patch_id
        state.updated_at = _utc_now_iso()
        return MemoryPatchResult(
            facts_added=len(facts),
            todos_added=0,
            todos_completed=0,
            patch_id=patch_id,
            generation=state.generation,
            replayed_patches=0,
            lock_recovered=False,
        )
