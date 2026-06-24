from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from openminion.modules.a2a.artifacts import LocalArtifactStore
from openminion.modules.a2a.config import RuntimeConfig, load_config
from openminion.modules.a2a.constants import DEFAULT_CONFIG_FILENAME
from openminion.modules.a2a.errors import (
    A2AError,
    ERROR_CODE_INVALID_ARGUMENT,
    ERROR_CODE_INVALID_CONFIG,
)
from openminion.modules.a2a.models import Envelope, new_uuid
from openminion.modules.a2a.policy import PolicyEngine
from openminion.modules.a2a.runtime import A2ARuntime
from openminion.modules.a2a.storage import (
    MemoryAuditStore,
    MemoryStateStore,
    SQLiteAuditStore,
    SQLiteStateStore,
)
from openminion.modules.cli_common import (
    add_common_module_root_args,
    apply_home_data_root_env,
    print_json_payload,
)
from openminion.modules.storage.module_cli import (
    add_storage_subcommands,
    run_module_storage_command,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    home_root = str(getattr(args, "home_root", "") or "").strip()
    data_root = str(getattr(args, "data_root", "") or "").strip()
    apply_home_data_root_env(home_root=home_root, data_root=data_root)

    if args.cmd == "storage":
        cfg = load_config(Path(args.config))
        db_path = Path(cfg.storage.state.path).expanduser().resolve(strict=False)
        return run_module_storage_command(
            args=args,
            module_id="a2a",
            db_path=db_path,
            home_root=home_root,
            data_root=data_root,
        )

    runtime = _build_runtime(Path(args.config))
    try:
        _dispatch(runtime, args)
    except A2AError as exc:
        _print_json({"ok": False, "error": exc.to_dict()})
        raise SystemExit(1)
    finally:
        runtime.close()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="a2actl")
    add_common_module_root_args(parser)
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_FILENAME,
        help="Path to a2actl config file",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    agents = sub.add_parser("agents", help="Agent registry operations")
    agents_sub = agents.add_subparsers(dest="agents_cmd", required=True)
    agents_sub.add_parser("list", help="List registered agents")

    call = sub.add_parser("call", help="Execute synchronous call")
    call.add_argument("--from-agent", default="cli")
    call.add_argument("--to-agent", default="agent.echo")
    call.add_argument("--to-capability", default="")
    call.add_argument("--method", required=True)
    call.add_argument("--params", default="{}", help="JSON object")
    call.add_argument("--idempotency-key", default="")
    call.add_argument("--timeout-ms", type=int, default=30_000)

    job = sub.add_parser("job", help="Async job operations")
    job_sub = job.add_subparsers(dest="job_cmd", required=True)

    job_start = job_sub.add_parser("start", help="Start async job")
    job_start.add_argument("--from-agent", default="cli")
    job_start.add_argument("--to-agent", default="agent.worker")
    job_start.add_argument("--to-capability", default="")
    job_start.add_argument("--method", required=True)
    job_start.add_argument("--params", default="{}", help="JSON object")
    job_start.add_argument("--idempotency-key", default="")
    job_start.add_argument("--timeout-ms", type=int, default=30_000)

    job_status = job_sub.add_parser("status", help="Get job status")
    job_status.add_argument("task_id")

    job_cancel = job_sub.add_parser("cancel", help="Cancel job")
    job_cancel.add_argument("task_id")

    trace = sub.add_parser("trace", help="Query trace events")
    trace.add_argument("trace_id")
    trace.add_argument("--limit", type=int, default=200)

    errors = sub.add_parser("errors", help="Query recent error records")
    errors.add_argument("--since", default="1h", help="Window like 30s, 10m, 1h")
    errors.add_argument("--limit", type=int, default=200)

    add_storage_subcommands(sub)

    return parser


def _dispatch(runtime: A2ARuntime, args: argparse.Namespace) -> None:
    if args.cmd == "agents" and args.agents_cmd == "list":
        _print_json({"agents": runtime.list_agents()})
        return

    if args.cmd == "call":
        _print_json(runtime.call(_request_envelope(args, "call")).to_dict())
        return

    if args.cmd == "job" and args.job_cmd == "start":
        _print_json(
            {
                "ok": True,
                "task_id": runtime.job_start(_request_envelope(args, "job.start")),
            }
        )
        return

    if args.cmd == "job" and args.job_cmd == "status":
        row = runtime.job_status(str(args.task_id))
        _print_json({"ok": True, "job": row.to_dict()})
        return

    if args.cmd == "job" and args.job_cmd == "cancel":
        row = runtime.job_cancel(str(args.task_id))
        _print_json({"ok": True, "job": row.to_dict()})
        return

    if args.cmd == "trace":
        rows = runtime.query_trace(str(args.trace_id), limit=int(args.limit))
        _print_json({"trace_id": args.trace_id, "events": rows})
        return

    if args.cmd == "errors":
        rows = runtime.query_errors(
            since_seconds=_parse_since(str(args.since)), limit=int(args.limit)
        )
        _print_json({"since": args.since, "errors": rows})
        return

    raise A2AError(ERROR_CODE_INVALID_ARGUMENT, "Unsupported command")


def _build_runtime(config_path: Path) -> A2ARuntime:
    cfg: RuntimeConfig = load_config(config_path)
    policy = PolicyEngine.from_config(cfg.policy.default_action, cfg.policy.rules)
    artifacts = LocalArtifactStore(cfg.artifacts.root)

    runtime = A2ARuntime(
        state_store=_build_state_store(cfg),
        audit_store=_build_audit_store(cfg),
        artifact_store=artifacts,
        policy_engine=policy,
        max_inline_bytes=cfg.artifacts.max_inline_bytes,
        recovery_stale_heartbeat_sec=cfg.recovery.stale_heartbeat_sec,
    )
    _register_builtin_agents(runtime)
    return runtime


def _register_builtin_agents(runtime: A2ARuntime) -> None:
    def echo_handler(envelope: Envelope) -> dict[str, Any]:
        return {
            "agent": "agent.echo",
            "method": envelope.method,
            "params": envelope.params,
            "trace_id": envelope.trace_id,
        }

    def worker_handler(envelope: Envelope) -> dict[str, Any]:
        params = envelope.params
        seconds = float(params.get("seconds", 0)) if isinstance(params, dict) else 0.0
        if seconds > 0:
            time.sleep(min(seconds, 30.0))
        return {
            "agent": "agent.worker",
            "method": envelope.method,
            "slept_seconds": seconds,
            "params": envelope.params,
        }

    runtime.register_agent(
        "agent.echo", ["echo.", "debug."], echo_handler, tags=["builtin", "debug"]
    )
    runtime.register_agent(
        "agent.worker",
        ["job.", "sleep.", "task."],
        worker_handler,
        tags=["builtin", "worker"],
    )


def _request_envelope(args: argparse.Namespace, message_type: str) -> Envelope:
    return Envelope.new(
        from_agent=str(args.from_agent),
        to_agent=_optional(args.to_agent),
        to_capability=_optional(args.to_capability),
        type=message_type,
        method=str(args.method),
        params=_parse_json_object(args.params),
        timeout_ms=int(args.timeout_ms),
        idempotency_key=(args.idempotency_key or new_uuid()),
    )


def _build_state_store(cfg: RuntimeConfig) -> MemoryStateStore | SQLiteStateStore:
    state_backend = cfg.storage.state.backend.lower()
    if state_backend == "memory":
        return MemoryStateStore()
    if state_backend == "sqlite":
        return SQLiteStateStore(cfg.storage.state.path)
    raise A2AError(
        ERROR_CODE_INVALID_CONFIG,
        f"Unsupported state backend: {cfg.storage.state.backend}",
    )


def _build_audit_store(cfg: RuntimeConfig) -> MemoryAuditStore | SQLiteAuditStore:
    audit_backend = cfg.storage.audit.backend.lower()
    if audit_backend in {"memory", "inmemory"}:
        return MemoryAuditStore()
    if audit_backend in {"sqlite_rotated", "sqlite"}:
        return SQLiteAuditStore(
            cfg.storage.audit.root, retention_days=cfg.storage.audit.retention_days
        )
    raise A2AError(
        ERROR_CODE_INVALID_CONFIG,
        f"Unsupported audit backend: {cfg.storage.audit.backend}",
    )


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip() or "{}"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise A2AError(
            ERROR_CODE_INVALID_ARGUMENT, f"Invalid JSON params: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise A2AError(
            ERROR_CODE_INVALID_ARGUMENT, "--params must decode to a JSON object"
        )
    return parsed


def _optional(value: str) -> str | None:
    text = (value or "").strip()
    return text if text else None


def _parse_since(value: str) -> int:
    raw = value.strip().lower()
    if raw.endswith("ms"):
        num = float(raw[:-2])
        return int(num / 1000.0)
    if raw.endswith("s"):
        return int(float(raw[:-1]))
    if raw.endswith("m"):
        return int(float(raw[:-1]) * 60)
    if raw.endswith("h"):
        return int(float(raw[:-1]) * 3600)
    if raw.endswith("d"):
        return int(float(raw[:-1]) * 86_400)
    return int(float(raw))


def _print_json(payload: dict[str, Any]) -> None:
    print_json_payload(payload, sort_keys=False, ensure_ascii=True)


if __name__ == "__main__":
    raise SystemExit(main())
