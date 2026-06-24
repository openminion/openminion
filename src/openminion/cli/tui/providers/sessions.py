from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from urllib.parse import unquote

from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION


def _agent_id_from_session_key(session_key: str) -> str:
    for part in (session_key or "").split("|"):
        if part.startswith("agent:"):
            return unquote(part[len("agent:") :])
    return ""


class RuntimeSessionsProvider:
    contract_version: str = CLI_INTERFACE_VERSION

    def __init__(
        self,
        session_store: Any | None,
        *,
        session_limit: int = 200,
        timeline_limit: int = 200,
    ) -> None:
        self._session_store = session_store
        self._session_limit = max(1, int(session_limit))
        self._timeline_limit = max(1, int(timeline_limit))

    def list_all_sessions(self) -> list[dict[str, Any]]:
        if self._session_store is None:
            return []
        list_sessions = getattr(self._session_store, "list_sessions", None)
        count_messages = getattr(self._session_store, "count_messages", None)
        list_participants = getattr(self._session_store, "list_participants", None)
        if not callable(list_sessions):
            return []

        try:
            sessions = list_sessions(limit=self._session_limit)
        except Exception:
            return []

        result: list[dict[str, Any]] = []
        for session in sessions:
            session_id = str(self._value(session, "id") or "").strip()
            if not session_id:
                continue
            turn_count = 0
            if callable(count_messages):
                try:
                    turn_count = int(count_messages(session_id=session_id))
                except (TypeError, ValueError):
                    turn_count = 0

            updated_at = str(
                self._value(session, "updated_at")
                or self._value(session, "created_at")
                or ""
            )
            session_key = str(self._value(session, "session_key") or "")
            channel = str(self._value(session, "channel") or "")
            active_agent_id = str(self._value(session, "active_agent_id") or "")
            metadata = self._value(session, "metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            participants: list[dict[str, str]] = []
            if callable(list_participants):
                try:
                    participants = [
                        {
                            "participant_type": str(
                                self._value(item, "participant_type") or ""
                            ),
                            "participant_id": str(
                                self._value(item, "participant_id") or ""
                            ),
                            "role": str(self._value(item, "role") or "participant"),
                            "channel": str(self._value(item, "channel") or ""),
                        }
                        for item in list_participants(session_id)
                    ]
                except Exception:
                    participants = []
            result.append(
                {
                    "id": session_id,
                    "age": self._format_age(updated_at),
                    "turn_count": max(0, turn_count),
                    "agent_id": active_agent_id
                    or _agent_id_from_session_key(session_key),
                    "channel": channel,
                    "name": str(metadata.get("name", "")),
                    "participants": participants,
                    "participant_count": len(participants),
                }
            )
        return result

    def get_session_timeline(self, session_id: str) -> list[dict[str, Any]]:
        if self._session_store is None:
            return []

        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return []

        timeline = self._timeline_from_events(normalized_session_id)
        if timeline:
            return timeline
        return self._timeline_from_messages(normalized_session_id)

    def close_session(self, session_id: str) -> None:
        if self._session_store is None:
            return
        close = getattr(self._session_store, "close_session", None)
        if callable(close):
            try:
                close(session_id=session_id, reason="user-closed")
            except Exception:
                pass

    def delete_session(self, session_id: str) -> None:
        if self._session_store is None:
            return
        delete = getattr(self._session_store, "delete_session", None)
        if callable(delete):
            try:
                delete(session_id)
            except Exception:
                pass

    def update_session_name(self, session_id: str, name: str) -> None:
        if self._session_store is None:
            return
        update = getattr(self._session_store, "update_session_metadata", None)
        if callable(update):
            try:
                update(session_id=session_id, patch={"name": name})
            except Exception:
                pass

    def _timeline_from_events(self, session_id: str) -> list[dict[str, Any]]:
        list_events = getattr(self._session_store, "list_events", None)
        if not callable(list_events):
            return []
        try:
            events = list_events(
                session_id=session_id,
                limit=self._timeline_limit,
                newest_first=False,
            )
        except Exception:
            return []
        if not isinstance(events, list):
            return []

        timeline: list[dict[str, Any]] = []
        for event in events:
            event_type = str(
                self._value(event, "event_type")
                or self._value(event, "type")
                or "event"
            )
            created_at = str(self._value(event, "created_at") or "")
            payload = self._value(event, "payload", {})
            if not isinstance(payload, dict):
                payload = {}
            timeline.append(
                {
                    "ts": self._time_hhmm(created_at),
                    "event_type": event_type,
                    "detail": self._event_detail(payload),
                }
            )
        return timeline

    def _timeline_from_messages(self, session_id: str) -> list[dict[str, Any]]:
        list_messages = getattr(self._session_store, "list_messages", None)
        if not callable(list_messages):
            return []
        try:
            messages = list_messages(session_id=session_id, limit=self._timeline_limit)
        except Exception:
            return []
        if not isinstance(messages, list):
            return []

        timeline: list[dict[str, Any]] = []
        for message in messages:
            created_at = str(self._value(message, "created_at") or "")
            role = str(self._value(message, "role") or "message")
            body = str(self._value(message, "body") or "")
            timeline.append(
                {
                    "ts": self._time_hhmm(created_at),
                    "event_type": f"message.{role}",
                    "detail": body[:72],
                }
            )
        return timeline

    @staticmethod
    def _event_detail(payload: dict[str, Any]) -> str:
        if not payload:
            return ""
        for key in (
            "summary",
            "detail",
            "mode_label",
            "mode_state",
            "tool",
            "step",
            "reason",
            "run_id",
        ):
            value = payload.get(key)
            text = str(value or "").strip()
            if text:
                return text
        items = [f"{key}={payload[key]}" for key in sorted(payload)[:2]]
        return " ".join(items)

    @staticmethod
    def _value(obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @staticmethod
    def _time_hhmm(raw_iso: str) -> str:
        text = str(raw_iso or "").strip()
        if len(text) >= 16:
            return text[11:16]
        return ""

    @staticmethod
    def _format_age(raw_iso: str) -> str:
        value = str(raw_iso or "").strip()
        if not value:
            return "—"
        try:
            updated_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return "—"

        now = datetime.now(timezone.utc)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)

        delta = max(0, int((now - updated_at).total_seconds()))
        if delta < 3600:
            minutes = max(1, delta // 60)
            return f"{minutes}m"
        if delta < 86400:
            return f"{delta // 3600}h"
        return f"{delta // 86400}d"
