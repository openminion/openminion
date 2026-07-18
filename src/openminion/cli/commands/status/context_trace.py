from __future__ import annotations

from typing import Any

from openminion.cli.commands.status.session_store import build_status_session_store
from openminion.cli.presentation.json_output import print_json_payload
from openminion.modules.context.trace_inspection import (
    ContextTraceLookupError,
    list_context_traces,
)


def run_context_trace_status(args: Any, *, config: Any) -> int:
    session_id = str(getattr(args, "session_id", "") or "").strip()
    turn_id = str(getattr(args, "turn_id", "") or "").strip() or None
    limit = max(1, int(getattr(args, "limit", 50) or 50))
    store = build_status_session_store(args, config)
    try:
        payload = {
            "ok": True,
            **list_context_traces(
                store,
                session_id=session_id,
                turn_id=turn_id,
                limit=limit,
            ),
        }
    except ContextTraceLookupError as exc:
        payload = {
            "ok": False,
            "code": exc.code,
            "message": str(exc),
            "details": {"session_id": session_id, "turn_id": turn_id},
        }
        if getattr(args, "json", False):
            print_json_payload(payload)
        else:
            print(f"status context-trace: {exc.code} session={session_id}")
            print(f"- message: {exc}")
        return 1
    finally:
        store.close()

    if getattr(args, "json", False):
        print_json_payload(payload)
        return 0
    _print_context_trace_status(payload)
    return 0


def _print_context_trace_status(payload: dict[str, Any]) -> None:
    traces = list(payload.get("traces", []) or [])
    print(
        "status context-trace: "
        f"session={payload.get('session_id', '')} count={len(traces)}"
    )
    for item in traces:
        trace = dict(item.get("decision_trace", {}) or {})
        decisions = list(trace.get("decisions", []) or [])
        print(
            "- trace: "
            f"event_id={item.get('event_id', '') or '-'} "
            f"turn={trace.get('turn_id', '') or '-'} "
            f"pack={trace.get('pack_version', '') or '-'} "
            f"status={trace.get('persistence_status', '') or '-'} "
            f"decisions={len(decisions)} "
            f"truncated={trace.get('truncated', False)}"
        )
        for decision in decisions[:5]:
            print(
                "  - decision: "
                f"segment={decision.get('segment_id', '')} "
                f"bucket={decision.get('bucket', '')} "
                f"action={decision.get('action', '')} "
                f"reason={decision.get('reason_code', '')}"
            )


__all__ = ["run_context_trace_status"]
