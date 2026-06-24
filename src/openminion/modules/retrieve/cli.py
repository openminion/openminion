from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from .constants import DEFAULT_CONFIG_FILENAME
from .config import load_config, resolve_default_config_path
from .runtime.retrieve import RetrieveCtl
from openminion.modules.cli_common import (
    add_common_module_root_args,
    apply_home_data_root_env,
    print_json_payload,
)
from openminion.modules.storage.module_cli import (
    add_storage_subcommands,
    run_module_storage_command,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="retrievectl", description="openminion-retrieve local-first CLI"
    )
    add_common_module_root_args(parser)
    parser.add_argument(
        "--config",
        default=None,
        help=f"Path to retrievectl config (default: generated configs/{DEFAULT_CONFIG_FILENAME})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show retrieval storage status")

    ingest = sub.add_parser("ingest-text", help="Ingest raw text into retrieval index")
    ingest.add_argument(
        "--source-type",
        required=True,
        choices=["episode", "artifact", "skill", "mem", "doc"],
    )
    ingest.add_argument("--source-ref", required=True)
    ingest.add_argument("--text", required=True)
    ingest.add_argument(
        "--scope", default="project", choices=["session", "agent", "global", "project"]
    )
    ingest.add_argument("--tags", default="")
    ingest.add_argument("--title", default="")
    ingest.add_argument("--corpus-id", default="")
    ingest.add_argument(
        "--unit-kind", default="chunk", choices=["chunk", "doc_group", "document"]
    )

    retrieve = sub.add_parser("retrieve", help="Run retrieval")
    retrieve.add_argument("--query", required=True)
    retrieve.add_argument(
        "--purpose",
        default="act",
        choices=["plan", "act", "verify", "summarize", "decide"],
    )
    retrieve.add_argument("--k", type=int, default=8)
    retrieve.add_argument(
        "--strategy",
        default="auto",
        choices=["auto", "contextual", "raptor", "longrag_doc_group"],
    )
    retrieve.add_argument("--scope", default="")
    retrieve.add_argument("--tags", default="")
    retrieve.add_argument("--types", default="")

    build = sub.add_parser("build-raptor", help="Build RAPTOR tree for a doc")
    build.add_argument("--doc-id", required=True)

    group = sub.add_parser("group-long", help="Build long doc-group units for corpus")
    group.add_argument("--corpus-id", required=True)
    group.add_argument("--min-tokens", type=int, default=2000)
    group.add_argument("--max-tokens", type=int, default=8000)

    expand = sub.add_parser("expand", help="Expand retrieval reference")
    expand.add_argument("--ref", required=True)
    expand.add_argument("--mode", default="window")
    expand.add_argument("--k", type=int, default=5)

    explain = sub.add_parser("explain", help="Explain a retrieval item/ref")
    explain.add_argument("--ref", required=True)

    add_storage_subcommands(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    home_root = str(getattr(args, "home_root", "") or "").strip()
    data_root = str(getattr(args, "data_root", "") or "").strip()
    apply_home_data_root_env(home_root=home_root, data_root=data_root)

    cfg_path = getattr(args, "config", None)
    config_path = (
        Path(cfg_path).expanduser().resolve()
        if cfg_path
        else resolve_default_config_path()
    )

    if args.command == "storage":
        cfg = load_config(config_path, env=dict(os.environ))
        db_path = Path(cfg.storage.sqlite_path).expanduser().resolve(strict=False)
        return run_module_storage_command(
            args=args,
            module_id="retrieve",
            db_path=db_path,
            home_root=home_root,
            data_root=data_root,
        )

    service = RetrieveCtl(config=config_path)
    try:
        if args.command == "status":
            print_json_payload(service.status())
            return 0

        if args.command == "ingest-text":
            tags = [item.strip() for item in str(args.tags).split(",") if item.strip()]
            result = service.ingest_source(
                source_type=args.source_type,
                source_ref=args.source_ref,
                text=args.text,
                scope=args.scope,
                tags=tags,
                title=args.title,
                corpus_id=args.corpus_id or None,
                unit_kind=args.unit_kind,
            )
            print_json_payload(result.model_dump(mode="json"))
            return 0

        if args.command == "retrieve":
            tags = [item.strip() for item in str(args.tags).split(",") if item.strip()]
            types = [
                item.strip() for item in str(args.types).split(",") if item.strip()
            ]
            scope: dict[str, Any] = {}
            if args.scope.strip():
                for item in args.scope.split(","):
                    key = item.strip().lower()
                    if key in {"session", "agent", "global", "project"}:
                        scope[key] = True

            rows = service.retrieve(
                query=args.query,
                purpose=args.purpose,
                scope=scope,
                k=args.k,
                strategy=args.strategy,
                filters={"tags": tags, "types": types},
            )
            print_json_payload(rows)
            return 0

        if args.command == "build-raptor":
            result = service.build_raptor_tree(args.doc_id)
            print_json_payload(result)
            return 0

        if args.command == "group-long":
            result = service.group_long_units(
                args.corpus_id,
                {"min_tokens": args.min_tokens, "max_tokens": args.max_tokens},
            )
            print_json_payload(result)
            return 0

        if args.command == "expand":
            rows = service.expand(ref=args.ref, mode=args.mode, k=args.k)
            print_json_payload(rows)
            return 0

        if args.command == "explain":
            payload = service.explain(args.ref)
            print_json_payload(payload)
            return 0

        parser.error(f"unsupported command: {args.command}")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
