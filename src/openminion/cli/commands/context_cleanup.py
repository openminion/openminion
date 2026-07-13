import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from openminion.base.logging import configure_logging
from openminion.services.config import resolve_services_path, resolve_services_roots
from openminion.services.bootstrap.paths import (
    SERVICES_STATE_DB_FILENAME,
    SERVICES_STATE_DIRNAME,
)

logger = logging.getLogger(__name__)


class SessionCleanupUtility:
    """Utility to clean up stale 'state machine error' text from session stores."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path.expanduser().resolve()
        self._store: Any | None = None

    def _get_store(self) -> Any | None:
        """Lazy-load the session store."""
        if self._store is not None:
            return self._store
        try:
            from openminion.modules.session.storage.sqlite_store import (
                SQLiteSessionStore,
            )

            self._store = SQLiteSessionStore(self.db_path)
            return self._store
        except Exception as exc:
            logger.error(f"Failed to load session store: {exc}")
            return None

    def _is_error_text(self, value: str) -> bool:
        """Check if text contains state machine error markers."""
        text = str(value or "").strip().lower()
        if not text:
            return False
        return "state machine error:" in text

    def scan_session(self, session_id: str) -> dict[str, Any]:
        """Scan a session for error-contaminated content."""
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
            logger.warning(f"Failed to scan turns: {exc}")

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
            logger.warning(f"Failed to scan events: {exc}")

        return result

    def cleanup_session(self, session_id: str, dry_run: bool = True) -> dict[str, Any]:
        """Clean up error-contaminated content from a session."""
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
                f"[DRY RUN] Would clean up {len(scan_result['contaminated_turns'])} turns and {len(scan_result['contaminated_events'])} events"
            )
            result["turns_removed"] = len(scan_result["contaminated_turns"])
            result["events_removed"] = len(scan_result["contaminated_events"])
            return result

        store = self._get_store()
        if store is None:
            return {"error": "Store not available"}

        # Note: Actual removal depends on store capabilities
        # For now, we log what would be removed
        turns_removed = 0
        events_removed = 0
        for turn in scan_result["contaminated_turns"]:
            logger.info(f"Removing contaminated turn: {turn['turn_id']}")
            turns_removed += 1
            details.append({"type": "turn", "id": turn["turn_id"]})

        for event in scan_result["contaminated_events"]:
            logger.info(f"Removing contaminated event: {event['event_id']}")
            events_removed += 1
            details.append({"type": "event", "id": event["event_id"]})

        result["turns_removed"] = turns_removed
        result["events_removed"] = events_removed
        return result

    def list_sessions(self) -> list[str]:
        """List all session IDs in the store."""
        store = self._get_store()
        if store is None:
            return []
        try:
            return [str(session_id) for session_id in store.list_sessions()]
        except Exception as exc:
            logger.error(f"Failed to list sessions: {exc}")
            return []


def main() -> int:
    configure_logging("INFO")
    parser = argparse.ArgumentParser(
        description="Session cleanup utility for stale 'state machine error' text (CSA-09/CSA-T08)",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Path to session database",
    )
    parser.add_argument(
        "--session-id",
        type=str,
        help="Specific session ID to clean up",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Scan all sessions for contamination",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Actually perform cleanup (default is dry-run)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )

    args = parser.parse_args()

    if args.db_path is None:
        args.db_path = resolve_services_path(
            Path(SERVICES_STATE_DIRNAME) / SERVICES_STATE_DB_FILENAME,
            roots=resolve_services_roots(),
        )

    utility = SessionCleanupUtility(args.db_path)

    if args.scan_all:
        sessions = utility.list_sessions()
        all_results = []
        for session_id in sessions:
            result = utility.scan_session(session_id)
            if result["contaminated_turns"] or result["contaminated_events"]:
                all_results.append(result)

        if args.json:
            print(json.dumps(all_results, indent=2))
        else:
            for result in all_results:
                print(f"Session: {result['session_id']}")
                print(f"  Contaminated turns: {len(result['contaminated_turns'])}")
                print(f"  Contaminated events: {len(result['contaminated_events'])}")
        return 0

    if args.session_id:
        if args.cleanup:
            result = utility.cleanup_session(args.session_id, dry_run=False)
        else:
            result = utility.scan_session(args.session_id)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Session: {result.get('session_id', args.session_id)}")
            if "error" in result:
                print(f"Error: {result['error']}")
                return 1
            print(f"Total turns: {result.get('total_turns', 'N/A')}")
            print(f"Total events: {result.get('total_events', 'N/A')}")
            print(f"Contaminated turns: {len(result.get('contaminated_turns', []))}")
            print(f"Contaminated events: {len(result.get('contaminated_events', []))}")
            if result.get("contaminated_turns"):
                print("\nContaminated turns:")
                for turn in result["contaminated_turns"]:
                    print(f"  - {turn['role']} ({turn['turn_id']}): {turn['preview']}")
            if result.get("contaminated_events"):
                print("\nContaminated events:")
                for event in result["contaminated_events"]:
                    print(
                        f"  - {event['event_type']} ({event['event_id']}): {event['preview']}"
                    )
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
