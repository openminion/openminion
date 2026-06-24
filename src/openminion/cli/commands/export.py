from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openminion.cli.bootstrap.loader import load_config
from openminion.modules.storage.runtime.context import build_runtime_storage
from openminion.modules.storage.runtime.session_store import SessionStore


def run_export(args) -> int:
    config_path = args.config
    config = load_config(config_path)
    storage_path = str(config.storage.path)

    session_id = str(getattr(args, "session_id", "")).strip()
    if not session_id:
        print("Error: --session-id is required.")
        return 1

    out_format = str(getattr(args, "format", "jsonl")).strip().lower()
    if out_format not in ("jsonl", "md"):
        print(f"Error: Unknown format '{out_format}'.")
        return 1

    out_path_str = str(getattr(args, "output", "")).strip()
    out_path = Path(out_path_str) if out_path_str else None

    include_events = bool(getattr(args, "include_events", False))

    records: list[dict[str, Any]] = []
    runtime_storage = build_runtime_storage(storage_path)
    try:
        sessions: SessionStore = runtime_storage.sessions
        session = sessions.get_session(session_id)
        if session is None:
            print(f"Error: Session '{session_id}' not found.")
            return 1

        unbounded_limit = 2_147_483_647
        for message in sessions.list_messages(
            session_id=session_id, limit=unbounded_limit
        ):
            records.append(
                {
                    "type": "message",
                    "id": str(message.id),
                    "role": str(message.role),
                    "body": str(message.body),
                    "metadata": dict(message.metadata),
                    "created_at": str(message.created_at),
                }
            )

        if include_events:
            for event in sessions.list_events(
                session_id=session_id,
                newest_first=False,
                limit=unbounded_limit,
            ):
                records.append(
                    {
                        "type": "event",
                        "id": str(event.id),
                        "event_type": str(event.event_type),
                        "payload": dict(event.payload),
                        "created_at": str(event.created_at),
                    }
                )
    finally:
        runtime_storage.close()

    records.sort(key=lambda x: (x["created_at"], x.get("id", "")))

    if out_format == "jsonl":
        output = "\n".join(json.dumps(record, sort_keys=True) for record in records)
    else:
        output = _export_md(session_id, records)

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"Exported {len(records)} records to {out_path}")
    else:
        print(output)

    return 0


def _export_md(session_id: str, records: list[dict[str, Any]]) -> str:
    lines = [
        f"# Session Transcript: {session_id}",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "---",
        "",
    ]

    for item in records:
        if item["type"] == "message":
            role = item["role"].capitalize()
            lines.append(f"### {role}")
            lines.append(f"_{item['created_at']}_")
            lines.append("")
            lines.append(item["body"])
            if item.get("metadata"):
                meta_str = json.dumps(item["metadata"], sort_keys=True)
                lines.append(f"\n*Metadata*: `{meta_str}`")
            lines.append("\n---")
        elif item["type"] == "event":
            lines.append(f"### Event: {item['event_type']}")
            lines.append(f"_{item['created_at']}_")
            lines.append("")
            payload_str = json.dumps(item["payload"], indent=2, sort_keys=True)
            lines.append("```json\n" + payload_str + "\n```")
            lines.append("\n---")

    return "\n".join(lines).strip()


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    export = subparsers.add_parser("export", help="Export commands")
    export_subcommands = export.add_subparsers(dest="export_command", required=True)

    export_transcript = export_subcommands.add_parser(
        "transcript", help="Export session transcript"
    )
    export_transcript.add_argument(
        "--session-id", required=True, help="Session identifier"
    )
    export_transcript.add_argument(
        "--format", choices=["jsonl", "md"], default="jsonl", help="Export format"
    )
    export_transcript.add_argument(
        "--output", default="", help="Output file path (default: stdout)"
    )
    export_transcript.add_argument(
        "--include-events", action="store_true", help="Include session events"
    )
    export_transcript.set_defaults(handler=run_export, needs_app=False)
