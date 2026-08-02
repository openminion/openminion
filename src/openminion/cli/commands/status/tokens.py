from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from openminion.base.config import OpenMinionConfig
from openminion.cli.commands.status.session_store import build_status_session_store
from openminion.cli.commands.status.token_report import (
    format_token_rollup,
    format_token_summary,
    prepare_token_rollup,
    token_rollup_json_payload,
)
from openminion.cli.presentation.json_output import print_json_payload
from openminion.modules.telemetry.usage import (
    StatsService,
    summary_to_json_payload,
)


def _session_id_from_row(row: Any) -> str:
    if isinstance(row, Mapping):
        return str(row.get("session_id") or row.get("id") or "").strip()
    return str(getattr(row, "session_id", None) or getattr(row, "id", "") or "").strip()


def _resolve_session_id(args: Any, store: Any) -> str:
    session_id = str(getattr(args, "session_id", "") or "").strip()
    if session_id:
        return session_id
    list_sessions = getattr(store, "list_sessions", None)
    if not callable(list_sessions):
        raise RuntimeError("--session-id is required for this session store.")
    sessions = list_sessions(limit=1)
    if not sessions:
        raise RuntimeError(
            "No sessions found. Start a session or run `openminion sessions list` "
            "to confirm the active data root."
        )
    latest_session_id = _session_id_from_row(sessions[0])
    if not latest_session_id:
        raise RuntimeError("--session-id is required; latest session has no id.")
    return latest_session_id


def _validate_token_status_args(
    args: Any,
) -> tuple[str, str, int | None, int | None, bool]:
    run_id = str(args.run_id or "").strip()
    requested_session_id = str(getattr(args, "session_id", "") or "").strip()
    recent_limit = getattr(args, "recent", None)
    event_limit = args.event_limit
    only_warnings = bool(getattr(args, "only_warnings", False))
    if event_limit is not None and int(event_limit) <= 0:
        raise RuntimeError("--event-limit must be greater than zero")
    normalized_recent_limit = None if recent_limit is None else int(recent_limit)
    if normalized_recent_limit is not None and normalized_recent_limit <= 0:
        raise RuntimeError("--recent must be greater than zero")
    if normalized_recent_limit is not None and (requested_session_id or run_id):
        raise RuntimeError("--recent cannot be combined with --session-id or --run-id")
    if only_warnings and normalized_recent_limit is None:
        raise RuntimeError("--only-warnings requires --recent")
    return (
        run_id,
        requested_session_id,
        normalized_recent_limit,
        event_limit,
        only_warnings,
    )


def run_tokens_status(args: Any, *, config: OpenMinionConfig) -> int:
    run_id, requested_session_id, recent_limit, event_limit, only_warnings = (
        _validate_token_status_args(args)
    )

    store = build_status_session_store(args, config)
    try:
        service = StatsService(store)
        if recent_limit is not None:
            summaries = service.get_recent_session_token_usage(
                limit=recent_limit,
                event_limit=event_limit,
            )
            visible_summaries = prepare_token_rollup(
                summaries,
                only_warnings=only_warnings,
            )
            if bool(args.json):
                print_json_payload(
                    token_rollup_json_payload(
                        visible_summaries,
                        input_session_count=len(summaries),
                        only_warnings=only_warnings,
                    )
                )
            else:
                print(
                    format_token_rollup(
                        visible_summaries,
                        input_session_count=len(summaries),
                        only_warnings=only_warnings,
                    )
                )
            return 0
        if run_id and not requested_session_id:
            summary = service.get_run_token_usage(run_id, event_limit=event_limit)
            if summary is None:
                raise RuntimeError(f"Run '{run_id}' was not found.")
            if store.get_session(summary.session_id) is None:
                raise RuntimeError(f"Session '{summary.session_id}' was not found.")
        else:
            session_id = _resolve_session_id(args, store)
            if store.get_session(session_id) is None:
                raise RuntimeError(f"Session '{session_id}' was not found.")
            if not run_id:
                summary = service.get_session_token_usage(
                    session_id,
                    event_limit=event_limit,
                )
            else:
                summary = service.get_run_token_usage(run_id, event_limit=event_limit)
                if summary is None:
                    raise RuntimeError(f"Run '{run_id}' was not found.")
                if summary.session_id != session_id:
                    raise RuntimeError(
                        f"Run '{run_id}' does not belong to session '{session_id}'."
                    )
        cost = (
            service.get_run_turn_cost(run_id, event_limit=event_limit)
            if run_id
            else None
        )
        if bool(args.json):
            print_json_payload(summary_to_json_payload(summary))
        else:
            print(format_token_summary(summary, cost=cost))
        return 0
    finally:
        store.close()


__all__ = ["run_tokens_status"]
