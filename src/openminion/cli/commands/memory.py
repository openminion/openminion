from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

from openminion.base.time import utc_now_iso
from typing import Any, Callable
from uuid import uuid4

from openminion.cli.presentation.json_output import print_json_payload


_DEFAULT_MIN_TOKEN_ESTIMATE = 1


@lru_cache(maxsize=1)
def _memory_sdk() -> dict[str, Any]:
    from sophiagraph import SophiaGraphSqliteStore
    from sophiagraph.contracts.errors import (
        MemctlError,
        MemoryBlockClassNotEligibleError,
        MemoryBlockEditDeniedError,
        MemoryBlockModeNotYetSupportedError,
        NotFoundError,
    )
    from sophiagraph.models import (
        MEMORY_BLOCK_V1_CLASS_ALLOWLIST,
        MemoryBlock,
        MemoryNamespace,
        validate_block_for_creation,
    )

    return {
        "SophiaGraphSqliteStore": SophiaGraphSqliteStore,
        "MemctlError": MemctlError,
        "MemoryBlock": MemoryBlock,
        "MemoryBlockClassNotEligibleError": MemoryBlockClassNotEligibleError,
        "MemoryBlockEditDeniedError": MemoryBlockEditDeniedError,
        "MemoryBlockModeNotYetSupportedError": MemoryBlockModeNotYetSupportedError,
        "MemoryNamespace": MemoryNamespace,
        "NotFoundError": NotFoundError,
        "MEMORY_BLOCK_V1_CLASS_ALLOWLIST": MEMORY_BLOCK_V1_CLASS_ALLOWLIST,
        "validate_block_for_creation": validate_block_for_creation,
    }


def _estimate_tokens(content: str) -> int:
    words = len(content.split())
    return max(_DEFAULT_MIN_TOKEN_ESTIMATE, words)


def _resolve_default_mode(class_name: str) -> str:
    if class_name == "agent_identity":
        return "read_only"
    return "pinned"


def _parse_namespace_flags(args: argparse.Namespace) -> Any:
    """Construct a typed namespace from the CLI flags.

    At least one identifier must be supplied. The CLI never infers a
    namespace from prose — operators must name the dimensions explicitly.
    """
    kwargs: dict[str, str] = {}
    for kind in (
        "tenant_id",
        "org_id",
        "user_id",
        "agent_id",
        "session_id",
        "conversation_id",
        "project_id",
        "graph_id",
    ):
        value = getattr(args, kind, None)
        if value:
            kwargs[kind] = str(value)
    if not kwargs:
        raise SystemExit(
            "memory blocks: at least one namespace flag is required "
            "(e.g. --agent-id, --session-id)"
        )
    return _memory_sdk()["MemoryNamespace"](**kwargs)


StoreFactory = Callable[[argparse.Namespace], Any]


def _default_store_factory(args: argparse.Namespace) -> Any:
    db_path = getattr(args, "sqlite", None)
    if not db_path:
        raise SystemExit(
            "memory blocks: --sqlite <path> is required when no factory override is set"
        )
    return _memory_sdk()["SophiaGraphSqliteStore"](Path(db_path).expanduser().resolve())


_store_factory: StoreFactory = _default_store_factory


def set_store_factory(factory: StoreFactory) -> None:
    global _store_factory
    _store_factory = factory


def reset_store_factory() -> None:
    global _store_factory
    _store_factory = _default_store_factory


def _render_block(block: Any) -> dict[str, Any]:
    payload = asdict(block)
    namespace = block.owner_namespace
    if isinstance(namespace, _memory_sdk()["MemoryNamespace"]):
        payload["owner_namespace"] = namespace.as_dict()
    payload["provenance"] = dict(block.provenance)
    return payload


def run_memory_blocks_list(args: argparse.Namespace) -> int:
    store = _store_factory(args)
    namespaces: list[Any] | None = None
    if any(
        getattr(args, kind, None)
        for kind in (
            "tenant_id",
            "org_id",
            "user_id",
            "agent_id",
            "session_id",
            "conversation_id",
            "project_id",
            "graph_id",
        )
    ):
        namespaces = [_parse_namespace_flags(args)]
    blocks = store.list_memory_blocks(namespaces=namespaces)
    payload = {
        "ok": True,
        "count": len(blocks),
        "blocks": [_render_block(b) for b in blocks],
    }
    print_json_payload(payload)
    return 0


def run_memory_blocks_pin(args: argparse.Namespace) -> int:
    sdk = _memory_sdk()
    class_name = args.class_name
    content = args.content
    if class_name not in sdk["MEMORY_BLOCK_V1_CLASS_ALLOWLIST"]:
        print_json_payload(
            {
                "ok": False,
                "code": "MEMORY_BLOCK_CLASS_NOT_ELIGIBLE",
                "class_name": class_name,
                "eligible": sorted(sdk["MEMORY_BLOCK_V1_CLASS_ALLOWLIST"]),
            },
            stream=sys.stderr,
        )
        return 2
    mode = args.mode or _resolve_default_mode(class_name)
    namespace = _parse_namespace_flags(args)
    now = utc_now_iso()
    block_id = args.block_id or f"blk-{uuid4()}"
    block = sdk["MemoryBlock"](
        block_id=block_id,
        class_name=class_name,
        mode=mode,
        content=content,
        token_estimate=(args.token_estimate or _estimate_tokens(content)),
        owner_namespace=namespace,
        source=args.source or "operator_pin",
        created_at=now,
        last_updated_at=now,
        last_updated_by=args.actor or "operator",
        stale_after=args.stale_after,
    )
    try:
        sdk["validate_block_for_creation"](block)
    except (
        sdk["MemoryBlockClassNotEligibleError"],
        sdk["MemoryBlockModeNotYetSupportedError"],
    ) as exc:
        print_json_payload({"ok": False, **exc.to_dict()}, stream=sys.stderr)
        return 2
    store = _store_factory(args)
    store.put_memory_block(block)
    print_json_payload({"ok": True, "block": _render_block(block)})
    return 0


def run_memory_blocks_update(args: argparse.Namespace) -> int:
    sdk = _memory_sdk()
    store = _store_factory(args)
    try:
        block = store.update_memory_block_content(
            args.block_id,
            new_content=args.content,
            actor=args.actor or "operator",
            operator_action=True,
        )
    except sdk["MemoryBlockEditDeniedError"] as exc:
        print_json_payload({"ok": False, **exc.to_dict()}, stream=sys.stderr)
        return 2
    except sdk["NotFoundError"] as exc:
        print_json_payload({"ok": False, **exc.to_dict()}, stream=sys.stderr)
        return 1
    print_json_payload({"ok": True, "block": _render_block(block)})
    return 0


def run_memory_blocks_unpin(args: argparse.Namespace) -> int:
    sdk = _memory_sdk()
    store = _store_factory(args)
    try:
        removed = store.delete_memory_block(
            args.block_id,
            actor=args.actor or "operator",
            operator_action=True,
        )
    except sdk["MemoryBlockEditDeniedError"] as exc:
        print_json_payload({"ok": False, **exc.to_dict()}, stream=sys.stderr)
        return 2
    payload = {"ok": True, "removed": bool(removed), "block_id": args.block_id}
    print_json_payload(payload)
    return 0


def run_memory_cli_bridge(args: argparse.Namespace) -> int:
    if args.memory_command != "blocks":
        print(f"memory: unknown subcommand {args.memory_command!r}", file=sys.stderr)
        return 2
    handler_map = {
        "list": run_memory_blocks_list,
        "pin": run_memory_blocks_pin,
        "update": run_memory_blocks_update,
        "unpin": run_memory_blocks_unpin,
    }
    handler = handler_map.get(args.blocks_command)
    if handler is None:
        print(
            f"memory blocks: unknown subcommand {args.blocks_command!r}",
            file=sys.stderr,
        )
        return 2
    try:
        return int(handler(args))
    except _memory_sdk()["MemctlError"] as exc:
        print_json_payload({"ok": False, **exc.to_dict()}, stream=sys.stderr)
        return 1


def _add_namespace_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tenant-id", dest="tenant_id", default=None)
    parser.add_argument("--org-id", dest="org_id", default=None)
    parser.add_argument("--user-id", dest="user_id", default=None)
    parser.add_argument("--agent-id", dest="agent_id", default=None)
    parser.add_argument("--session-id", dest="session_id", default=None)
    parser.add_argument("--conversation-id", dest="conversation_id", default=None)
    parser.add_argument("--project-id", dest="project_id", default=None)
    parser.add_argument("--graph-id", dest="graph_id", default=None)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    memory = subparsers.add_parser(
        "memory",
        help="Memory operator surfaces (v1: blocks)",
        description=(
            "OpenMinion memory operator surfaces backed by the sophiagraph package "
            "via direct library import."
        ),
    )
    memory_subparsers = memory.add_subparsers(dest="memory_command", required=True)

    blocks = memory_subparsers.add_parser(
        "blocks",
        help="Manage v1 memory blocks (list / pin / update / unpin)",
    )
    blocks.add_argument(
        "--sqlite",
        default=None,
        help="Path to the sophiagraph SQLite database",
    )
    blocks_subparsers = blocks.add_subparsers(dest="blocks_command", required=True)

    blocks_list = blocks_subparsers.add_parser(
        "list",
        help="List memory blocks (optionally filtered by namespace)",
    )
    _add_namespace_flags(blocks_list)
    blocks_list.set_defaults(handler=run_memory_cli_bridge, needs_app=False)

    blocks_pin = blocks_subparsers.add_parser(
        "pin",
        help="Create or seed a memory block (agent_identity / active_mission / session_pin)",
    )
    blocks_pin.add_argument(
        "class_name", help="One of: agent_identity, active_mission, session_pin"
    )
    blocks_pin.add_argument("content", help="Block prose content (operator-authored)")
    blocks_pin.add_argument("--block-id", default=None, dest="block_id")
    blocks_pin.add_argument(
        "--mode",
        default=None,
        help=(
            "Override the default v1 mode. Allowed: read_only, pinned. "
            "Deferred modes (shared, writable) are rejected."
        ),
    )
    blocks_pin.add_argument(
        "--token-estimate", type=int, default=None, dest="token_estimate"
    )
    blocks_pin.add_argument("--source", default=None)
    blocks_pin.add_argument("--actor", default=None)
    blocks_pin.add_argument("--stale-after", default=None, dest="stale_after")
    _add_namespace_flags(blocks_pin)
    blocks_pin.set_defaults(handler=run_memory_cli_bridge, needs_app=False)

    blocks_update = blocks_subparsers.add_parser(
        "update",
        help="Operator-authored update of a pinned block's content",
    )
    blocks_update.add_argument("block_id", help="Block ID to update")
    blocks_update.add_argument("content", help="New operator-authored content")
    blocks_update.add_argument("--actor", default=None)
    _add_namespace_flags(blocks_update)
    blocks_update.set_defaults(handler=run_memory_cli_bridge, needs_app=False)

    blocks_unpin = blocks_subparsers.add_parser(
        "unpin",
        help="Operator-authored delete of a pinned block",
    )
    blocks_unpin.add_argument("block_id", help="Block ID to remove")
    blocks_unpin.add_argument("--actor", default=None)
    _add_namespace_flags(blocks_unpin)
    blocks_unpin.set_defaults(handler=run_memory_cli_bridge, needs_app=False)


__all__ = [
    "register",
    "reset_store_factory",
    "run_memory_blocks_list",
    "run_memory_blocks_pin",
    "run_memory_blocks_unpin",
    "run_memory_blocks_update",
    "run_memory_cli_bridge",
    "set_store_factory",
]
