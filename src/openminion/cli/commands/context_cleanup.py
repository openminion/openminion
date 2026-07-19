import argparse
import json
import sys
from pathlib import Path

from openminion.base.logging import configure_logging
from openminion.modules.session.diagnostics.cleanup import SessionCleanupUtility
from openminion.services.config import resolve_services_path, resolve_services_roots
from openminion.services.bootstrap.paths import (
    SERVICES_STATE_DB_FILENAME,
    SERVICES_STATE_DIRNAME,
)

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
