from __future__ import annotations

import argparse
import json
from typing import Any, cast

from openminion.base.config.env import resolve_environment_config
from openminion.cli.config import load_cli_config, resolve_cli_roots
from openminion.modules.controlplane.config import (
    ControlPlaneConfig,
    from_base_config as controlplane_from_base_config,
    load_config as load_controlplane_config,
)
from openminion.modules.controlplane.constants import (
    PRINCIPAL_BINDING_STATUS_ACTIVE,
)
from openminion.modules.controlplane.pairing.admin import (
    ControlPlanePairingAdmin,
    open_pairing_admin,
)


def run_channel_pairings(args: argparse.Namespace) -> int:
    action = str(getattr(args, "pairings_command", "") or "").strip().lower()
    admin = _open_admin(getattr(args, "config", None))
    try:
        if action == "list":
            return _list_pairings(args, admin=admin)
        if action == "show":
            return _show_pairing(args, admin=admin)
        if action == "scopes":
            return _set_pairing_scopes(args, admin=admin)
        if action == "revoke":
            return _revoke_pairing(args, admin=admin)
    finally:
        admin.close()
    raise RuntimeError("unknown pairings command")


def register_pairings_subcommands(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    handler: Any,
) -> None:
    pairings = subcommands.add_parser(
        "pairings", help="Inspect and manage channel pairings"
    )
    pairings_subcommands = pairings.add_subparsers(
        dest="pairings_command", required=True
    )

    list_cmd = pairings_subcommands.add_parser("list", help="List paired channels")
    _add_pairings_config_arg(list_cmd)
    list_cmd.add_argument("--channel", default=None)
    list_cmd.add_argument(
        "--status",
        choices=("active", "inactive", "all"),
        default="active",
    )
    list_cmd.add_argument("--limit", type=int, default=100)
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(handler=handler, needs_app=False)

    show = pairings_subcommands.add_parser("show", help="Show one channel pairing")
    _add_pairings_config_arg(show)
    _add_subject_args(show)
    show.add_argument("--json", action="store_true")
    show.set_defaults(handler=handler, needs_app=False)

    scopes = pairings_subcommands.add_parser(
        "scopes", help="Update scopes for one channel pairing"
    )
    scopes_subcommands = scopes.add_subparsers(
        dest="pairings_scopes_command", required=True
    )
    scopes_set = scopes_subcommands.add_parser(
        "set", help="Replace scopes for one channel pairing"
    )
    _add_pairings_config_arg(scopes_set)
    _add_subject_args(scopes_set)
    scopes_set.add_argument("--scopes", required=True)
    scopes_set.add_argument("--yes", action="store_true")
    scopes_set.add_argument("--json", action="store_true")
    scopes_set.set_defaults(handler=handler, needs_app=False)

    revoke = pairings_subcommands.add_parser(
        "revoke", help="Revoke one channel pairing"
    )
    _add_pairings_config_arg(revoke)
    _add_subject_args(revoke)
    revoke.add_argument("--note", default="revoked by local operator")
    revoke.add_argument("--yes", action="store_true")
    revoke.add_argument("--json", action="store_true")
    revoke.set_defaults(handler=handler, needs_app=False)


def _add_pairings_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=None, help="Config file path")


def _add_subject_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--channel", required=True)
    parser.add_argument("--subject-id", required=True)


def _list_pairings(args: argparse.Namespace, *, admin: ControlPlanePairingAdmin) -> int:
    status = _status_filter(getattr(args, "status", "active"))
    pairings = admin.list_pairings(
        channel=_optional_text(getattr(args, "channel", None)),
        status=status,
        limit=max(1, int(getattr(args, "limit", 100))),
    )
    payload = {"pairings": pairings}
    if getattr(args, "json", False):
        _print_json(payload)
        return 0
    if not pairings:
        print("No channel pairings found.")
        return 0
    for row in payload["pairings"]:
        scopes = ", ".join(row["scopes"]) if row["scopes"] else "(none)"
        print(
            f"{row['channel']} {row['subject_id']} "
            f"status={row['status']} scopes={scopes}"
        )
    return 0


def _show_pairing(args: argparse.Namespace, *, admin: ControlPlanePairingAdmin) -> int:
    result = admin.show_pairing(channel=args.channel, subject_id=args.subject_id)
    if not result.found:
        return _not_found(args)
    payload = {"pairing": result.pairing}
    if getattr(args, "json", False):
        _print_json(payload)
    else:
        pairing = cast(dict[str, Any], payload["pairing"])
        print(f"channel: {pairing['channel']}")
        print(f"subject_id: {pairing['subject_id']}")
        print(f"principal_id: {pairing['principal_id']}")
        print(f"status: {pairing['status']}")
        print("scopes: " + (", ".join(pairing["scopes"]) or "(none)"))
        if pairing.get("note"):
            print(f"note: {pairing['note']}")
    return 0


def _set_pairing_scopes(
    args: argparse.Namespace, *, admin: ControlPlanePairingAdmin
) -> int:
    if not _confirmed(args):
        return _confirmation_required(args, action="change scopes")
    scopes = _parse_scope_csv(getattr(args, "scopes", None))
    if not scopes:
        print("Refusing to set an empty scope list.")
        return 2
    result = admin.set_scopes(
        channel=args.channel,
        subject_id=args.subject_id,
        scopes=scopes,
    )
    if not result.found:
        return _not_found(args)
    _print_result(args, "scopes updated", result.pairing)
    return 0


def _revoke_pairing(args: argparse.Namespace, *, admin: ControlPlanePairingAdmin) -> int:
    if not _confirmed(args):
        return _confirmation_required(args, action="revoke pairing")
    result = admin.revoke(
        channel=args.channel,
        subject_id=args.subject_id,
        note=getattr(args, "note", None),
    )
    if not result.found:
        return _not_found(args)
    _print_result(args, "pairing revoked", result.pairing)
    return 0


def _status_filter(raw: str) -> str | None:
    value = str(raw or "").strip().lower()
    if value == "all":
        return None
    return value or PRINCIPAL_BINDING_STATUS_ACTIVE


def _optional_text(value: object | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _parse_scope_csv(raw: str | None) -> list[str]:
    return [
        scope.strip()
        for chunk in str(raw or "").split(",")
        if (scope := chunk.strip())
    ]


def _confirmed(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "yes", False))


def _confirmation_required(args: argparse.Namespace, *, action: str) -> int:
    message = (
        f"Refusing to {action} without --yes. "
        "This changes live access for a paired channel subject."
    )
    if getattr(args, "json", False):
        _print_json({"ok": False, "error": "confirmation_required", "message": message})
    else:
        print(message)
    return 2


def _not_found(args: argparse.Namespace) -> int:
    message = (
        "No channel pairing found for "
        f"{getattr(args, 'channel', '')}:{getattr(args, 'subject_id', '')}."
    )
    if getattr(args, "json", False):
        _print_json({"ok": False, "error": "pairing_not_found", "message": message})
    else:
        print(message)
    return 1


def _print_result(
    args: argparse.Namespace,
    message: str,
    pairing: dict[str, Any] | None,
) -> None:
    if getattr(args, "json", False):
        _print_json({"ok": True, "message": message, "pairing": pairing})
        return
    print(message)


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _open_admin(config_path: str | None) -> ControlPlanePairingAdmin:
    cfg = _load_pairings_controlplane_config(config_path)
    return open_pairing_admin(cfg)


def _load_pairings_controlplane_config(config_path: str | None) -> ControlPlaneConfig:
    base = load_cli_config(config_path)
    if "controlplane" in dict(getattr(base, "channels", {}) or {}):
        roots = resolve_cli_roots(config_path=config_path)
        return controlplane_from_base_config(
            base_config=base,
            home_root=roots.home_root,
            data_root=roots.data_root,
        )
    return load_controlplane_config(config_path, env=_resolved_env_snapshot())


def _resolved_env_snapshot() -> dict[str, str]:
    snapshot = resolve_environment_config().snapshot()
    return {str(key): str(value) for key, value in snapshot.items()}
