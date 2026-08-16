from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from pathlib import PurePosixPath

from openminion.base.time import utc_now

from .contracts import CommandPlan, OperationTarget

_SHELL_OPERATORS = frozenset({"|", "||", "&&", ";", "<", ">", ">>", "2>", "&"})
_SHELL_EXECUTABLES = frozenset({"bash", "dash", "fish", "sh", "zsh"})
_PRIVILEGED_EXECUTABLES = frozenset({"doas", "su", "sudo"})
_DANGEROUS_EXECUTABLES = frozenset(
    {"dd", "halt", "mkfs", "poweroff", "reboot", "rm", "shutdown"}
)


def build_command_plan(
    *,
    target: OperationTarget,
    argv: tuple[str, ...],
    cwd: str,
    timeout_seconds: float,
    session_id: str,
    idempotency_key: str,
    ttl_seconds: int,
    policy_outcome: str,
) -> CommandPlan:
    now = utc_now()
    values = {
        "plan_id": f"opplan-{uuid.uuid4().hex}",
        "target_id": target.target_id,
        "target_revision": target.revision,
        "argv": _normalize_argv(argv),
        "cwd": _normalize_cwd(cwd, target.workspace_scopes),
        "timeout_seconds": min(timeout_seconds, target.timeout_seconds),
        "session_id": session_id,
        "idempotency_key": idempotency_key,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        "policy_outcome": policy_outcome,
    }
    return CommandPlan.model_validate({"plan_hash": _plan_hash(values), **values})


def validate_command_plan(plan: CommandPlan, *, supplied_hash: str) -> None:
    if plan.plan_hash != supplied_hash or plan.plan_hash != _stored_hash(plan):
        raise ValueError("command plan hash changed")
    if datetime.fromisoformat(plan.expires_at) <= utc_now():
        raise ValueError("command plan expired")


def _normalize_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(argv, (str, bytes)):
        raise TypeError("command argv must be a structured sequence")
    if not argv:
        raise ValueError("command argv cannot be empty")
    normalized = tuple(str(token) for token in argv)
    if any(not token or "\0" in token or "\n" in token for token in normalized):
        raise ValueError("command argv contains an invalid token")
    if any(
        token in _SHELL_OPERATORS or "$(" in token or "`" in token
        for token in normalized
    ):
        raise ValueError("shell operators are not allowed")
    executable = PurePosixPath(normalized[0]).name
    if executable in _SHELL_EXECUTABLES:
        raise ValueError("shell executables are not allowed")
    if executable in _PRIVILEGED_EXECUTABLES:
        raise PermissionError("privileged commands are not allowed")
    if executable in _DANGEROUS_EXECUTABLES:
        raise PermissionError("dangerous commands are not allowed")
    return normalized


def _normalize_cwd(cwd: str, scopes: tuple[str, ...]) -> str:
    token = str(cwd or "").strip()
    if not token:
        return ""
    path = PurePosixPath(token)
    if not path.is_absolute() or not scopes or ".." in path.parts:
        raise ValueError("command cwd must be inside a configured workspace scope")
    if not any(
        path == PurePosixPath(scope) or PurePosixPath(scope) in path.parents
        for scope in scopes
    ):
        raise ValueError("command cwd is outside configured workspace scopes")
    return str(path)


def _plan_hash(values: Mapping[str, object]) -> str:
    payload = json.dumps(values, sort_keys=True, separators=(",", ":"), default=list)
    return hashlib.sha256(payload.encode()).hexdigest()


def _stored_hash(plan: CommandPlan) -> str:
    return _plan_hash(plan.model_dump(exclude={"plan_hash"}, mode="json"))


class CommandPlanStore:
    """Durable immutable command plans keyed by their public plan id."""

    def __init__(self, path: Path | str = ":memory:") -> None:
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS operation_plans "
            "(plan_id TEXT PRIMARY KEY, plan_json TEXT NOT NULL)"
        )
        self._connection.commit()

    def put(self, plan: CommandPlan) -> CommandPlan:
        with self._lock:
            self._connection.execute(
                "INSERT INTO operation_plans VALUES (?, ?)",
                (plan.plan_id, plan.model_dump_json()),
            )
            self._connection.commit()
        return plan

    def get(self, plan_id: str) -> CommandPlan:
        row = self._connection.execute(
            "SELECT plan_json FROM operation_plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown command plan: {plan_id}")
        return CommandPlan.model_validate_json(str(row[0]))

    def list(self) -> tuple[CommandPlan, ...]:
        rows = self._connection.execute(
            "SELECT plan_json FROM operation_plans ORDER BY plan_id"
        ).fetchall()
        return tuple(CommandPlan.model_validate_json(str(row[0])) for row in rows)

    def close(self) -> None:
        with self._lock:
            self._connection.close()
