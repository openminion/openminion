from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.artifact.config import load_config
from openminion.modules.artifact.constants import DEFAULT_CONFIG_FILENAME
from openminion.modules.artifact.errors import ArtifactCtlError
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
        cfg = load_config(args.config)
        db_path = Path(cfg.index.sqlite_path).expanduser().resolve(strict=False)
        return run_module_storage_command(
            args=args,
            module_id="artifact",
            db_path=db_path,
            home_root=home_root,
            data_root=data_root,
        )

    ctl = ArtifactCtl(args.config)
    try:
        _dispatch(ctl, args)
    except ArtifactCtlError as exc:
        _print_json({"ok": False, "error": exc.to_dict()})
        raise SystemExit(1)
    finally:
        ctl.close()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="artifactctl")
    add_common_module_root_args(parser)
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_FILENAME,
        help="Path to artifactctl config",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    ingest = sub.add_parser("ingest", help="Ingest a file")
    ingest.add_argument("path")
    ingest.add_argument("--mime", default=None)
    ingest.add_argument("--label", default=None)
    ingest.add_argument("--meta", default="{}", help="JSON object")
    ingest.add_argument("--session", default=None)
    ingest.add_argument("--trace", default=None)
    ingest.add_argument("--agent", default=None)

    show = sub.add_parser("show", help="Show artifact metadata or digest")
    show.add_argument("target")
    show.add_argument("--digest", action="store_true")

    view = sub.add_parser("view", help="Show a view")
    view.add_argument("target")
    view.add_argument("--type", required=True, choices=["text", "json", "table"])

    search = sub.add_parser("search", help="Search artifacts")
    search.add_argument("query")
    search.add_argument("--session", default=None)
    search.add_argument("--trace", default=None)
    search.add_argument("--agent", default=None)
    search.add_argument("--mime", default=None)
    search.add_argument("--limit", type=int, default=100)

    largest = sub.add_parser("largest", help="Largest artifacts")
    largest.add_argument("--top", type=int, default=50)

    alias = sub.add_parser("alias", help="Alias operations")
    alias_sub = alias.add_subparsers(dest="alias_cmd", required=True)

    alias_set = alias_sub.add_parser("set", help="Set alias")
    alias_set.add_argument("alias")
    alias_set.add_argument("target")

    alias_resolve = alias_sub.add_parser("resolve", help="Resolve alias")
    alias_resolve.add_argument("alias")

    alias_list = alias_sub.add_parser("list", help="List aliases")
    alias_list.add_argument("--prefix", default=None)

    alias_delete = alias_sub.add_parser("delete", help="Delete alias")
    alias_delete.add_argument("alias")

    gc = sub.add_parser("gc", help="Run mark-and-sweep soft-delete")
    gc.add_argument("--plan", action="store_true")

    purge = sub.add_parser(
        "purge", help="Physically remove deleted files after grace period"
    )
    purge.add_argument("--grace", default=None, help="e.g. 7d, 12h, 3600s")

    verify = sub.add_parser("verify", help="Verify blob integrity")
    verify.add_argument("target", nargs="?", default=None)
    verify.add_argument("--all", action="store_true")

    refs = sub.add_parser("refs", help="Reference edge operations")
    refs_sub = refs.add_subparsers(dest="refs_cmd", required=True)

    ref_add = refs_sub.add_parser("add", help="Add reference edge")
    ref_add.add_argument(
        "owner_type", choices=["session", "memory", "alias", "collection", "a2a"]
    )
    ref_add.add_argument("owner_id")
    ref_add.add_argument("target")

    ref_remove = refs_sub.add_parser("remove", help="Remove reference edge")
    ref_remove.add_argument(
        "owner_type", choices=["session", "memory", "alias", "collection", "a2a"]
    )
    ref_remove.add_argument("owner_id")
    ref_remove.add_argument("target")

    add_storage_subcommands(sub)

    return parser


def _dispatch(ctl: ArtifactCtl, args: argparse.Namespace) -> None:
    if args.cmd == "ingest":
        ref = ctl.ingest_file(
            args.path,
            mime=args.mime,
            label=args.label,
            meta=_parse_json_obj(args.meta),
            session_id=args.session,
            trace_id=args.trace,
            agent_id=args.agent,
        )
        _print_json({"ok": True, "artifact": ref.to_dict()})
        return

    if args.cmd == "show":
        if args.digest:
            _print_json({"ok": True, "digest": ctl.read_digest(args.target)})
            return
        meta = ctl.get(args.target)
        _print_json({"ok": True, "artifact": meta.to_dict()})
        return

    if args.cmd == "view":
        data = ctl.read_view(args.target, args.type)
        _print_json({"ok": True, "view_type": args.type, "data": data})
        return

    if args.cmd == "search":
        filters = {
            "session_id": args.session,
            "trace_id": args.trace,
            "agent_id": args.agent,
            "mime": args.mime,
        }
        rows = ctl.search(args.query, filters=filters)
        rows = rows[: max(1, int(args.limit))]
        _print_json({"ok": True, "results": [row.to_dict() for row in rows]})
        return

    if args.cmd == "largest":
        rows = ctl.largest(limit=int(args.top))
        _print_json({"ok": True, "results": [row.to_dict() for row in rows]})
        return

    if args.cmd == "alias":
        _dispatch_alias(ctl, args)
        return

    if args.cmd == "gc":
        gc_report = ctl.gc(plan_only=bool(args.plan))
        _print_json({"ok": True, "report": gc_report.to_dict()})
        return

    if args.cmd == "purge":
        grace_days = _parse_duration_to_days(args.grace) if args.grace else None
        purge_report = ctl.purge(grace_days=grace_days)
        _print_json({"ok": True, "report": purge_report.to_dict()})
        return

    if args.cmd == "verify":
        target = "all" if args.all or args.target is None else args.target
        verify_report = ctl.verify(target)
        _print_json({"ok": True, "report": verify_report.to_dict()})
        return

    if args.cmd == "refs" and args.refs_cmd == "add":
        ctl.ref_add(args.owner_type, args.owner_id, args.target)
        _print_json({"ok": True})
        return

    if args.cmd == "refs" and args.refs_cmd == "remove":
        ctl.ref_remove(args.owner_type, args.owner_id, args.target)
        _print_json({"ok": True})
        return

    raise ArtifactCtlError("INVALID_ARGUMENT", "Unsupported command")


def _dispatch_alias(ctl: ArtifactCtl, args: argparse.Namespace) -> None:
    if args.alias_cmd == "set":
        ctl.alias_set(args.alias, args.target)
        _print_json(
            {"ok": True, "alias": args.alias, **_resolved_payload(ctl, args.alias)}
        )
        return
    if args.alias_cmd == "resolve":
        _print_json({"ok": True, **_resolved_payload(ctl, args.alias)})
        return
    if args.alias_cmd == "list":
        _print_json({"ok": True, "aliases": ctl.alias_list(prefix=args.prefix)})
        return
    if args.alias_cmd == "delete":
        ctl.alias_delete(args.alias)
        _print_json({"ok": True})


def _parse_json_obj(raw: str) -> dict[str, Any]:
    text = (raw or "").strip() or "{}"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ArtifactCtlError(
            "INVALID_ARGUMENT", f"Invalid JSON object: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ArtifactCtlError("INVALID_ARGUMENT", "Expected JSON object")
    return parsed


def _parse_duration_to_days(raw: str) -> int:
    text = raw.strip().lower()
    if text.endswith("d"):
        return int(float(text[:-1]))
    if text.endswith("h"):
        hours = float(text[:-1])
        return max(0, int(hours / 24.0))
    if text.endswith("m"):
        minutes = float(text[:-1])
        return max(0, int(minutes / (24.0 * 60.0)))
    if text.endswith("s"):
        seconds = float(text[:-1])
        return max(0, int(seconds / 86400.0))
    return int(float(text))


def _print_json(payload: dict[str, Any]) -> None:
    print_json_payload(payload, sort_keys=False, ensure_ascii=True)


def _resolved_payload(ctl: ArtifactCtl, alias: str) -> dict[str, Any]:
    resolved = ctl.alias_resolve(alias)
    return {"resolved": None if resolved is None else resolved.to_dict()}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
