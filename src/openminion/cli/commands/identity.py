from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Iterable

from openminion.cli.commands.identity_editor import (
    get_identity_context,
    print_structured_payload,
    run_identity_apply,
    run_identity_candidate_validate,
    run_identity_inspect,
)
from openminion.modules.identity.runtime.bundle_importer import (
    BundleTextDocument,
    build_profile_from_parsed_bundle,
    parse_bundle_documents,
)
from openminion.modules.identity.runtime.lockfile import (
    IDENTITY_LOCKFILE_NAME,
    IdentityLockfile,
    build_lock_manifest,
    compute_tree_sha256,
    read_identity_lockfile,
    write_identity_lockfile,
)
from openminion.modules.identity.runtime.md_generator import (
    export_profile_to_markdown_bundle,
)
from openminion.modules.identity.runtime.service import IdentityCtl
from openminion.modules.identity import (
    IdentityBundle,
    IdentityDocument,
    load_identity_bundle,
)


def run_identity_list(*, ctl: IdentityCtl | None = None) -> None:
    ctl = ctl or _get_identityctl()
    profiles = ctl.list_profiles()

    print(
        f"{'Agent ID':<20} {'Display Name':<25} {'Version (prefix)':<20} {'Updated At'}"
    )
    print("-" * 90)
    for profile in profiles:
        version = profile.profile_version[:12]
        print(
            f"{profile.agent_id:<20} {profile.display_name:<25} "
            f"{version:<20} {profile.updated_at}"
        )


def run_identity_show(agent_id: str, *, ctl: IdentityCtl | None = None) -> None:
    import yaml

    ctl = ctl or _get_identityctl()
    profile = ctl.get_profile(agent_id)

    if not profile:
        print(f"ERROR: Profile for agent '{agent_id}' not found", file=sys.stderr)
        sys.exit(1)

    profile_data = profile.model_dump(mode="python", exclude_none=True)
    print(yaml.dump(profile_data, default_flow_style=False, indent=2))


def run_identity_upsert(yaml_path: str, *, ctl: IdentityCtl | None = None) -> None:
    ctl = ctl or _get_identityctl()
    file_path = Path(yaml_path).expanduser().resolve()

    if not file_path.exists():
        print(f"ERROR: Path '{yaml_path}' does not exist", file=sys.stderr)
        sys.exit(1)

    agent_ids = ctl.load_profiles_from_path(file_path)
    summaries = {item.agent_id: item for item in ctl.list_profiles()}
    for aid in agent_ids:
        summary = summaries.get(aid)
        version = (
            str(getattr(summary, "profile_version", "") or "unknown")[:12]
            if summary is not None
            else "unknown"
        )
        print(f"loaded: {aid} ({version})")


def run_identity_import_from_bundle(
    from_bundle: str,
    agent_id: str | None = None,
    *,
    ctl: IdentityCtl | None = None,
) -> None:
    ctl = ctl or _get_identityctl()
    raw_bundle_path = Path(from_bundle).expanduser().resolve()
    if not raw_bundle_path.exists():
        print(f"ERROR: Path '{from_bundle}' does not exist", file=sys.stderr)
        sys.exit(1)
    if not raw_bundle_path.is_dir():
        print(f"ERROR: Path '{from_bundle}' is not a directory", file=sys.stderr)
        sys.exit(1)

    try:
        resolved_agent_id, bundle_loader_root = _resolve_bundle_import_target(
            from_bundle_path=raw_bundle_path,
            explicit_agent_id=agent_id,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    bundle = load_identity_bundle(resolved_agent_id, root=bundle_loader_root)
    if not bundle.ok:
        for err in bundle.errors:
            print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    existing = ctl.get_profile(resolved_agent_id)
    if existing is not None:
        existing_meta = dict(existing.meta or {})
        existing_source = str(existing_meta.get("source", "") or "").strip().lower()
        is_bundle_managed = existing_source == "bundle" or (
            not existing_source
            and bool(str(existing_meta.get("bundle_fingerprint", "") or "").strip())
        )
        if not is_bundle_managed:
            print(
                "ERROR: bundle import cannot overwrite a YAML-managed or protected "
                f"profile for agent '{resolved_agent_id}'; delete it first to change "
                "authority",
                file=sys.stderr,
            )
            sys.exit(1)
    next_profile_revision = (
        max(1, existing.profile_revision + 1) if existing is not None else 1
    )

    documents = _bundle_documents_from_manifest(bundle)
    if not documents:
        print(
            "ERROR: bundle import found no readable markdown documents", file=sys.stderr
        )
        sys.exit(1)

    parsed_bundle = parse_bundle_documents(documents)
    defaulted_fields: list[str] = []
    import_warnings = [str(value) for value in bundle.warnings]
    if not str(parsed_bundle.mission).strip():
        defaulted_fields.append("role.mission")
        import_warnings.append(
            "missing AGENT.md section Mission; default role.mission applied"
        )
    if not parsed_bundle.voice:
        defaulted_fields.append("personality.tone")
        import_warnings.append(
            "missing SOUL.md section Voice; default personality.tone applied"
        )

    profile = build_profile_from_parsed_bundle(
        agent_id=resolved_agent_id,
        parsed=parsed_bundle,
        profile_revision=next_profile_revision,
        display_name=resolved_agent_id,
    )
    meta = dict(profile.meta or {})
    meta["bundle_fingerprint"] = str(bundle.fingerprint)
    meta["bundle_imported"] = True
    meta["source"] = "bundle"
    if defaulted_fields:
        meta["bundle_import_defaulted_fields"] = list(defaulted_fields)
    if import_warnings:
        meta["bundle_import_warnings"] = list(import_warnings)
    profile = profile.model_copy(update={"meta": meta})

    profile_version = ctl.upsert_profile(profile)
    print(f"imported: {resolved_agent_id} ({str(profile_version)[:12]})")
    if defaulted_fields:
        print(f"defaulted_fields: {', '.join(defaulted_fields)}")
    if import_warnings:
        print(f"warnings: {len(import_warnings)}")


def run_identity_export_yaml(
    output_path: str,
    agent_id: str | None = None,
    *,
    ctl: IdentityCtl | None = None,
) -> None:
    import yaml

    ctl = ctl or _get_identityctl()
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    normalized_agent = str(agent_id or "").strip()

    if normalized_agent:
        profile = ctl.get_profile(normalized_agent)
        if profile is None:
            print(
                f"ERROR: Profile for agent '{normalized_agent}' not found",
                file=sys.stderr,
            )
            sys.exit(1)
        payload = profile.model_dump(mode="python", exclude_none=True)
        output.write_text(
            yaml.safe_dump(payload, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        print(f"exported_yaml: {normalized_agent} -> {output}")
        print("fidelity_notice: YAML export is lossless for schema-supported fields.")
        return

    profiles = ctl.list_profiles()
    profile_payloads: dict[str, dict[str, object]] = {}
    for summary in profiles:
        item = ctl.get_profile(summary.agent_id)
        if item is None:
            continue
        profile_payloads[summary.agent_id] = item.model_dump(
            mode="python",
            exclude_none=True,
        )
    payload = {"profiles": profile_payloads}
    output.write_text(
        yaml.safe_dump(payload, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    print(f"exported_yaml: {len(profile_payloads)} profiles -> {output}")
    print("fidelity_notice: YAML export is lossless for schema-supported fields.")


def run_identity_export(
    *,
    output_path: str | None = None,
    output_dir: str | None = None,
    agent_id: str | None = None,
    force: bool = False,
    ctl: IdentityCtl | None = None,
) -> None:
    normalized_output = str(output_path or "").strip()
    normalized_output_dir = str(output_dir or "").strip()
    if bool(normalized_output) == bool(normalized_output_dir):
        print(
            "ERROR: exactly one of --output (YAML) or --output-dir (markdown bundle) is required",
            file=sys.stderr,
        )
        sys.exit(1)
    if normalized_output:
        run_identity_export_yaml(normalized_output, agent_id=agent_id, ctl=ctl)
        return
    run_identity_export_markdown(
        normalized_output_dir,
        agent_id=agent_id,
        force=force,
        ctl=ctl,
    )


def run_identity_export_markdown(
    output_dir: str,
    agent_id: str | None = None,
    *,
    force: bool = False,
    ctl: IdentityCtl | None = None,
) -> None:
    ctl = ctl or _get_identityctl()
    base_output_dir = Path(output_dir).expanduser().resolve()
    base_output_dir.mkdir(parents=True, exist_ok=True)
    print(
        "fidelity_notice: markdown bundle export is lossy; use YAML export for lossless schema-preserving workflows."
    )

    normalized_agent = str(agent_id or "").strip()
    profile_targets: list[tuple[str, object, str]] = []
    if normalized_agent:
        profile = ctl.get_profile(normalized_agent)
        if profile is None:
            print(
                f"ERROR: Profile for agent '{normalized_agent}' not found",
                file=sys.stderr,
            )
            sys.exit(1)
        summaries = {item.agent_id: item for item in ctl.list_profiles()}
        version = str(
            getattr(summaries.get(normalized_agent), "profile_version", "") or ""
        )
        profile_targets.append((normalized_agent, profile, version))
    else:
        for summary in ctl.list_profiles():
            profile = ctl.get_profile(summary.agent_id)
            if profile is None:
                continue
            profile_targets.append(
                (summary.agent_id, profile, str(summary.profile_version))
            )

    for target_agent_id, profile, profile_version in profile_targets:
        bundle_dir = (base_output_dir / "agents" / target_agent_id).resolve()
        bundle_dir.mkdir(parents=True, exist_ok=True)
        drift_issues = _detect_bundle_lockfile_drift(bundle_dir)
        if drift_issues and not force:
            print(
                f"ERROR: refusing to overwrite drifted bundle for '{target_agent_id}' without --force",
                file=sys.stderr,
            )
            for issue in drift_issues:
                print(f"ERROR: drift: {issue}", file=sys.stderr)
            sys.exit(1)

        export_result = export_profile_to_markdown_bundle(profile)
        for document in export_result.documents:
            destination = (bundle_dir / document.relative_path).resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(document.content, encoding="utf-8")

        entries = build_lock_manifest(bundle_dir)
        lockfile = IdentityLockfile(
            generated_from_profile_version=str(profile_version),
            generated_at=datetime.now(timezone.utc).isoformat(),
            files=entries,
            tree_sha256=compute_tree_sha256(entries),
        )
        write_identity_lockfile(bundle_dir / IDENTITY_LOCKFILE_NAME, lockfile)
        print(f"exported_bundle: {target_agent_id} -> {bundle_dir}")
        if export_result.lossy_fields:
            print(
                f"lossy_fields: {target_agent_id}: {', '.join(export_result.lossy_fields)}"
            )


def run_identity_diff(
    agent_id: str,
    bundle_dir: str | None = None,
    *,
    ctl: IdentityCtl | None = None,
) -> None:
    normalized_agent = str(agent_id or "").strip()
    if not normalized_agent:
        print("ERROR: agent_id is required", file=sys.stderr)
        sys.exit(1)
    ctl = ctl or _get_identityctl()
    profile = ctl.get_profile(normalized_agent)
    if profile is None:
        print(
            f"ERROR: Profile for agent '{normalized_agent}' not found", file=sys.stderr
        )
        sys.exit(1)

    export_result = export_profile_to_markdown_bundle(profile)
    expected_parsed = parse_bundle_documents(
        [
            BundleTextDocument(
                relative_path=item.relative_path,
                content=item.content,
            )
            for item in export_result.documents
        ]
    )
    expected_profile = build_profile_from_parsed_bundle(
        agent_id=normalized_agent,
        parsed=expected_parsed,
        profile_revision=max(1, int(profile.profile_revision)),
        display_name=normalized_agent,
    )

    bundle = load_identity_bundle(
        normalized_agent,
        root=(Path(bundle_dir).expanduser().resolve() if bundle_dir else None),
    )
    if not bundle.ok:
        for err in bundle.errors:
            print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
    disk_documents = _bundle_documents_from_manifest(bundle)
    if not disk_documents:
        print(
            "ERROR: bundle diff found no readable markdown documents",
            file=sys.stderr,
        )
        sys.exit(1)
    disk_parsed = parse_bundle_documents(disk_documents)
    disk_profile = build_profile_from_parsed_bundle(
        agent_id=normalized_agent,
        parsed=disk_parsed,
        profile_revision=max(1, int(profile.profile_revision)),
        display_name=normalized_agent,
    )

    differences = _collect_bundle_semantic_differences(expected_profile, disk_profile)
    print(f"identity_diff: {normalized_agent}")
    print(f"bundle_root: {bundle.root_path}")
    print(
        "fidelity_notice: markdown comparison is lossy; listed lossy fields are intentionally excluded from semantic drift checks."
    )
    if differences:
        print("semantic_bundle_drift_fields:")
        for key, expected_value, actual_value in differences:
            print(f"- {key}: sqlite={expected_value!r} bundle={actual_value!r}")
    else:
        print("semantic_bundle_drift_fields: none")

    if export_result.lossy_fields:
        print("lossy_fields_not_compared:")
        for field in export_result.lossy_fields:
            print(f"- {field}")
    else:
        print("lossy_fields_not_compared: none")
    print(f"result: {'drifted' if differences else 'clean'}")


def run_identity_delete(agent_id: str, *, ctl: IdentityCtl | None = None) -> None:
    ctl = ctl or _get_identityctl()

    profile = ctl.get_profile(agent_id)
    if not profile:
        print(f"Profile for agent '{agent_id}' not found", file=sys.stderr)
        sys.exit(1)

    source = str((profile.meta or {}).get("source", "")).strip().lower()
    if source in {"yaml", "bundle"}:
        print(
            f"ERROR: {source}-managed profile '{agent_id}' cannot be deleted from SQLite; remove or migrate its authoritative source explicitly",
            file=sys.stderr,
        )
        sys.exit(1)

    ctl.delete_profile(agent_id)
    print(f"Successfully deleted profile for agent '{agent_id}'")


def run_identity_render(
    agent_id: str,
    purpose: str = "act",
    max_tokens: int = 180,
    *,
    ctl: IdentityCtl | None = None,
) -> None:
    ctl = ctl or _get_identityctl()

    try:
        snippet = ctl.render(agent_id, purpose=purpose, max_tokens=max_tokens)

        print(snippet.text)
        print("\n--- Rendering Stats ---")
        print(f"Purpose: {purpose}")
        print(f"Max Tokens: {max_tokens}")
        print(f"Used Tokens: {snippet.budget.used_tokens}")
        print(f"Profile Version: {snippet.profile_version[:12]}")
        print(f"Render Version: {snippet.render_version}")
        if snippet.included_fields:
            print(f"Included Fields: {', '.join(snippet.included_fields)}")
        if snippet.omitted_fields:
            print(f"Omitted Fields: {', '.join(snippet.omitted_fields)}")
    except ValueError as e:
        print(f"Rendering Error: {e}", file=sys.stderr)
        sys.exit(1)


def run_identity_validate(
    agent_id: str | None = None,
    *,
    file_path: str | None = None,
    strict: bool = False,
    json_output: bool = False,
    config_path: object | None = None,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
    ctl: IdentityCtl | None = None,
) -> None:
    if file_path:
        run_identity_candidate_validate(
            file_path,
            strict=strict,
            json_output=json_output,
        )
        return

    ctl = ctl or _get_identityctl(
        config_path=config_path, home_root=home_root, data_root=data_root
    )
    targets = [agent_id] if agent_id else [row.agent_id for row in ctl.list_profiles()]
    results: dict[str, object] = {}
    overall_ok = True
    for target in targets:
        normalized = str(target or "").strip()
        if not normalized:
            continue
        profile = ctl.get_profile(normalized)
        if profile is None:
            print(f"ERROR: Profile for agent '{normalized}' not found", file=sys.stderr)
            sys.exit(1)
        result = ctl.validate_profile(profile, strict=strict)
        overall_ok = overall_ok and result.ok
        results[normalized] = result.model_dump(mode="python")

    print_structured_payload(
        {"ok": overall_ok, "results": results},
        json_output=json_output,
    )
    if not overall_ok:
        sys.exit(1)


def run_identity_warm_cache(
    agent_id: str,
    purposes: list[str] | None = None,
    *,
    ctl: IdentityCtl | None = None,
) -> None:
    ctl = ctl or _get_identityctl()
    try:
        count = ctl.warm_cache(agent_id=agent_id, purposes=purposes or None)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"warmed: {agent_id} ({count})")


def run_identity_clear_cache(
    agent_id: str | None = None, *, ctl: IdentityCtl | None = None
) -> None:
    ctl = ctl or _get_identityctl()
    ctl.clear_cache(agent_id=agent_id)
    print(f"cleared_cache: {agent_id or 'all'}")


def _get_identityctl(
    *,
    config_path: object | None = None,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
) -> IdentityCtl:
    ctl, _identity_root = get_identity_context(
        config_path=config_path,
        home_root=home_root,
        data_root=data_root,
    )
    return ctl


def _resolve_bundle_import_target(
    *,
    from_bundle_path: Path,
    explicit_agent_id: str | None,
) -> tuple[str, Path]:
    normalized_agent = str(explicit_agent_id or "").strip()
    has_required_files = (from_bundle_path / "AGENT.md").is_file() and (
        from_bundle_path / "SOUL.md"
    ).is_file()
    if has_required_files:
        if from_bundle_path.parent.name == "agents":
            return (
                normalized_agent or from_bundle_path.name,
                from_bundle_path.parent,
            )
        if normalized_agent and normalized_agent != from_bundle_path.name:
            raise ValueError(
                "when --from-bundle points to a direct bundle directory, --agent-id must match the directory name unless parent directory is named 'agents'"
            )
        return (normalized_agent or from_bundle_path.name, from_bundle_path)

    if not normalized_agent:
        raise ValueError(
            "--agent-id is required when --from-bundle points to an identity root or agents directory"
        )
    return (normalized_agent, from_bundle_path)


def _bundle_documents_from_manifest(bundle: IdentityBundle) -> list[BundleTextDocument]:
    documents: list[BundleTextDocument] = []
    for item in _iter_bundle_documents(bundle):
        path = Path(bundle.root_path) / item.relative_path
        if not path.is_file():
            continue
        documents.append(
            BundleTextDocument(
                relative_path=item.relative_path,
                content=path.read_text(encoding="utf-8", errors="ignore"),
            )
        )
    return documents


def _iter_bundle_documents(bundle: IdentityBundle) -> Iterable[IdentityDocument]:
    for item in [bundle.agent, bundle.soul, *bundle.skills, *bundle.notes]:
        if item is None:
            continue
        yield item


def _collect_bundle_semantic_differences(
    expected_profile,
    disk_profile,
) -> list[tuple[str, object, object]]:
    expected_snapshot = {
        "role.mission": expected_profile.role.mission,
        "role.responsibilities": list(expected_profile.role.responsibilities),
        "role.hard_constraints": list(expected_profile.role.hard_constraints),
        "role.escalation_rules": list(expected_profile.role.escalation_rules),
        "personality.tone": expected_profile.personality.tone,
        "personality.formatting": list(expected_profile.personality.formatting),
        "personality.interaction_style": list(
            expected_profile.personality.interaction_style
        ),
    }
    disk_snapshot = {
        "role.mission": disk_profile.role.mission,
        "role.responsibilities": list(disk_profile.role.responsibilities),
        "role.hard_constraints": list(disk_profile.role.hard_constraints),
        "role.escalation_rules": list(disk_profile.role.escalation_rules),
        "personality.tone": disk_profile.personality.tone,
        "personality.formatting": list(disk_profile.personality.formatting),
        "personality.interaction_style": list(
            disk_profile.personality.interaction_style
        ),
    }
    differences: list[tuple[str, object, object]] = []
    for key in sorted(expected_snapshot):
        expected_value = expected_snapshot[key]
        actual_value = disk_snapshot[key]
        if expected_value != actual_value:
            differences.append((key, expected_value, actual_value))
    return differences


def _detect_bundle_lockfile_drift(bundle_dir: Path) -> list[str]:
    lockfile_path = bundle_dir / IDENTITY_LOCKFILE_NAME
    current_entries = build_lock_manifest(bundle_dir)
    if not lockfile_path.exists():
        if current_entries:
            return ["lockfile missing for non-empty bundle directory"]
        return []

    lockfile = read_identity_lockfile(lockfile_path)
    current_map = {item.relative_path: item.sha256 for item in current_entries}
    locked_map = {item.relative_path: item.sha256 for item in lockfile.files}

    issues: list[str] = []
    added = sorted(path for path in current_map if path not in locked_map)
    removed = sorted(path for path in locked_map if path not in current_map)
    changed = sorted(
        path
        for path in current_map
        if path in locked_map and current_map[path] != locked_map[path]
    )
    if added:
        issues.append(f"added files: {', '.join(added)}")
    if removed:
        issues.append(f"removed files: {', '.join(removed)}")
    if changed:
        issues.append(f"changed files: {', '.join(changed)}")

    if lockfile.tree_sha256:
        current_tree_hash = compute_tree_sha256(current_entries)
        if str(lockfile.tree_sha256) != str(current_tree_hash):
            issues.append("tree hash mismatch")

    return issues


def _add_identity_show_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("agent_id")


def _add_identity_upsert_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "yaml_path", help="Path to YAML file or directory with profiles"
    )


def _add_identity_import_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--from-bundle",
        dest="from_bundle",
        required=True,
        help="Path to bundle root (agents/<agent_id>) or identity root containing agents/",
    )
    parser.add_argument(
        "--agent-id",
        default="",
        help="Agent ID when --from-bundle points to an identity root or agents directory",
    )


def _add_identity_export_args(parser: argparse.ArgumentParser) -> None:
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", help="Output YAML path")
    destination.add_argument(
        "--output-dir", help="Output directory for markdown bundle exports"
    )
    parser.add_argument(
        "--agent-id",
        default="",
        help="Optional agent ID. When omitted, export all profiles",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting a drifted markdown bundle export",
    )


def _add_identity_diff_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("agent_id", help="Agent ID to diff")
    parser.add_argument(
        "--bundle-dir",
        default="",
        help="Optional bundle root override (identity root or agents directory)",
    )


def _add_identity_delete_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("agent_id")


def _add_identity_render_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("agent_id")
    parser.add_argument("--purpose", default="act")
    parser.add_argument("--max-tokens", type=int, default=180)


def _add_identity_validate_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--agent-id", default="", help="Optional agent ID to validate")
    parser.add_argument("--file", default="", help="Validate an unsaved YAML candidate")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Promote semantic warnings to validation errors",
    )
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")


def _add_identity_inspect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--agent-id", default="", help="Optional agent ID to inspect")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")


def _add_identity_apply_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--file", required=True, help="Complete direct profile YAML")
    parser.add_argument("--expected-profile-version", required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Promote semantic warnings to validation errors",
    )
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")


def _add_identity_warm_cache_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--agent-id", required=True)
    parser.add_argument(
        "--purpose",
        action="append",
        default=[],
        help="Purpose to warm; repeat for multiple purposes",
    )


def _add_identity_clear_cache_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--agent-id", default="", help="Optional agent ID cache scope")


_IDENTITY_SUBCOMMAND_SPECS: tuple[tuple[str, str, Any], ...] = (
    ("list", "List all agent profiles", None),
    ("show", "Show specific agent profile", _add_identity_show_args),
    ("inspect", "Inspect identity state", _add_identity_inspect_args),
    (
        "upsert",
        "Create/update profiles from YAML file or directory",
        _add_identity_upsert_args,
    ),
    ("import", "Import profile from markdown bundle", _add_identity_import_args),
    (
        "export",
        "Export profiles to YAML or markdown bundles",
        _add_identity_export_args,
    ),
    ("diff", "Diff SQLite profile vs markdown bundle", _add_identity_diff_args),
    ("delete", "Delete specific agent profile", _add_identity_delete_args),
    ("render", "Render identity snippet for agent", _add_identity_render_args),
    ("validate", "Validate identity profiles", _add_identity_validate_args),
    ("apply", "Atomically apply direct profile YAML", _add_identity_apply_args),
    (
        "warm-cache",
        "Warm cached identity snippets",
        _add_identity_warm_cache_args,
    ),
    ("clear-cache", "Clear cached identity snippets", _add_identity_clear_cache_args),
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    identity = subparsers.add_parser(
        "identity",
        help="Identity profile management (list, show, upsert, import, export, delete, render)",
        description=(
            "Identity profile management. Startup precedence: YAML sync first, "
            "bundle sync second, default fallback last."
        ),
    )
    identity_subparsers = identity.add_subparsers(
        dest="identity_command", required=True
    )

    for name, help_text, add_args in _IDENTITY_SUBCOMMAND_SPECS:
        sub = identity_subparsers.add_parser(name, help=help_text)
        if add_args is not None:
            add_args(sub)
        sub.set_defaults(handler=_run_registered_identity_command, needs_app=False)


def _run_registered_identity_command(args: argparse.Namespace) -> int:
    command = str(getattr(args, "identity_command", "") or "").strip().lower()
    context = {
        "config_path": getattr(args, "config", None),
        "home_root": getattr(args, "home_root", None),
        "data_root": getattr(args, "data_root", None),
    }
    if command == "inspect":
        run_identity_inspect(
            agent_id=(str(getattr(args, "agent_id", "") or "").strip() or None),
            json_output=bool(getattr(args, "json", False)),
            **context,
        )
        return 0
    if command == "validate":
        run_identity_validate(
            agent_id=(str(getattr(args, "agent_id", "") or "").strip() or None),
            file_path=(str(getattr(args, "file", "") or "").strip() or None),
            strict=bool(getattr(args, "strict", False)),
            json_output=bool(getattr(args, "json", False)),
            **context,
        )
        return 0
    if command == "apply":
        run_identity_apply(
            str(args.file),
            expected_profile_version=str(args.expected_profile_version),
            expected_source_sha256=str(args.expected_source_sha256),
            strict=bool(getattr(args, "strict", False)),
            json_output=bool(getattr(args, "json", False)),
            **context,
        )
        return 0
    ctl, identity_root = get_identity_context(**context)
    if command == "list":
        run_identity_list(ctl=ctl)
    elif command == "show":
        run_identity_show(str(args.agent_id), ctl=ctl)
    elif command == "upsert":
        run_identity_upsert(str(args.yaml_path), ctl=ctl)
    elif command == "import":
        run_identity_import_from_bundle(
            str(args.from_bundle),
            agent_id=(str(getattr(args, "agent_id", "") or "").strip() or None),
            ctl=ctl,
        )
    elif command == "export":
        run_identity_export(
            output_path=(str(getattr(args, "output", "") or "").strip() or None),
            output_dir=(str(getattr(args, "output_dir", "") or "").strip() or None),
            agent_id=(str(getattr(args, "agent_id", "") or "").strip() or None),
            force=bool(getattr(args, "force", False)),
            ctl=ctl,
        )
    elif command == "diff":
        run_identity_diff(
            str(args.agent_id),
            bundle_dir=(
                str(getattr(args, "bundle_dir", "") or "").strip() or str(identity_root)
            ),
            ctl=ctl,
        )
    elif command == "delete":
        run_identity_delete(str(args.agent_id), ctl=ctl)
    elif command == "render":
        run_identity_render(
            str(args.agent_id),
            purpose=str(args.purpose),
            max_tokens=int(args.max_tokens),
            ctl=ctl,
        )
    elif command == "warm-cache":
        run_identity_warm_cache(
            str(args.agent_id),
            purposes=[str(value) for value in list(args.purpose or [])],
            ctl=ctl,
        )
    elif command == "clear-cache":
        run_identity_clear_cache(
            agent_id=(str(getattr(args, "agent_id", "") or "").strip() or None),
            ctl=ctl,
        )
    else:
        raise RuntimeError(f"Unknown identity command: {command}")
    return 0
