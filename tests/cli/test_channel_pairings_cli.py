from __future__ import annotations

import json
from pathlib import Path

from openminion.cli.parser.base import build_parser
from openminion.modules.controlplane.contracts.models import InboundMessage, ParsedCommand
from openminion.modules.controlplane.runtime.security import ScopeAuthorizer
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore


def _write_profile(tmp_path: Path) -> Path:
    config_path = tmp_path / "agent.json"
    config_path.write_text(
        json.dumps(
            {
                "enabled_channels": ["console", "telegram", "slack"],
                "channels": {
                    "controlplane": {
                        "sqlite_path": str(
                            tmp_path / ".openminion" / "controlplane" / "cp.db"
                        ),
                        "openminion_enabled": False,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return config_path


def _open_store(config_path: Path) -> SQLiteControlPlaneStore:
    from openminion.cli.commands.channel_pairings import (
        _load_pairings_controlplane_config,
    )

    cfg = _load_pairings_controlplane_config(str(config_path))
    return SQLiteControlPlaneStore(cfg.sqlite_path, wal=cfg.wal)


def _seed_binding(
    config_path: Path,
    *,
    channel: str = "telegram",
    subject_id: str = "7105273251",
    scopes: list[str] | None = None,
) -> str:
    store = _open_store(config_path)
    try:
        selected_scopes = scopes or [
            "cp.message.read",
            "cp.message.write",
            "session.read",
            "session.write",
            "run.start",
        ]
        principal_id = store.upsert_pairing(
            channel=channel,
            chat_id=subject_id,
            user_id=subject_id,
            session_id="sess-test",
            scopes=selected_scopes,
            note="seeded for cli test",
            pairing_id="principal-telegram-1",
        )
        store.bind_principal_subject(
            principal_id=principal_id,
            channel=channel,
            subject_id=subject_id,
            scopes=selected_scopes,
            note="seeded for cli test",
            meta={
                "token": "must-not-leak",
                "token_hash": "must-not-leak",
                "token_hash_prefix": "must-not-leak",
            },
        )
    finally:
        store.close()
    return principal_id


def _run_cli(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


def _json_stdout(capsys) -> dict:
    captured = capsys.readouterr()
    return json.loads(captured.out)


def _as_text(payload: object) -> str:
    return json.dumps(payload, sort_keys=True)


def _auth_for(config_path: Path, *, channel: str, subject_id: str):
    store = _open_store(config_path)
    try:
        inbound = InboundMessage(
            channel=channel,
            chat_id=subject_id,
            user_id=subject_id,
            user_key=f"{channel}:user:{subject_id}",
            chat_key=f"{channel}:chat:{subject_id}",
            text="/help",
        )
        return ScopeAuthorizer(store=store).auth_for_inbound(inbound)
    finally:
        store.close()


def test_channel_pairings_subcommands_registered() -> None:
    parser = build_parser()
    args = parser.parse_args(["channel", "pairings", "list", "--json"])

    assert args.command == "channel"
    assert args.channel_name == "pairings"
    assert args.pairings_command == "list"
    assert args.json is True


def test_pairings_list_and_show_json_are_stable_and_redacted(
    tmp_path: Path,
    capsys,
) -> None:
    config_path = _write_profile(tmp_path)
    principal_id = _seed_binding(config_path)

    rc = _run_cli(
        [
            "channel",
            "pairings",
            "list",
            "--config",
            str(config_path),
            "--json",
        ]
    )
    assert rc == 0
    listed = _json_stdout(capsys)
    assert listed == {
        "pairings": [
            {
                "channel": "telegram",
                "created_at": listed["pairings"][0]["created_at"],
                "last_seen_at": listed["pairings"][0]["last_seen_at"],
                "note": "seeded for cli test",
                "principal_id": principal_id,
                "scopes": [
                    "cp.message.read",
                    "cp.message.write",
                    "session.read",
                    "session.write",
                    "run.start",
                ],
                "status": "active",
                "subject_id": "7105273251",
            }
        ]
    }
    assert "token" not in _as_text(listed).lower()
    assert "hash" not in _as_text(listed).lower()

    rc = _run_cli(
        [
            "channel",
            "pairings",
            "show",
            "--config",
            str(config_path),
            "--channel",
            "telegram",
            "--subject-id",
            "7105273251",
            "--json",
        ]
    )
    assert rc == 0
    shown = _json_stdout(capsys)
    assert shown["pairing"]["principal_id"] == principal_id
    assert shown["pairing"]["subject_id"] == "7105273251"
    assert "token" not in _as_text(shown).lower()
    assert "hash" not in _as_text(shown).lower()


def test_pairings_scopes_set_requires_confirmation_and_updates_audit(
    tmp_path: Path,
    capsys,
) -> None:
    config_path = _write_profile(tmp_path)
    _seed_binding(config_path)

    denied = _run_cli(
        [
            "channel",
            "pairings",
            "scopes",
            "set",
            "--config",
            str(config_path),
            "--channel",
            "telegram",
            "--subject-id",
            "7105273251",
            "--scopes",
            "cp.message.read",
            "--json",
        ]
    )
    assert denied == 2
    assert _json_stdout(capsys)["error"] == "confirmation_required"

    before = _auth_for(config_path, channel="telegram", subject_id="7105273251")
    assert "cp.message.write" in before.scopes

    rc = _run_cli(
        [
            "channel",
            "pairings",
            "scopes",
            "set",
            "--config",
            str(config_path),
            "--channel",
            "telegram",
            "--subject-id",
            "7105273251",
            "--scopes",
            "cp.message.read",
            "--yes",
            "--json",
        ]
    )
    assert rc == 0
    updated = _json_stdout(capsys)
    assert updated["pairing"]["scopes"] == ["cp.message.read"]

    after = _auth_for(config_path, channel="telegram", subject_id="7105273251")
    command = ParsedCommand(canonical="help", original_text="/help", args=[])
    allowed, reason = ScopeAuthorizer().command_allowed(command, after)
    assert allowed is False
    assert "cp.message.write" in reason

    store = _open_store(config_path)
    try:
        events = store.list_audit(event_type="cp.pairing.binding.scopes_updated")
        assert len(events) == 1
    finally:
        store.close()


def test_pairings_scopes_set_refuses_empty_scope_list(
    tmp_path: Path,
    capsys,
) -> None:
    config_path = _write_profile(tmp_path)
    _seed_binding(config_path)

    rc = _run_cli(
        [
            "channel",
            "pairings",
            "scopes",
            "set",
            "--config",
            str(config_path),
            "--channel",
            "telegram",
            "--subject-id",
            "7105273251",
            "--scopes",
            "",
            "--yes",
        ]
    )

    assert rc == 2
    assert "empty scope list" in capsys.readouterr().out


def test_pairings_revoke_deactivates_next_authorization(
    tmp_path: Path,
    capsys,
) -> None:
    config_path = _write_profile(tmp_path)
    _seed_binding(config_path)

    rc = _run_cli(
        [
            "channel",
            "pairings",
            "revoke",
            "--config",
            str(config_path),
            "--channel",
            "telegram",
            "--subject-id",
            "7105273251",
            "--yes",
            "--json",
        ]
    )
    assert rc == 0
    revoked = _json_stdout(capsys)
    assert revoked["pairing"]["status"] == "inactive"

    auth = _auth_for(config_path, channel="telegram", subject_id="7105273251")
    assert auth.role == "unpaired"

    store = _open_store(config_path)
    try:
        events = store.list_audit(event_type="cp.pairing.binding.revoked")
        assert len(events) == 1
    finally:
        store.close()


def test_pairings_unknown_binding_returns_not_found(tmp_path: Path, capsys) -> None:
    config_path = _write_profile(tmp_path)

    rc = _run_cli(
        [
            "channel",
            "pairings",
            "show",
            "--config",
            str(config_path),
            "--channel",
            "telegram",
            "--subject-id",
            "missing",
            "--json",
        ]
    )
    assert rc == 1
    payload = _json_stdout(capsys)
    assert payload["error"] == "pairing_not_found"
