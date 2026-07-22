"""Human-reviewed memory import commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import typer

from openminion.modules.memory.portability.review_contracts import (
    MemoryReviewError,
    read_review_artifact,
    read_review_plan,
    read_review_receipt,
    write_review_document,
    write_review_markdown,
)

from openminion.modules.memory.portability.models import (
    MemoryBundleExportOptions,
    MemoryBundleImportOptions,
)

ServiceFactory = Callable[[str | None], Any]
_CLI_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


def _rewrites(values: list[str]) -> dict[str, str]:
    rewrites: dict[str, str] = {}
    for value in values:
        source, separator, target = str(value or "").partition("=")
        if not separator or not source.strip() or not target.strip():
            raise typer.BadParameter("scope rewrites must use source=target")
        rewrites[source.strip()] = target.strip()
    return rewrites


def _options(
    *,
    scope_rewrite: list[str],
    trust: str,
    conflict: str,
    id_mode: str,
) -> MemoryBundleImportOptions:
    return MemoryBundleImportOptions(
        scope_rewrites=_rewrites(scope_rewrite),
        trust_mode=trust,  # type: ignore[arg-type]
        conflict_mode=conflict,  # type: ignore[arg-type]
        id_mode=id_mode,  # type: ignore[arg-type]
    )


def _options_from_plan(plan) -> MemoryBundleImportOptions:
    payload = plan.options
    return MemoryBundleImportOptions(
        scope_rewrites=dict(payload.get("scope_rewrites", {})),
        trust_mode=str(payload.get("trust_mode", "direct")),  # type: ignore[arg-type]
        conflict_mode=str(payload.get("conflict_mode", "skip")),  # type: ignore[arg-type]
        id_mode=str(payload.get("id_mode", "preserve")),  # type: ignore[arg-type]
        dry_run=bool(payload.get("dry_run", False)),
    )


def _fail(exc: Exception) -> None:
    if isinstance(exc, MemoryReviewError):
        typer.echo(f"Error [{exc.reason_code}]: {exc}", err=True)
    else:
        typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(1)


def _register_export(review: typer.Typer, service_factory: ServiceFactory) -> None:
    @review.command("export")
    def export_command(
        scope: list[str] = typer.Option(..., "--scope"),
        out: Path = typer.Option(..., "--out"),
        markdown_out: Path | None = typer.Option(None, "--markdown-out"),
        include_candidates: bool = typer.Option(False, "--include-candidates"),
        include_tier_history: bool = typer.Option(False, "--include-tier-history"),
        include_provenance: bool = typer.Option(False, "--include-provenance"),
        db: str | None = typer.Option(None, "--db"),
    ) -> None:
        """Export a canonical JSON review artifact."""

        try:
            artifact = service_factory(db).export_review_artifact(
                MemoryBundleExportOptions(
                    scopes=scope,
                    include_candidates=include_candidates,
                    include_tier_history=include_tier_history,
                    include_provenance=include_provenance,
                )
            )
            write_review_document(artifact, out)
            if markdown_out is not None:
                write_review_markdown(artifact, markdown_out)
        except _CLI_ERRORS as exc:
            _fail(exc)
        typer.echo(f"artifact: {out.resolve(strict=False)}")
        typer.echo(f"sha256: {artifact.artifact_sha256}")


def _register_inspect(review: typer.Typer) -> None:
    @review.command("inspect")
    def inspect_command(
        artifact_path: Path = typer.Option(..., "--artifact"),
    ) -> None:
        """Inspect versions, digests, counts, and warnings without raw contents."""

        try:
            artifact = read_review_artifact(artifact_path)
        except _CLI_ERRORS as exc:
            _fail(exc)
        typer.echo(f"version: {artifact.version}")
        typer.echo(f"artifact_sha256: {artifact.artifact_sha256}")
        typer.echo(f"bundle_id: {artifact.bundle_id}")
        typer.echo(f"source: {artifact.source_backend} / {artifact.source_instance}")
        for summary in artifact.section_summaries:
            typer.echo(
                f"section.{summary.name}: {summary.count} ({summary.disposition})"
            )
        for warning in artifact.warnings:
            typer.echo(f"warning: {warning}")


def _register_plan(review: typer.Typer, service_factory: ServiceFactory) -> None:
    @review.command("plan")
    def plan_command(
        artifact_path: Path = typer.Option(..., "--artifact"),
        out: Path = typer.Option(..., "--out"),
        scope_rewrite: list[str] = typer.Option([], "--scope-rewrite"),
        trust: str = typer.Option("direct", "--trust"),
        conflict: str = typer.Option("skip", "--conflict"),
        id_mode: str = typer.Option("preserve", "--id-mode"),
        db: str | None = typer.Option(None, "--db"),
    ) -> None:
        """Create a no-write import plan for the current target."""

        try:
            artifact = read_review_artifact(artifact_path)
            plan = service_factory(db).plan_review_import(
                artifact,
                _options(
                    scope_rewrite=scope_rewrite,
                    trust=trust,
                    conflict=conflict,
                    id_mode=id_mode,
                ),
            )
            write_review_document(plan, out)
        except _CLI_ERRORS as exc:
            _fail(exc)
        typer.echo(f"plan: {out.resolve(strict=False)}")
        typer.echo(f"sha256: {plan.plan_sha256}")


def _register_decide(review: typer.Typer, service_factory: ServiceFactory) -> None:
    @review.command("decide")
    def decide_command(
        plan_path: Path = typer.Option(..., "--plan"),
        out: Path = typer.Option(..., "--out"),
        reviewer: str = typer.Option(..., "--reviewer"),
        decision: str = typer.Option(..., "--decision"),
        note: str | None = typer.Option(None, "--note"),
        db: str | None = typer.Option(None, "--db"),
    ) -> None:
        """Record an explicit approve or reject decision."""

        try:
            plan = read_review_plan(plan_path)
            receipt = service_factory(db).decide_review_import(
                plan,
                reviewer=reviewer,
                decision=decision,
                note=note,
            )
            write_review_document(receipt, out)
        except _CLI_ERRORS as exc:
            _fail(exc)
        typer.echo(f"receipt: {out.resolve(strict=False)}")
        typer.echo(f"decision: {receipt.decision}")


def _register_apply(review: typer.Typer, service_factory: ServiceFactory) -> None:
    @review.command("apply")
    def apply_command(
        artifact_path: Path = typer.Option(..., "--artifact"),
        plan_path: Path = typer.Option(..., "--plan"),
        receipt_path: Path = typer.Option(..., "--receipt"),
        db: str | None = typer.Option(None, "--db"),
    ) -> None:
        """Apply an approved, unchanged review plan."""

        try:
            artifact = read_review_artifact(artifact_path)
            plan = read_review_plan(plan_path)
            receipt = read_review_receipt(receipt_path)
            result = service_factory(db).apply_review_import(
                artifact,
                plan,
                receipt,
                _options_from_plan(plan),
            )
        except _CLI_ERRORS as exc:
            _fail(exc)
        typer.echo(f"applied: {result.applied}")
        typer.echo(f"imported_records: {result.imported_records}")
        typer.echo(f"staged_candidates: {result.staged_candidates}")


def register_review_commands(
    app: typer.Typer,
    *,
    service_factory: ServiceFactory,
) -> None:
    """Register the review subgroup on the canonical memory CLI."""

    review = typer.Typer(help="review memory changes before applying them")
    _register_export(review, service_factory)
    _register_inspect(review)
    _register_plan(review, service_factory)
    _register_decide(review, service_factory)
    _register_apply(review, service_factory)
    app.add_typer(review, name="review")


__all__ = ["register_review_commands"]
