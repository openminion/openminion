import argparse
import json
import os
from pathlib import Path
from typing import Any

from openminion.modules.cli_common import (
    add_common_module_root_args,
    apply_home_data_root_env,
    print_json_payload,
)
from openminion.modules.config import (
    is_module_standalone_mode,
    resolve_module_data_root,
    resolve_module_home_root,
)
from .constants import (
    DEFAULT_INTEGRATED_SQLITE_SUBPATH,
    DEFAULT_STANDALONE_SQLITE_SUBPATH,
    POLICY_DURATION_CHOICES,
    POLICY_DURATION_FOREVER,
    POLICY_DURATION_ONCE,
    POLICY_GRANT_EFFECT_CHOICES,
    POLICY_MODE_CHOICES,
    POLICY_MODE_DISABLED,
    POLICY_SUBJECT_ID_LOCAL,
)
from .models import PolicyConfig, PolicyGrantInput, stable_invocation_hash
from .runtime.service import PolicyCtl
from openminion.modules.storage.module_cli import (
    add_storage_subcommands,
    run_module_storage_command,
)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    home_root, data_root = _apply_policy_cli_roots(args)
    db_path = _resolve_policy_db_path(args)
    if args.cmd == "storage":
        return run_module_storage_command(
            args=args,
            module_id="policy",
            db_path=Path(db_path).expanduser().resolve(),
            home_root=home_root,
            data_root=data_root,
        )
    ctl = PolicyCtl.with_sqlite(db_path, config=PolicyConfig(mode=args.mode))
    try:
        return _dispatch_policy_command(args=args, ctl=ctl)
    finally:
        ctl.close()


def _apply_policy_cli_roots(args: argparse.Namespace) -> tuple[str, str]:
    home_root = str(getattr(args, "home_root", "") or "").strip()
    data_root = str(getattr(args, "data_root", "") or "").strip()
    apply_home_data_root_env(home_root=home_root, data_root=data_root)
    return home_root, data_root


def _resolve_policy_db_path(args: argparse.Namespace) -> str:
    db_path = str(getattr(args, "db", "") or "").strip()
    if db_path:
        return db_path
    env_map = os.environ
    if is_module_standalone_mode(env_map):
        return str((Path.home() / DEFAULT_STANDALONE_SQLITE_SUBPATH).resolve())
    resolved_home_root = resolve_module_home_root(
        None,
        env_map,
        fallback_to_cwd=True,
    )
    resolved_data_root = resolve_module_data_root(
        home_root=resolved_home_root,
        env=env_map,
    )
    return str((resolved_data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH).resolve())


def _dispatch_policy_command(*, args: argparse.Namespace, ctl: PolicyCtl) -> int:
    if args.cmd == "set-mode":
        ctl.set_mode(args.value)
        _print_json({"ok": True, "mode": ctl.mode()})
        return 0
    if args.cmd == "mode":
        _print_json({"ok": True, "mode": ctl.mode()})
        return 0
    if args.cmd == "list-grants":
        return _handle_list_grants(args=args, ctl=ctl)
    if args.cmd == "revoke":
        updated = ctl.revoke_grant(args.grant_id)
        _print_json({"ok": updated, "grant_id": args.grant_id})
        return 0
    if args.cmd == "cleanup":
        cleaned = ctl.cleanup_expired()
        _print_json({"ok": True, "cleaned": cleaned})
        return 0
    if args.cmd == "create-grant":
        return _handle_create_grant(args=args, ctl=ctl)
    if args.cmd == "check":
        payload = _parse_json_object(args.invocation_json)
        ctx = _parse_json_object(args.context_json) if args.context_json else {}
        decision = ctl.check(payload, ctx)
        _print_json({"ok": True, "decision": decision.to_dict()})
        return 0
    if args.cmd == "list-decisions":
        rows = ctl.list_decisions(limit=args.limit)
        _print_json({"ok": True, "decisions": rows})
    return 0


def _handle_list_grants(*, args: argparse.Namespace, ctl: PolicyCtl) -> int:
    grants = ctl.list_grants(
        subject_id=args.subject_id,
        effect=args.effect,
        tool=args.tool,
        method=args.method,
        active_only=args.active_only,
    )
    _print_json({"ok": True, "grants": [g.__dict__ for g in grants]})
    return 0


def _handle_create_grant(*, args: argparse.Namespace, ctl: PolicyCtl) -> int:
    invocation_hash = args.invocation_hash
    if args.duration_type == POLICY_DURATION_ONCE and not invocation_hash:
        invocation_hash = stable_invocation_hash(
            tool=args.tool,
            method=args.method,
            args=_parse_json_object(args.args_json) if args.args_json else {},
        )
    grant_id = ctl.create_grant(
        PolicyGrantInput(
            effect=args.effect,
            subject_id=args.subject_id,
            tool=args.tool,
            method=args.method,
            target_json=_parse_json_object(args.target_json),
            duration_type=args.duration_type,
            expires_at=args.expires_at,
            session_id=args.session_id,
            invocation_hash=invocation_hash,
            max_uses=args.max_uses,
            reason=args.reason,
        )
    )
    _print_json({"ok": True, "grant_id": grant_id})
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="policyctl", description="openminion-policy CLI"
    )
    add_common_module_root_args(parser)
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite database path (defaults under OpenMinion Home when set)",
    )
    parser.add_argument("--mode", default=POLICY_MODE_DISABLED)

    sub = parser.add_subparsers(dest="cmd", required=True)

    set_mode = sub.add_parser("set-mode", help="Persist policy mode")
    set_mode.add_argument("value", choices=list(POLICY_MODE_CHOICES))

    sub.add_parser("mode", help="Show effective mode")

    list_grants = sub.add_parser("list-grants", help="List grants")
    list_grants.add_argument("--subject-id", default=None)
    list_grants.add_argument(
        "--effect", default=None, choices=list(POLICY_GRANT_EFFECT_CHOICES)
    )
    list_grants.add_argument("--tool", default=None)
    list_grants.add_argument("--method", default=None)
    list_grants.add_argument("--active-only", action="store_true")

    revoke = sub.add_parser("revoke", help="Revoke grant")
    revoke.add_argument("--grant-id", required=True)

    sub.add_parser("cleanup", help="Revoke expired grants")

    create = sub.add_parser("create-grant", help="Create grant")
    create.add_argument(
        "--effect", required=True, choices=list(POLICY_GRANT_EFFECT_CHOICES)
    )
    create.add_argument("--subject-id", default=POLICY_SUBJECT_ID_LOCAL)
    create.add_argument("--tool", default="*")
    create.add_argument("--method", default="*")
    create.add_argument(
        "--duration-type",
        default=POLICY_DURATION_FOREVER,
        choices=list(POLICY_DURATION_CHOICES),
    )
    create.add_argument("--expires-at", default=None)
    create.add_argument("--session-id", default=None)
    create.add_argument("--invocation-hash", default=None)
    create.add_argument("--max-uses", type=int, default=None)
    create.add_argument("--target-json", default="{}")
    create.add_argument(
        "--args-json",
        default=None,
        help="Only used to derive invocation hash for once grants",
    )
    create.add_argument("--reason", default=None)

    check = sub.add_parser("check", help="Evaluate invocation")
    check.add_argument("--invocation-json", required=True)
    check.add_argument("--context-json", default=None)

    list_decisions = sub.add_parser("list-decisions", help="List decision logs")
    list_decisions.add_argument("--limit", type=int, default=100)
    add_storage_subcommands(sub)
    return parser


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON payload must be an object")
    return dict(payload)


def _print_json(payload: dict[str, Any]) -> None:
    print_json_payload(payload, sort_keys=False, ensure_ascii=True)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
