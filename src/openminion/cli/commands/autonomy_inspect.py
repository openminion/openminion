from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from openminion.cli.presentation.json_output import print_json_payload
from openminion.modules.task.autonomy import (
    AutonomyRun,
    AutonomyRunStatus,
    AutonomyRunStore,
)


def list_autonomy_runs(args: argparse.Namespace, store: AutonomyRunStore) -> int:
    status = _status_arg(getattr(args, "status", None))
    runs = store.list_runs(status=status, limit=int(getattr(args, "limit", 50)))
    payload = {
        "ok": True,
        "runs": [_run_summary(run) for run in runs],
        "count": len(runs),
    }
    if bool(getattr(args, "json", False)):
        print_json_payload(payload)
        return 0
    if not runs:
        print("No autonomy runs.")
        return 0
    for run in runs:
        print(
            f"{run.run_id} {run.status.value} phase={run.phase.value} "
            f"goal={run.goal_text[:80]}"
        )
    return 0


def show_autonomy_run(args: argparse.Namespace, store: AutonomyRunStore) -> int:
    run = store.require(str(args.run_id))
    proof_payload = (
        _load_proof_payload(run)
        if bool(getattr(args, "include_proof", False))
        else None
    )
    payload = {"ok": True, "run": run.model_dump(mode="json")}
    if proof_payload is not None:
        payload["proof"] = proof_payload
    if bool(getattr(args, "json", False)):
        print_json_payload(payload)
        return 0
    print(f"run_id: {run.run_id}")
    print(f"status: {run.status.value}")
    print(f"phase: {run.phase.value}")
    print(f"goal: {run.goal_text}")
    print(f"workspace_ref: {run.workspace_ref or '-'}")
    print(f"proof_packet_ref: {run.proof_packet_ref or '-'}")
    if proof_payload is not None:
        print(f"proof_status: {proof_payload.get('status', '-')}")
        print(f"proof_validation: {proof_payload.get('validation_summary', '-')}")
    if run.next_action_hint:
        print(f"next_action: {run.next_action_hint}")
    return 0


def _run_summary(run: AutonomyRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "goal_id": run.goal_id,
        "goal_text": run.goal_text,
        "session_id": run.session_id,
        "status": run.status.value,
        "phase": run.phase.value,
        "workspace_ref": run.workspace_ref,
        "proof_packet_ref": run.proof_packet_ref,
        "created_at_ms": run.created_at_ms,
        "updated_at_ms": run.updated_at_ms,
    }


def _load_proof_payload(run: AutonomyRun) -> dict[str, Any] | None:
    if not run.proof_packet_ref:
        return None
    path = Path(run.proof_packet_ref).expanduser().resolve(strict=False)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _status_arg(value: object) -> AutonomyRunStatus | None:
    raw = str(value or "").strip()
    return AutonomyRunStatus(raw) if raw else None


__all__ = ("list_autonomy_runs", "show_autonomy_run")
