"""Session cleanup diagnostics for stale runtime text."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SessionCleanupUtility:
    """Scan and dry-run cleanup for stale state-machine error session text."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path.expanduser().resolve()
        self._store: Any | None = None

    def _get_store(self) -> Any | None:
        if self._store is not None:
            return self._store
        try:
            from openminion.modules.session.storage.sqlite_store import (
                SQLiteSessionStore,
            )

            self._store = SQLiteSessionStore(self.db_path)
            return self._store
        except Exception as exc:
            logger.error("Failed to load session store: %s", exc)
            return None

    def _is_error_text(self, value: str) -> bool:
        text = str(value or "").strip().lower()
        return bool(text and "state machine error:" in text)

    def scan_session(self, session_id: str) -> dict[str, Any]:
        store = self._get_store()
        if store is None:
            return {"error": "Store not available"}

        contaminated_turns: list[dict[str, Any]] = []
        contaminated_events: list[dict[str, Any]] = []
        result = {
            "session_id": session_id,
            "contaminated_turns": contaminated_turns,
            "contaminated_events": contaminated_events,
            "total_turns": 0,
            "total_events": 0,
        }

        try:
            turns = store.list_turns(session_id)
            result["total_turns"] = len(turns)
            for idx, turn in enumerate(turns):
                if isinstance(turn, dict):
                    content = str(
                        turn.get("content", turn.get("text", "")) or ""
                    ).strip()
                    if self._is_error_text(content):
                        contaminated_turns.append(
                            {
                                "index": idx,
                                "turn_id": turn.get(
                                    "turn_id", turn.get("id", f"turn-{idx}")
                                ),
                                "role": turn.get(
                                    "role", turn.get("turn_type", "unknown")
                                ),
                                "preview": content[:100] + "..."
                                if len(content) > 100
                                else content,
                            }
                        )
        except Exception as exc:
            logger.warning("Failed to scan turns: %s", exc)

        try:
            events = store.list_events(session_id)
            result["total_events"] = len(events)
            for idx, event in enumerate(events):
                if isinstance(event, dict):
                    content = str(
                        event.get("content", event.get("text", "")) or ""
                    ).strip()
                    if self._is_error_text(content):
                        contaminated_events.append(
                            {
                                "index": idx,
                                "event_id": event.get(
                                    "event_id", event.get("id", f"event-{idx}")
                                ),
                                "event_type": event.get("event_type", "unknown"),
                                "preview": content[:100] + "..."
                                if len(content) > 100
                                else content,
                            }
                        )
        except Exception as exc:
            logger.warning("Failed to scan events: %s", exc)

        return result

    def cleanup_session(self, session_id: str, dry_run: bool = True) -> dict[str, Any]:
        scan_result = self.scan_session(session_id)
        if scan_result.get("error"):
            return scan_result

        details: list[dict[str, Any]] = []
        result = {
            "session_id": session_id,
            "dry_run": dry_run,
            "turns_removed": 0,
            "events_removed": 0,
            "details": details,
        }

        if dry_run:
            logger.info(
                "[DRY RUN] Would clean up %s turns and %s events",
                len(scan_result["contaminated_turns"]),
                len(scan_result["contaminated_events"]),
            )
            result["turns_removed"] = len(scan_result["contaminated_turns"])
            result["events_removed"] = len(scan_result["contaminated_events"])
            return result

        store = self._get_store()
        if store is None:
            return {"error": "Store not available"}

        turns_removed = 0
        events_removed = 0
        for turn in scan_result["contaminated_turns"]:
            logger.info("Removing contaminated turn: %s", turn["turn_id"])
            turns_removed += 1
            details.append({"type": "turn", "id": turn["turn_id"]})

        for event in scan_result["contaminated_events"]:
            logger.info("Removing contaminated event: %s", event["event_id"])
            events_removed += 1
            details.append({"type": "event", "id": event["event_id"]})

        result["turns_removed"] = turns_removed
        result["events_removed"] = events_removed
        return result

    def list_sessions(self) -> list[str]:
        store = self._get_store()
        if store is None:
            return []
        try:
            return [str(session_id) for session_id in store.list_sessions()]
        except Exception as exc:
            logger.error("Failed to list sessions: %s", exc)
            return []
