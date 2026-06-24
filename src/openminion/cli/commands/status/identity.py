from __future__ import annotations

from pathlib import Path
from typing import Any

from openminion.base.config.core import resolve_default_agent_id
from openminion.cli.config import resolve_cli_identity_db_path
from openminion.cli.identity.provenance import build_identity_provenance
from openminion.cli.presentation.json_output import print_json_payload
from openminion.modules.identity.runtime.service import IdentityCtl
from openminion.modules.identity.storage.store import SQLiteIdentityStore
from openminion.services.lifecycle.self_improvement import SelfImprovementEngine


def run_self_improvement_status(args, *, config) -> int:
    engine = SelfImprovementEngine.from_config(config)
    agent_id = str(
        getattr(args, "agent_id", "") or resolve_default_agent_id(config)
    ).strip()
    if not agent_id:
        raise RuntimeError("`agent_id` is required.")

    if args.status_command == "notes":
        notes = [note.to_dict() for note in engine.list_notes(agent_id=agent_id)]
        payload = {
            "ok": True,
            "agent_id": agent_id,
            "notes_path": str(engine.notes_root),
            "application_mode": engine.application_mode,
            "notes": notes,
            "count": len(notes),
        }
        if getattr(args, "json", False):
            print_json_payload(payload)
            return 0
        print(
            f"status notes: agent_id={agent_id} mode={engine.application_mode} count={len(notes)}"
        )
        for note in notes:
            print(
                f"- signature={note['signature']} status={note['status']} "
                f"occurrences={note['occurrence_count']} applies={note['apply_count']}"
            )
        return 0

    if args.status_command == "note-activate":
        signature = str(getattr(args, "signature", "")).strip()
        if not signature:
            raise RuntimeError("`--signature` is required.")
        promoted = engine.promote_note(agent_id=agent_id, signature=signature)
        if not promoted:
            raise RuntimeError(
                f"Improvement note '{signature}' was not found for agent '{agent_id}'."
            )
        payload = {
            "ok": True,
            "agent_id": agent_id,
            "signature": signature,
            "status": "active",
            "notes_path": str(engine.notes_root),
        }
        if getattr(args, "json", False):
            print_json_payload(payload)
            return 0
        print(
            f"status note-activate: agent_id={agent_id} signature={signature} status=active"
        )
        return 0

    raise RuntimeError("Unknown status command.")


def _build_identityctl_for_status(config) -> tuple[Any, Path]:
    db_path = resolve_cli_identity_db_path(config)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
    return ctl, db_path


def run_identity_status(args, *, config) -> int:
    agent_id = str(
        getattr(args, "agent_id", "") or resolve_default_agent_id(config)
    ).strip()
    purpose = str(getattr(args, "purpose", "act") or "act").strip() or "act"
    max_tokens = max(1, int(getattr(args, "max_tokens", 200) or 200))
    identityctl, db_path = _build_identityctl_for_status(config)
    try:
        profile = identityctl.get_profile(agent_id)
        if profile is None:
            provenance = build_identity_provenance(None)
            payload = {
                "ok": False,
                "agent_id": agent_id,
                "identity_db_path": str(db_path),
                "profile_version": None,
                "render_version": None,
                "profile_revision": None,
                "bundle_imported": False,
                "bundle_fingerprint": "",
                **provenance,
                "error": f"identity profile not found: {agent_id}",
            }
            if getattr(args, "json", False):
                print_json_payload(payload)
            else:
                print(f"status identity: agent_id={agent_id} ok=False")
                print(f"- error: {payload['error']}")
                print(f"- identity_db_path: {db_path}")
            return 1

        snippet = identityctl.render(
            agent_id=agent_id,
            purpose=purpose,
            max_tokens=max_tokens,
        )
        meta = dict(getattr(profile, "meta", {}) or {})
        provenance = build_identity_provenance(profile)
        payload = {
            "ok": True,
            "agent_id": agent_id,
            "identity_db_path": str(db_path),
            "profile_revision": int(profile.profile_revision),
            "profile_version": str(snippet.profile_version),
            "render_version": str(snippet.render_version),
            "bundle_imported": bool(meta.get("bundle_imported")),
            "bundle_fingerprint": str(meta.get("bundle_fingerprint") or ""),
            **provenance,
        }
        if getattr(args, "render", False):
            payload.update(
                {
                    "purpose": str(snippet.purpose),
                    "text": str(snippet.text),
                }
            )
            if getattr(args, "json", False):
                print_json_payload(payload)
            else:
                print(
                    f"status identity render: agent_id={snippet.agent_id} purpose={snippet.purpose}"
                )
                print(f"- profile_version: {snippet.profile_version}")
                print(f"- render_version: {snippet.render_version}")
                print(f"- profile_revision: {profile.profile_revision}")
                print(f"- bundle_imported: {payload['bundle_imported']}")
                print(f"- bundle_fingerprint: {payload['bundle_fingerprint']}")
                print(f"- source_classification: {payload['source_classification']}")
                print(f"- meta_source: {payload['meta_source']}")
                print(
                    f"- source_refreshable_by_bundle: {payload['source_refreshable_by_bundle']}"
                )
                print("\n--- Rendered Identity ---")
                print(snippet.text)
            return 0

        if getattr(args, "json", False):
            print_json_payload(payload)
        else:
            print(f"status identity: agent_id={agent_id} ok=True")
            print(f"- profile_revision: {payload['profile_revision']}")
            print(f"- profile_version: {payload['profile_version']}")
            print(f"- render_version: {payload['render_version']}")
            print(f"- bundle_imported: {payload['bundle_imported']}")
            print(f"- bundle_fingerprint: {payload['bundle_fingerprint']}")
            print(f"- source_classification: {payload['source_classification']}")
            print(f"- meta_source: {payload['meta_source']}")
            print(
                f"- source_refreshable_by_bundle: {payload['source_refreshable_by_bundle']}"
            )
            print(f"- identity_db_path: {db_path}")
        return 0
    finally:
        try:
            identityctl.close()
        except Exception:
            pass
