from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Iterable

from openminion.cli.config import load_cli_config, resolve_cli_identity_db_path
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
from openminion.modules.identity.storage.store import SQLiteIdentityStore
from openminion.modules.identity.runtime.service import IdentityCtl
from openminion.services.agent.identity import (
    IdentityBundle,
    IdentityDocument,
    load_identity_bundle,
)


def run_identity_list() -> None:
    ctl = _get_identityctl()
    profiles = ctl.list_profiles()

    print(
        f"{'Agent ID':<20} {'Display Name':<25} {'Version (prefix)':<20} {'Updated At'}"
    )
    print("-" * 90)
    for profile in profiles:
        version = (
            profile.model_dump().get("profile_version", "")[0:12]
            if profile
            else "unknown"
        )
        print(
            f"{profile.agent_id:<20} {profile.display_name:<25} "
            f"{version:<20} {profile.updated_at}"
        )


def run_identity_show(agent_id: str) -> None:
    import yaml

    ctl = _get_identityctl()
    profile = ctl.get_profile(agent_id)

    if not profile:
        print(f"ERROR: Profile for agent '{agent_id}' not found", file=sys.stderr)
        sys.exit(1)

    profile_data = profile.model_dump(mode="python", exclude_none=True)
    print(yaml.dump(profile_data, default_flow_style=False, indent=2))


def run_identity_upsert(yaml_path: str) -> None:
    ctl = _get_identityctl()
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
    from_bundle: str, agent_id: str | None = None
) -> None:
    ctl = _get_identityctl()
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
        for err in list(bundle.errors):
            print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    existing = ctl.get_profile(resolved_agent_id)
    next_profile_revision = 1
    if existing is not None:
        try:
            next_profile_revision = max(1, int(existing.profile_revision) + 1)
        except (TypeError, ValueError):
            next_profile_revision = 1

    documents = _bundle_documents_from_manifest(bundle)
    if not documents:
        print(
            "ERROR: bundle import found no readable markdown documents", file=sys.stderr
        )
        sys.exit(1)

    parsed_bundle = parse_bundle_documents(documents)
    defaulted_fields: list[str] = []
    import_warnings = [str(value) for value in list(bundle.warnings)]
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
    meta = dict(getattr(profile, "meta", {}) or {})
    meta["bundle_fingerprint"] = str(bundle.fingerprint)
    meta["bundle_imported"] = True
    meta["source"] = "bundle"
    if defaulted_fields:
        meta["bundle_import_defaulted_fields"] = list(defaulted_fields)
    if import_warnings:
        meta["bundle_import_warnings"] = list(import_warnings)
    profile = profile.model_copy(update={"meta": meta})

    profile_version = ctl.upsert_profile(
        profile,
        actor="identity-cli",
        reason="bundle_import",
    )
    print(f"imported: {resolved_agent_id} ({str(profile_version)[:12]})")
    if defaulted_fields:
        print(f"defaulted_fields: {', '.join(defaulted_fields)}")
    if import_warnings:
        print(f"warnings: {len(import_warnings)}")


def run_identity_export_yaml(output_path: str, agent_id: str | None = None) -> None:
    import yaml

    ctl = _get_identityctl()
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
        run_identity_export_yaml(normalized_output, agent_id=agent_id)
        return
    run_identity_export_markdown(
        normalized_output_dir,
        agent_id=agent_id,
        force=force,
    )


def run_identity_export_markdown(
    output_dir: str,
    agent_id: str | None = None,
    *,
    force: bool = False,
) -> None:
    ctl = _get_identityctl()
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


def run_identity_diff(agent_id: str, bundle_dir: str | None = None) -> None:
    normalized_agent = str(agent_id or "").strip()
    if not normalized_agent:
        print("ERROR: agent_id is required", file=sys.stderr)
        sys.exit(1)
    ctl = _get_identityctl()
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
        for err in list(bundle.errors):
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


def run_identity_delete(agent_id: str) -> None:
    ctl = _get_identityctl()

    profile = ctl.get_profile(agent_id)
    if not profile:
        print(f"Profile for agent '{agent_id}' not found", file=sys.stderr)
        sys.exit(1)

    ctl.delete_profile(agent_id)
    print(f"Successfully deleted profile for agent '{agent_id}'")


def run_identity_render(
    agent_id: str, purpose: str = "act", max_tokens: int = 180
) -> None:
    ctl = _get_identityctl()

    try:
        snippet = ctl.render(agent_id, purpose=purpose, max_tokens=max_tokens)

        print(snippet.text)
        print("\n--- Rendering Stats ---")
        print(f"Purpose: {purpose}")
        print(f"Max Tokens: {max_tokens}")
        print(
            f"Used Tokens: {getattr(snippet.budget, 'used_tokens', 0) if snippet.budget else 0}"
        )
        print(
            f"Profile Version: {getattr(snippet, 'profile_version', 'unknown')[:12] if hasattr(snippet, 'profile_version') else 'unknown'}"
        )
        print(f"Render Version: {snippet.render_version}")
        if snippet.included_fields:
            print(f"Included Fields: {', '.join(snippet.included_fields)}")
        if snippet.omitted_fields:
            print(f"Omitted Fields: {', '.join(snippet.omitted_fields)}")
    except ValueError as e:
        print(f"Rendering Error: {e}", file=sys.stderr)
        sys.exit(1)


def _get_identityctl() -> IdentityCtl:
    config = load_cli_config()
    db_path = resolve_cli_identity_db_path(config).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteIdentityStore(sqlite_path=str(db_path))
    return IdentityCtl(store=store)


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
    for item in [bundle.agent, bundle.soul, *list(bundle.skills), *list(bundle.notes)]:
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


def _build_identity_bridge_argv(args: argparse.Namespace) -> list[str]:
    command = str(getattr(args, "identity_command", "") or "").strip().lower()
    if command == "list":
        return ["list"]
    if command == "show":
        return ["show", str(args.agent_id)]
    if command == "upsert":
        return ["upsert", str(args.yaml_path)]
    if command == "import":
        argv = [
            "import",
            "--from-bundle",
            str(args.from_bundle),
        ]
        agent_id = str(getattr(args, "agent_id", "") or "").strip()
        if agent_id:
            argv.extend(["--agent-id", agent_id])
        return argv
    if command == "export":
        argv = ["export"]
        output = str(getattr(args, "output", "") or "").strip()
        output_dir = str(getattr(args, "output_dir", "") or "").strip()
        agent_id = str(getattr(args, "agent_id", "") or "").strip()
        if output:
            argv.extend(["--output", output])
        if output_dir:
            argv.extend(["--output-dir", output_dir])
        if agent_id:
            argv.extend(["--agent-id", agent_id])
        if bool(getattr(args, "force", False)):
            argv.append("--force")
        return argv
    if command == "diff":
        argv = ["diff", str(args.agent_id)]
        bundle_dir = str(getattr(args, "bundle_dir", "") or "").strip()
        if bundle_dir:
            argv.extend(["--bundle-dir", bundle_dir])
        return argv
    if command == "delete":
        return ["delete", str(args.agent_id)]
    if command == "render":
        return [
            "render",
            str(args.agent_id),
            "--purpose",
            str(args.purpose),
            "--max-tokens",
            str(args.max_tokens),
        ]
    raise RuntimeError(f"Unknown identity command: {command}")


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


def app(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="openminion identity",
        description=(
            "Manage agent identity profiles. Startup precedence: YAML sync first, "
            "markdown bundle sync second, default fallback last."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List identity profiles")
    list_parser.set_defaults(_handler=lambda _: run_identity_list())

    show_parser = subparsers.add_parser("show", help="Show one identity profile")
    _add_identity_show_args(show_parser)
    show_parser.set_defaults(_handler=lambda ns: run_identity_show(ns.agent_id))

    upsert_parser = subparsers.add_parser(
        "upsert", help="Create or update profiles from a YAML file or directory"
    )
    _add_identity_upsert_args(upsert_parser)
    upsert_parser.set_defaults(_handler=lambda ns: run_identity_upsert(ns.yaml_path))

    import_parser = subparsers.add_parser(
        "import", help="Import profile from markdown bundle"
    )
    _add_identity_import_args(import_parser)
    import_parser.set_defaults(
        _handler=lambda ns: run_identity_import_from_bundle(
            ns.from_bundle, agent_id=(ns.agent_id or "")
        )
    )

    export_parser = subparsers.add_parser(
        "export",
        help="Export profiles to YAML (--output) or markdown bundle (--output-dir)",
    )
    _add_identity_export_args(export_parser)
    export_parser.set_defaults(
        _handler=lambda ns: run_identity_export(
            output_path=ns.output,
            output_dir=ns.output_dir,
            agent_id=(ns.agent_id or ""),
            force=bool(ns.force),
        )
    )

    diff_parser = subparsers.add_parser(
        "diff",
        help="Compare SQLite profile against on-disk markdown bundle",
    )
    _add_identity_diff_args(diff_parser)
    diff_parser.set_defaults(
        _handler=lambda ns: run_identity_diff(
            ns.agent_id,
            bundle_dir=(ns.bundle_dir or ""),
        )
    )

    delete_parser = subparsers.add_parser("delete", help="Delete one identity profile")
    _add_identity_delete_args(delete_parser)
    delete_parser.set_defaults(_handler=lambda ns: run_identity_delete(ns.agent_id))

    render_parser = subparsers.add_parser(
        "render", help="Render identity snippet for an agent"
    )
    _add_identity_render_args(render_parser)
    render_parser.set_defaults(
        _handler=lambda ns: run_identity_render(
            ns.agent_id, purpose=ns.purpose, max_tokens=ns.max_tokens
        )
    )

    namespace = parser.parse_args(argv)
    namespace._handler(namespace)
    return 0


_IDENTITY_SUBCOMMAND_SPECS: tuple[tuple[str, str, Any], ...] = (
    ("list", "List all agent profiles", None),
    ("show", "Show specific agent profile", _add_identity_show_args),
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
        sub.set_defaults(
            handler=lambda args: app(_build_identity_bridge_argv(args)),
            needs_app=False,
        )
