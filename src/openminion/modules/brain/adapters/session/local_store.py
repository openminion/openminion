import hashlib
import json
from pathlib import Path
from typing import Any

from ...interfaces import BRAIN_ADAPTER_INTERFACE_VERSION
from ...schemas import iso_now, new_uuid
from openminion.base.constants import STATE_KEY_ACTIVE


class LocalSessionStore:
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def append_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        attachments: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        turn_id = new_uuid()
        record = {
            "turn_id": turn_id,
            "session_id": session_id,
            "ts": iso_now(),
            "role": role,
            "content": content,
            "attachments": attachments or [],
            "meta": meta or {},
        }
        self._append_jsonl(self._turns_path(session_id), record)
        return turn_id

    def append_event(
        self,
        session_id: str,
        type: str,
        payload: dict[str, Any],
        *,
        agent_id: str | None = None,
        trace_id: str | None = None,
        task_id: str | None = None,
        parent_id: str | None = None,
        artifact_refs: list[str] | None = None,
        memory_refs: list[str] | None = None,
        status: str | None = None,
        error: dict[str, Any] | None = None,
        actor_type: str | None = None,
        actor_id: str | None = None,
        trace: dict[str, Any] | None = None,
        importance: int | None = None,
        redaction: str | None = None,
    ) -> str:
        trace_obj = trace if isinstance(trace, dict) else {}
        event_id = new_uuid()
        record = {
            "event_id": event_id,
            "session_id": session_id,
            "ts": iso_now(),
            "type": type,
            "payload": payload,
            "agent_id": agent_id or actor_id,
            "actor_type": actor_type,
            "trace_id": trace_id or trace_obj.get("trace_id"),
            "span_id": trace_obj.get("span_id"),
            "task_id": task_id,
            "parent_id": parent_id,
            "artifact_refs": artifact_refs or [],
            "memory_refs": memory_refs or [],
            "status": status,
            "error": error,
            "importance": importance,
            "redaction": redaction,
        }
        self._append_jsonl(self._events_path(session_id), record)
        return event_id

    def emit_canonical_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        actor_type: str = "system",
        actor_id: str | None = None,
        trace_id: str | None = None,
        task_id: str | None = None,
        importance: int = 1,
    ) -> str:
        return self.append_event(
            session_id=session_id,
            type=event_type,
            payload={**(payload or {}), "_canonical": True},
            agent_id=actor_id if actor_type in ("agent", "system") else None,
            trace_id=trace_id,
            task_id=task_id,
        )

    def put_working_state(
        self,
        session_id: str,
        *,
        state_ref: str | None = None,
        state_inline: dict[str, Any] | None = None,
    ) -> int:
        path = self._state_path(session_id)
        prior = self.get_latest_working_state(session_id)
        version = 1
        if prior and isinstance(prior.get("version"), int):
            version = int(prior["version"]) + 1
        payload = {
            "session_id": session_id,
            "ts": iso_now(),
            "version": version,
            "state_ref": state_ref,
            "state_inline": state_inline,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8"
        )
        return version

    def get_latest_working_state(self, session_id: str) -> dict[str, Any] | None:
        path = self._state_path(session_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def update_session_status(self, session_id: str, status: str) -> None:
        path = self._session_meta_path(session_id)
        existing: dict[str, Any] = {}
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
        existing["session_id"] = session_id
        existing["updated_at"] = iso_now()
        existing["status"] = status
        path.write_text(
            json.dumps(existing, ensure_ascii=True, indent=2), encoding="utf-8"
        )

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        return self._read_jsonl(self._events_path(session_id))

    def list_turns(self, session_id: str) -> list[dict[str, Any]]:
        return self._read_jsonl(self._turns_path(session_id))

    def get_slice(
        self,
        session_id: str,
        purpose: str,
        limits: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_limits = dict(limits or {})
        max_turns = int(
            normalized_limits.get("max_turns")
            or normalized_limits.get("recent_turn_limit")
            or 12
        )
        max_turns = max(1, max_turns)

        turns = self.list_turns(session_id)[-max_turns:]
        recent_turns = [
            {
                "turn_id": str(item.get("turn_id", "")),
                "role": str(item.get("role", "user")),
                "text": str(item.get("content", "")),
                "timestamp": str(item.get("ts", "")),
            }
            for item in turns
            if str(item.get("content", "")).strip()
        ]

        latest_state = self.get_latest_working_state(session_id)
        active_state = (
            dict(latest_state.get("state_inline", {}))
            if isinstance(latest_state, dict)
            and isinstance(latest_state.get("state_inline"), dict)
            else {}
        )
        summary_short = str(active_state.get("last_result", "") or "").strip()

        slice_payload = {
            "session_id": session_id,
            "purpose": purpose,
            "recent_turns": recent_turns,
            STATE_KEY_ACTIVE: active_state,
            "summary_short": summary_short,
            "last_event_id": str(latest_state.get("version", "0"))
            if isinstance(latest_state, dict)
            else "0",
        }
        slice_version = hashlib.sha256(
            json.dumps(
                slice_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode("utf-8")
        ).hexdigest()[:16]

        return {
            "session_id": session_id,
            "slice_version": f"local:{slice_version}",
            "summary_short": summary_short,
            "recent_turns": recent_turns,
            "open_tasks": list(active_state.get("open_questions", []))
            if isinstance(active_state, dict)
            else [],
            STATE_KEY_ACTIVE: active_state,
            "recent_tool_events": [],
            "prompt_context_id": None,
            "checkpoint_id": None,
            "seed_bundle_id": None,
        }

    def _session_dir(self, session_id: str) -> Path:
        path = self.root / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _turns_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "turns.jsonl"

    def _events_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "events.jsonl"

    def _state_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "working_state.json"

    def _session_meta_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "session.json"

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=True) + "\n")

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        items: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            items.append(json.loads(line))
        return items
