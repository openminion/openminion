import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import typer

from openminion.base.config.env import resolve_environment_config
from openminion.base.config import resolve_data_root, resolve_home_root
from openminion.modules.memory.storage.factory import resolve_memory_backend
from openminion.modules.memory.storage import (
    AuditedMemoryStore,
    SQLiteMemoryAuditSink,
    default_memory_audit_db_path,
)
from openminion.modules.memory.storage.base import (
    ListQueryOptions,
    SearchQueryOptions,
    CandidateListOptions,
)
from openminion.modules.memory.models import CandidateReview
from openminion.modules.memory.portability import (
    MemoryBundleExportOptions,
    MemoryBundleImportOptions,
    read_bundle_snapshot,
    write_bundle_snapshot,
)
from openminion.modules.memory.config import load_config
from openminion.modules.memory.constants import (
    DEFAULT_INTEGRATED_SQLITE_SUBPATH,
    DEFAULT_STANDALONE_SQLITE_SUBPATH,
    MEMORY_CANDIDATE_STATUS_APPROVED,
    MEMORY_CANDIDATE_STATUS_REJECTED,
)
from openminion.modules.memory.diagnostics.operability import (
    compute_stats,
    format_history_timeline,
    resolve_trace_file_path,
    serialize_for_json,
)
from openminion.modules.memory.diagnostics.cli import (
    build_inspect_payload,
    emit_export,
    follow_trace_file,
    read_trace_events_or_warn,
    render_stats_human,
    render_trace_rows,
    resolve_integrated_db_path_from_env,
)
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.diagnostics.tool_failure import (
    diagnose_tool_failure_fact_poisoning,
    render_tool_failure_fact_diagnostic_report,
)
from openminion.modules.memory.runtime.scorer import score_records
from openminion.modules.memory.runtime.promotion import PromotionPolicy
from openminion.modules.memory.runtime.gc import run_gc
from openminion.modules.cli_common import (
    DATA_ROOT_OPTION_HELP,
    HOME_ROOT_OPTION_HELP,
    apply_home_data_root_env,
)
from openminion.base.constants import OPENMINION_DATA_ROOT_ENV, OPENMINION_HOME_ENV
from openminion.modules.storage.cli_registrar import register_storage_commands
from openminion.modules.storage.module_cli import build_storage_argv, run_storage_argv


def _get_service(db: Optional[str] = None) -> MemoryService:
    resolved_db_path: Path
    if db:
        resolved_db_path = Path(db)
        resolved = resolve_memory_backend(
            config={"backend": "sqlite"}, db_path=resolved_db_path
        )
        store = resolved.store
    else:
        env_owner = resolve_environment_config()
        data_root_env = env_owner.get(OPENMINION_DATA_ROOT_ENV, "").strip()
        if data_root_env:
            home_root = resolve_home_root()
            data_root = resolve_data_root(home_root, data_root=data_root_env)
            resolved_db_path = Path(data_root) / DEFAULT_INTEGRATED_SQLITE_SUBPATH
            resolved = resolve_memory_backend(
                config=load_config(env=dict(os.environ)).store,
                db_path=resolved_db_path,
            )
            store = resolved.store
        else:
            cfg = load_config(env=dict(os.environ))
            resolved_db_path = Path(
                cfg.store.sqlite_path
                or (Path.home() / DEFAULT_STANDALONE_SQLITE_SUBPATH)
            )
            resolved = resolve_memory_backend(
                config=cfg.store,
                db_path=resolved_db_path,
            )
            store = resolved.store
    audited_store = AuditedMemoryStore(
        store,
        sink=SQLiteMemoryAuditSink(default_memory_audit_db_path(resolved_db_path)),
    )
    return MemoryService(audited_store, PromotionPolicy())


def _resolve_storage_db_path(db: Optional[Path]) -> Path:
    if db:
        return db.expanduser().resolve(strict=False)
    integrated = resolve_integrated_db_path_from_env()
    if integrated is not None:
        return integrated
    cfg = load_config(env=dict(os.environ))
    sqlite_path = cfg.store.sqlite_path or (
        Path.home() / DEFAULT_STANDALONE_SQLITE_SUBPATH
    )
    return Path(sqlite_path).expanduser().resolve(strict=False)


def _run_storage_command(
    *,
    command: str,
    db: Optional[Path],
    root: Optional[Path],
    fallback: Optional[Path],
    snapshot_root: Optional[Path],
    snapshot_path: Optional[Path],
    mode: Optional[str],
    level: Optional[str],
    out: Optional[Path],
    notes: Optional[str],
    storage_input: Optional[Path],
    skip_checksum: bool,
) -> None:
    env_owner = resolve_environment_config()
    home_root = env_owner.get(OPENMINION_HOME_ENV, "").strip() or None
    data_root = env_owner.get(OPENMINION_DATA_ROOT_ENV, "").strip() or None
    db_path = _resolve_storage_db_path(db)
    argv = build_storage_argv(
        module_id="memory",
        db_path=db_path,
        command=command,
        home_root=home_root,
        data_root=data_root,
        root=str(root) if root else None,
        fallback=str(fallback) if fallback else None,
        snapshot_root=str(snapshot_root) if snapshot_root else None,
        snapshot_path=str(snapshot_path) if snapshot_path else None,
        mode=mode,
        level=level,
        out=str(out) if out else None,
        notes=notes,
        input_dir=str(storage_input) if storage_input else None,
        skip_checksum=skip_checksum,
    )
    run_storage_argv(argv)


def _output(data, as_json: bool):
    if as_json:
        typer.echo(json.dumps(serialize_for_json(data), default=str))
    else:
        if isinstance(data, list):
            for item in data:
                typer.echo(str(item))
        else:
            typer.echo(str(data))


def _fail_memory_command(command: str, error: Exception) -> None:
    typer.echo(
        f"Error: {command} is not supported for this memory store ({error})",
        err=True,
    )
    raise typer.Exit(1)


def _build_candidate_review(reviewer: str, note: Optional[str]) -> CandidateReview:
    return CandidateReview(reviewer, datetime.now(timezone.utc).isoformat(), note)


def _render_search_result(record: Any) -> str:
    title = str(getattr(record, "title", "") or "").strip()
    content = getattr(record, "content", "")
    if isinstance(content, dict):
        content = json.dumps(content, default=str, sort_keys=True)
    preview = " ".join(str(content).split()).strip()
    if len(preview) > 120:
        preview = preview[:117].rstrip() + "..."
    heading = (
        f"{getattr(record, 'id', '')} "
        f"[{getattr(record, 'type', '')}] "
        f"{getattr(record, 'scope', '')}"
    )
    if title:
        heading = f"{heading} {title}"
    return f"{heading}\n  {preview}".rstrip()


def _register_root_callback(app: typer.Typer) -> None:
    @app.callback(invoke_without_command=True)
    def main_callback(
        ctx: typer.Context,
        home_root: Optional[Path] = typer.Option(
            None,
            "--home-root",
            help=HOME_ROOT_OPTION_HELP,
        ),
        data_root: Optional[Path] = typer.Option(
            None,
            "--data-root",
            help=DATA_ROOT_OPTION_HELP,
        ),
    ) -> None:
        apply_home_data_root_env(home_root=home_root, data_root=data_root)
        if ctx.invoked_subcommand is not None:
            return
        typer.echo(ctx.get_help())


def _register_storage_commands(storage_app: typer.Typer) -> None:
    register_storage_commands(storage_app, run_storage_command=_run_storage_command)


def _register_trace_commands(trace_app: typer.Typer) -> None:
    @trace_app.command("list")
    def cmd_trace_list(
        trace_file: Optional[Path] = typer.Option(None, "--trace-file"),
        limit: int = typer.Option(50, "--limit"),
        event_type: Optional[str] = typer.Option(None, "--event-type"),
        since: Optional[str] = typer.Option(None, "--since"),
        json_out: bool = typer.Option(False, "--json"),
    ):
        """List persisted memory trace events."""
        resolved_trace_file = resolve_trace_file_path(explicit_path=trace_file)
        events = read_trace_events_or_warn(
            resolved_trace_file,
            limit=limit,
            event_type=event_type,
            since=since,
        )
        if not events:
            return
        if json_out:
            _output(events, True)
            return
        typer.echo(render_trace_rows(events))

    @trace_app.command("tail")
    def cmd_trace_tail(
        trace_file: Optional[Path] = typer.Option(None, "--trace-file"),
        limit: int = typer.Option(20, "--limit"),
        event_type: Optional[str] = typer.Option(None, "--event-type"),
        follow: bool = typer.Option(False, "--follow"),
        json_out: bool = typer.Option(False, "--json"),
    ):
        """Tail persisted memory trace events."""
        resolved_trace_file = resolve_trace_file_path(explicit_path=trace_file)
        if follow:
            if json_out:
                typer.echo(
                    "Warning: --json is ignored in follow mode; streaming JSONL is written line-by-line.",
                    err=True,
                )
            follow_trace_file(
                resolved_trace_file,
                limit=limit,
                event_type=event_type,
            )
            return
        events = read_trace_events_or_warn(
            resolved_trace_file,
            limit=limit,
            event_type=event_type,
        )
        if not events:
            return
        if json_out:
            _output(events, True)
            return
        typer.echo(render_trace_rows(events))


def _register_read_commands(app: typer.Typer) -> None:
    @app.command("list")
    def cmd_list(
        scope: str = typer.Option(..., help="Scope to list records from"),
        type: Optional[str] = typer.Option(None, help="Filter by type"),
        limit: int = typer.Option(100, help="Max results"),
        json_out: bool = typer.Option(False, "--json", help="Output as JSON"),
        db: Optional[str] = typer.Option(None, help="Override DB path"),
    ):
        """List memory records."""
        svc = _get_service(db)
        opts = ListQueryOptions(
            scopes=[scope], types=[type] if type else None, limit=limit
        )
        records = svc.list(opts)
        _output(records, json_out)

    @app.command("get")
    def cmd_get(
        record_id: str = typer.Argument(..., help="Record ID"),
        json_out: bool = typer.Option(False, "--json"),
        db: Optional[str] = typer.Option(None),
    ):
        """Get a single memory record by ID."""
        svc = _get_service(db)
        try:
            record = svc.get(record_id)
            _output(record, json_out)
        except Exception as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)

    @app.command("search")
    def cmd_search(
        query: str = typer.Argument(..., help="Full-text search query"),
        scope: Optional[str] = typer.Option(None),
        limit: int = typer.Option(20),
        explain: bool = typer.Option(False, "--explain"),
        json_out: bool = typer.Option(False, "--json"),
        db: Optional[str] = typer.Option(None),
    ):
        """Full-text search across memory records."""
        svc = _get_service(db)
        opts = SearchQueryOptions(
            query=query, scopes=[scope] if scope else [], limit=limit
        )
        records = svc.search(opts)
        if explain:
            records = score_records(
                records,
                ranking_config=svc._ranking_config,  # noqa: SLF001
            )
        if json_out:
            _output(records, True)
            return
        if not explain:
            for record in records:
                typer.echo(_render_search_result(record))
            return
        for record in records:
            typer.echo(_render_search_result(record))
            breakdown = dict(getattr(record, "meta", {}).get("score_breakdown", {}))
            typer.echo(
                "  score: "
                f"unified={float(breakdown.get('unified_score', 0.0) or 0.0):.3f} "
                f"relevance={float(breakdown.get('relevance', 0.0) or 0.0):.3f} "
                f"recency={float(breakdown.get('recency', 0.0) or 0.0):.3f} "
                f"feedback={float(breakdown.get('feedback', 0.0) or 0.0):.3f} "
                f"type_bonus={float(breakdown.get('type_bonus', 0.0) or 0.0):.3f} "
                f"confidence={float(breakdown.get('confidence', 0.0) or 0.0):.3f}"
            )

    @app.command("candidates")
    def cmd_candidates(
        session_id: str = typer.Option(..., help="Session ID to list candidates for"),
        status: Optional[str] = typer.Option(None, help="Filter by status"),
        limit: int = typer.Option(100),
        json_out: bool = typer.Option(False, "--json"),
        db: Optional[str] = typer.Option(None),
    ):
        """List candidates for a session."""
        svc = _get_service(db)
        opts = CandidateListOptions(session_id=session_id, status=status, limit=limit)
        candidates = svc.candidate_list(opts)
        _output(candidates, json_out)

    @app.command("history")
    def cmd_history(
        scope: str = typer.Option(...),
        type: str = typer.Option(...),
        key: str = typer.Option(...),
        json_out: bool = typer.Option(False, "--json"),
        db: Optional[str] = typer.Option(None),
    ):
        """Show version history for a keyed record."""
        svc = _get_service(db)
        records = svc._store.history(scope, type, key)  # noqa: SLF001
        if json_out:
            _output(records, True)
            return
        typer.echo(format_history_timeline(records))

    @app.command("stats")
    def cmd_stats(
        scope: Optional[str] = typer.Option(None, "--scope"),
        json_out: bool = typer.Option(False, "--json"),
        db: Optional[str] = typer.Option(None, "--db"),
    ):
        """Show aggregate memory stats."""
        svc = _get_service(db)
        try:
            stats = compute_stats(svc._store, scope=scope)  # noqa: SLF001
        except TypeError as exc:
            _fail_memory_command("stats", exc)
        if json_out:
            _output(stats, True)
            return
        typer.echo(render_stats_human(stats))

    @app.command("export")
    def cmd_export(
        scope: str = typer.Option(..., "--scope"),
        type: Optional[str] = typer.Option(None, "--type"),
        limit: int = typer.Option(100, "--limit"),
        export_format: str = typer.Option("jsonl", "--format"),
        bundle: bool = typer.Option(False, "--bundle"),
        include_candidates: bool = typer.Option(False, "--include-candidates"),
        include_tier_history: bool = typer.Option(False, "--include-tier-history"),
        include_provenance: bool = typer.Option(
            False,
            "--include-provenance",
            help=(
                "MPF-08: include per-turn provenance traces from the canonical "
                "MemoryProvenanceRecorder in the bundle. Off by default; the "
                "recorder is in-memory and many operators do not need the trace "
                "history shipped alongside records."
            ),
        ),
        out: Optional[Path] = typer.Option(None, "--out"),
        db: Optional[str] = typer.Option(None, "--db"),
    ):
        """Export filtered memory records."""
        svc = _get_service(db)
        if bundle:
            if export_format != "jsonl":
                typer.echo(
                    "Error: --bundle cannot be combined with --format",
                    err=True,
                )
                raise typer.Exit(1)
            if out is None:
                typer.echo("Error: --bundle requires --out", err=True)
                raise typer.Exit(1)
            snapshot = svc.export_bundle_snapshot(
                MemoryBundleExportOptions(
                    scopes=[scope],
                    types=[type] if type else None,
                    limit=limit,
                    include_candidates=include_candidates,
                    include_tier_history=include_tier_history,
                    include_provenance=include_provenance,
                )
            )
            path = write_bundle_snapshot(snapshot, out)
            typer.echo(str(path))
            return
        opts = ListQueryOptions(
            scopes=[scope],
            types=[type] if type else None,
            limit=limit,
        )
        records = svc.list(opts)
        if export_format not in {"json", "jsonl"}:
            typer.echo("Error: --format must be 'json' or 'jsonl'", err=True)
            raise typer.Exit(1)
        emit_export(records, export_format=export_format, out=out)

    @app.command("import")
    def cmd_import(
        bundle: Path = typer.Option(..., "--bundle"),
        scope_rewrite: list[str] = typer.Option([], "--scope-rewrite"),
        trust: str = typer.Option("direct", "--trust"),
        conflict: str = typer.Option("skip", "--conflict"),
        id_mode: str = typer.Option("preserve", "--id-mode"),
        dry_run: bool = typer.Option(False, "--dry-run"),
        json_out: bool = typer.Option(False, "--json"),
        db: Optional[str] = typer.Option(None, "--db"),
    ):
        """Import a memory bundle."""
        svc = _get_service(db)
        rewrites: dict[str, str] = {}
        for item in scope_rewrite:
            raw = str(item or "").strip()
            if not raw or "=" not in raw:
                typer.echo(
                    "Error: --scope-rewrite must use source=target format",
                    err=True,
                )
                raise typer.Exit(1)
            src, dst = raw.split("=", 1)
            src = src.strip()
            dst = dst.strip()
            if not src or not dst:
                typer.echo(
                    "Error: --scope-rewrite must use source=target format",
                    err=True,
                )
                raise typer.Exit(1)
            rewrites[src] = dst
        try:
            snapshot = read_bundle_snapshot(bundle)
            result = svc.import_bundle_snapshot(
                snapshot,
                MemoryBundleImportOptions(
                    scope_rewrites=rewrites,
                    trust_mode=str(trust or "direct"),  # type: ignore[arg-type]
                    conflict_mode=str(conflict or "skip"),  # type: ignore[arg-type]
                    id_mode=str(id_mode or "preserve"),  # type: ignore[arg-type]
                    dry_run=bool(dry_run),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)
        if json_out:
            _output(result, True)
            return
        typer.echo(f"applied: {result.applied}")
        typer.echo(f"trust_mode: {result.trust_mode}")
        typer.echo(f"conflict_mode: {result.conflict_mode}")
        typer.echo(f"id_mode: {result.id_mode}")
        typer.echo(f"imported_records: {result.imported_records}")
        typer.echo(f"staged_candidates: {result.staged_candidates}")
        typer.echo(f"imported_candidates: {result.imported_candidates}")
        typer.echo(f"imported_relations: {result.imported_relations}")
        typer.echo(f"imported_tier_transitions: {result.imported_tier_transitions}")
        typer.echo(f"imported_provenance_traces: {result.imported_provenance_traces}")
        typer.echo(f"skipped_records: {result.skipped_records}")
        if result.skipped_sections:
            typer.echo("skipped_sections: " + ", ".join(result.skipped_sections))
        if result.rewrites:
            typer.echo("rewrites:")
            for src, dst in result.rewrites.items():
                typer.echo(f"  {src} -> {dst}")
        if result.conflicts:
            typer.echo("conflicts:")
            for item in result.conflicts:
                typer.echo("  " + json.dumps(item, sort_keys=True, default=str))

    @app.command("inspect")
    def cmd_inspect(
        scope: Optional[str] = typer.Option(None, "--scope"),
        trace_file: Optional[Path] = typer.Option(None, "--trace-file"),
        json_out: bool = typer.Option(False, "--json"),
        db: Optional[str] = typer.Option(None, "--db"),
    ):
        """Show a combined memory inspection view."""
        svc = _get_service(db)
        db_path = _resolve_storage_db_path(Path(db) if db else None)
        resolved_trace_file = resolve_trace_file_path(
            explicit_path=trace_file,
            db_path=db_path,
        )
        payload = build_inspect_payload(
            service=svc,
            scope=scope,
            db_path=db_path,
            trace_file=resolved_trace_file,
        )
        if json_out:
            _output(payload, True)
            return
        snapshot = payload["snapshot"]
        typer.echo("Memory inspect")
        typer.echo(f"  db: {payload['db_path']}")
        typer.echo(f"  scope: {scope or 'all'}")
        typer.echo(
            "  availability: "
            f"memory={snapshot.get('memory_available')} "
            f"vector={snapshot.get('vector_search_available')} "
            f"degraded={snapshot.get('degraded')}"
        )
        if payload.get("stats"):
            typer.echo("")
            typer.echo(render_stats_human(payload["stats"]))
        history_summary = payload.get("history_summary")
        if history_summary:
            typer.echo("")
            typer.echo(
                "Deepest history: "
                f"{history_summary.get('scope')} {history_summary.get('type')} {history_summary.get('key')} "
                f"(depth {history_summary.get('depth')})"
            )
            reasons = [
                reason for reason in history_summary.get("reasons", []) if reason
            ]
            if reasons:
                typer.echo("  reasons: " + ", ".join(reasons))
        last_gc = payload.get("last_gc")
        last_reflection = payload.get("last_reflection")
        if last_gc or last_reflection:
            typer.echo("")
            typer.echo(f"Last GC-ish event: {last_gc or 'n/a'}")
            typer.echo(f"Last reflection: {last_reflection or 'n/a'}")
        recent_events = list(payload.get("recent_trace_events", []))
        if recent_events:
            typer.echo("")
            typer.echo("Recent trace events:")
            typer.echo(render_trace_rows(recent_events))

    @app.command("diagnose-tool-failures")
    def cmd_diagnose_tool_failures(
        scope: Optional[str] = typer.Option(None, "--scope"),
        tombstone_structured: bool = typer.Option(False, "--tombstone-structured"),
        limit: Optional[int] = typer.Option(None, "--limit"),
        json_out: bool = typer.Option(False, "--json"),
        db: Optional[str] = typer.Option(None, "--db"),
    ):
        """Cmd diagnose tool failures helper."""
        svc = _get_service(db)
        report = diagnose_tool_failure_fact_poisoning(
            svc._store,  # noqa: SLF001
            scopes=[scope] if scope else None,
            tombstone_structured=tombstone_structured,
            limit=limit,
        )
        if json_out:
            _output(report.to_dict(), True)
            return
        typer.echo(render_tool_failure_fact_diagnostic_report(report))


def _register_write_commands(app: typer.Typer) -> None:
    @app.command("approve")
    def cmd_approve(
        candidate_id: str = typer.Argument(...),
        reviewer: str = typer.Option(..., help="Reviewer identifier"),
        note: Optional[str] = typer.Option(None),
        db: Optional[str] = typer.Option(None),
    ):
        """Approve a candidate."""
        svc = _get_service(db)
        try:
            updated = svc.candidate_update(
                candidate_id,
                {
                    "status": MEMORY_CANDIDATE_STATUS_APPROVED,
                    "review": _build_candidate_review(reviewer, note),
                },
            )
            typer.echo(f"Approved: {updated.candidate_id}")
        except Exception as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)

    @app.command("reject")
    def cmd_reject(
        candidate_id: str = typer.Argument(...),
        reviewer: str = typer.Option(...),
        note: Optional[str] = typer.Option(None),
        db: Optional[str] = typer.Option(None),
    ):
        """Reject a candidate."""
        svc = _get_service(db)
        try:
            updated = svc.candidate_update(
                candidate_id,
                {
                    "status": MEMORY_CANDIDATE_STATUS_REJECTED,
                    "review": _build_candidate_review(reviewer, note),
                },
            )
            typer.echo(f"Rejected: {updated.candidate_id}")
        except Exception as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)

    @app.command("promote-approved")
    def cmd_promote_approved(
        session_id: str = typer.Option(
            ..., help="Promote all approved candidates in session"
        ),
        target_scope: str = typer.Option(..., help="Target scope for promotion"),
        db: Optional[str] = typer.Option(None),
    ):
        """Promote all approved candidates in a session to a target scope."""
        svc = _get_service(db)
        opts = CandidateListOptions(
            session_id=session_id,
            status=MEMORY_CANDIDATE_STATUS_APPROVED,
        )
        candidates = svc.candidate_list(opts)
        promoted = 0
        failed = 0
        for c in candidates:
            try:
                svc.promote_candidate(c.candidate_id, target_scope)
                promoted += 1
            except Exception as e:
                typer.echo(f"  Failed {c.candidate_id}: {e}", err=True)
                failed += 1
        typer.echo(f"Promoted: {promoted}, Failed: {failed}")
        if failed:
            raise typer.Exit(1)

    @app.command("gc")
    def cmd_gc(
        db: Optional[str] = typer.Option(None),
        batch_size: int = typer.Option(500),
        json_out: bool = typer.Option(False, "--json"),
    ):
        """Run garbage collection and retention pass."""
        svc = _get_service(db)
        retention_config = None
        if not db:
            retention_config = load_config(env=dict(os.environ)).retention
        try:
            result = run_gc(
                svc._store,  # noqa: SLF001
                batch_size=batch_size,
                retention_config=retention_config,
            )
        except (AttributeError, TypeError) as exc:
            _fail_memory_command("gc", exc)
        _output(result, json_out)
        typer.echo(
            f"GC: deleted_records={result.deleted_records}, "
            f"deleted_candidates={result.deleted_candidates}"
        )

    @app.command("provenance")
    def cmd_provenance(
        session_id: str = typer.Option(None, "--session", help="Session ID"),
        turn_id: str = typer.Option(None, "--turn", help="Turn ID"),
        memory_id: str = typer.Option(None, "--memory", help="Memory ID"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """Show memory-retrieval provenance."""
        from openminion.modules.memory.runtime.provenance import (
            default_provenance_recorder,
        )

        recorder = default_provenance_recorder()
        if memory_id and (session_id or turn_id):
            typer.echo(
                "Error: pass either --memory OR (--session and --turn), not both",
                err=True,
            )
            raise typer.Exit(1)
        if memory_id:
            traces = recorder.find_traces_citing_memory(memory_id)
            payload = {
                "memory_id": memory_id,
                "trace_count": len(traces),
                "traces": [t.to_dict() for t in traces],
            }
            _output(payload, json_out)
            return
        if not (session_id and turn_id):
            typer.echo(
                "Error: --memory OR (--session and --turn) is required",
                err=True,
            )
            raise typer.Exit(1)
        trace = recorder.get_turn_trace(session_id=session_id, turn_id=turn_id)
        if trace is None:
            typer.echo(
                f"No provenance trace recorded for session={session_id} turn={turn_id}",
                err=True,
            )
            raise typer.Exit(1)
        _output(trace.to_dict(), json_out)

    @app.command("forget")
    def cmd_forget(
        record_id: str = typer.Option(
            None, "--memory", help="Memory record ID to forget"
        ),
        source: str = typer.Option(
            None, "--source", help="Source class to batch-forget"
        ),
        reason: str = typer.Option(None, "--reason", help="Operator-supplied reason"),
        apply: bool = typer.Option(
            False,
            "--apply",
            help="Apply the deletion (default is dry-run when --source is used)",
        ),
        db: str = typer.Option(None, help="Override DB path"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """Soft-delete a memory or batch-forget by source."""
        if not reason or not reason.strip():
            typer.echo("Error: --reason is required for any forget operation", err=True)
            raise typer.Exit(1)
        if bool(record_id) == bool(source):
            typer.echo(
                "Error: pass exactly one of --memory or --source",
                err=True,
            )
            raise typer.Exit(1)
        svc = _get_service(db)
        if record_id:
            try:
                ok = svc.delete_record(record_id, reason=reason)
            except Exception as exc:
                _fail_memory_command("forget", exc)
                return
            payload = {
                "mode": "memory",
                "memory_id": record_id,
                "deleted": bool(ok),
                "reason": reason,
            }
            _output(payload, json_out)
            return
        try:
            matched = svc.forget_by_source(source, reason=reason, dry_run=not apply)
        except Exception as exc:
            _fail_memory_command("forget", exc)
            return
        payload = {
            "mode": "source",
            "source": source,
            "matched_count": len(matched),
            "matched_ids": matched,
            "applied": bool(apply),
            "reason": reason,
        }
        _output(payload, json_out)


def _build_app() -> typer.Typer:
    app = typer.Typer(help="openminion-memory control surface")
    storage_app = typer.Typer(help="storage maintenance commands")
    trace_app = typer.Typer(help="memory trace commands")
    _register_root_callback(app)
    _register_storage_commands(storage_app)
    _register_trace_commands(trace_app)
    _register_read_commands(app)
    _register_write_commands(app)
    app.add_typer(storage_app, name="storage")
    app.add_typer(trace_app, name="trace")
    return app


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for `python -m openminion.modules.memory` and `memctl`."""

    argv = argv if argv is not None else sys.argv[1:]
    app = _build_app()
    app(prog_name="memctl", args=list(argv))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
