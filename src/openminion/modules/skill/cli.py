from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from openminion.modules.skill.errors import SkillError
from openminion.modules.skill.runtime.skill import Skill
from openminion.modules.skill.config import load_config
from openminion.modules.skill.constants import (
    DEFAULT_CONFIG_FILENAME,
    RISK_CLASS_CHOICES,
    SKILL_STATUS_DEPRECATED,
)
from openminion.modules.cli_common import (
    add_common_module_root_args,
    apply_home_data_root_env,
    print_json_payload,
)
from openminion.modules.storage.module_cli import (
    add_storage_subcommands,
    run_module_storage_command,
)

_REPLAY_STATUS_CHOICES = ("passed", "failed", "blocked", "skipped")
_LEARNING_COMMANDS = frozenset(
    {
        "learning-scan",
        "learning-inspect",
        "learning-save-workflow",
        "learning-propose",
        "learning-replay-proof",
        "learning-apply-proved",
        "learning-trust-status",
    }
)


def _add_ingest_metadata_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--scope",
        default="global",
        choices=["global", "agent", "project"],
        help=(
            "Skill scope. 'project' is currently stored as a label only and "
            "does not affect runtime catalog visibility."
        ),
    )
    parser.add_argument("--agent-id", default=None)
    parser.add_argument(
        "--trust",
        default=None,
        choices=[
            "trusted_local",
            "trusted_remote",
            "untrusted_local",
            "untrusted_remote",
        ],
        help="Optional trust declaration persisted into bundle_metadata.trust.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    home_root = str(getattr(args, "home_root", "") or "").strip()
    data_root = str(getattr(args, "data_root", "") or "").strip()
    apply_home_data_root_env(home_root=home_root, data_root=data_root)
    home_root_path = Path(home_root).expanduser().resolve() if home_root else None

    if args.cmd == "storage":
        cfg = load_config(args.config, home_root=home_root_path, env=dict(os.environ))
        db_path = Path(cfg.sqlite_path).expanduser().resolve(strict=False)
        return run_module_storage_command(
            args=args,
            module_id="skill",
            db_path=db_path,
            home_root=home_root,
            data_root=data_root,
        )

    ctl = Skill(args.config, home_root=home_root_path)
    try:
        _dispatch(ctl, args)
    except SkillError as exc:
        _print_json({"ok": False, "error": exc.to_dict()})
        raise SystemExit(1)
    finally:
        ctl.close()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skill")
    add_common_module_root_args(parser)
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_FILENAME,
        help="Path to skill config",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    ingest = sub.add_parser("ingest", help="Ingest a markdown skill file")
    ingest.add_argument("--name", required=True)
    ingest.add_argument("--file", required=True)
    _add_ingest_metadata_args(ingest)

    ingest_text = sub.add_parser(
        "ingest-text", help="Ingest markdown text passed inline"
    )
    ingest_text.add_argument("--name", required=True)
    ingest_text.add_argument("--markdown", required=True)
    _add_ingest_metadata_args(ingest_text)

    show = sub.add_parser("show", help="Show a skill package")
    show.add_argument("skill_id")
    show.add_argument("--version", default=None)

    list_cmd = sub.add_parser("list", help="List skills")
    list_cmd.add_argument("--status", default=None, help="Comma-separated statuses")
    list_cmd.add_argument("--scope", default=None)
    list_cmd.add_argument("--agent-id", default=None)
    list_cmd.add_argument("--tag", default=None)
    list_cmd.add_argument("--tool", default=None)

    match = sub.add_parser("match", help="Match skills for an intent")
    match.add_argument("--intent", required=True)
    match.add_argument("--agent-id", required=True)
    match.add_argument("--tool-id", default=None)
    match.add_argument(
        "--risk", default=RISK_CLASS_CHOICES[0], choices=list(RISK_CLASS_CHOICES)
    )
    match.add_argument("--verify", action="store_true")
    match.add_argument("--k", type=int, default=3)
    match.add_argument("--status-filter", default=None, help="Comma-separated statuses")

    snippet = sub.add_parser("snippet", help="Render purpose-specific skill snippet")
    snippet.add_argument("skill_id")
    snippet.add_argument("--version", default=None)
    snippet.add_argument("--purpose", required=True, choices=["plan", "act", "verify"])
    snippet.add_argument("--max-tokens", type=int, default=180)

    lint = sub.add_parser("lint", help="Lint a skill")
    lint.add_argument("skill_id")
    lint.add_argument("--version", default=None)

    validate = sub.add_parser(
        "validate",
        help=(
            "Emit typed SkillValidationReport (typed lint surface only; "
            "use 'openminion skill validate' for harness-backed composition)."
        ),
    )
    validate.add_argument("skill_id")
    validate.add_argument("--version", default=None)

    test_cmd = sub.add_parser(
        "test",
        help="Emit typed SkillTestReport over the skill harness for a skill root",
    )
    test_cmd.add_argument(
        "skill_root", help="Filesystem skill root containing SKILL.md"
    )
    test_cmd.add_argument(
        "--regression-ref",
        action="append",
        default=[],
        help="Regression reference (repeatable).",
    )

    debug_cmd = sub.add_parser(
        "debug",
        help="Emit typed SkillAuthoringDebugView for a skill",
    )
    debug_cmd.add_argument("skill_id")
    debug_cmd.add_argument("--version", default=None)

    inspect_cmd = sub.add_parser(
        "inspect", help="Inspect a skill package (SLV2-04 alias for show)"
    )
    inspect_cmd.add_argument("skill_id")
    inspect_cmd.add_argument("--version", default=None)

    disable_cmd = sub.add_parser(
        "disable",
        help=(
            "Disable a skill — sets its status to 'deprecated' so the runtime "
            "catalog drops it. Requires --reason."
        ),
    )
    disable_cmd.add_argument("skill_id")
    disable_cmd.add_argument(
        "--reason",
        required=True,
        help=(
            "Operator-supplied justification for disabling the skill. "
            "Recorded with the disable event."
        ),
    )

    remove_cmd = sub.add_parser(
        "remove",
        help=(
            "Remove a skill from the catalog. Dry-run by default — pass "
            "--apply to actually delete. Requires --reason."
        ),
    )
    remove_cmd.add_argument("skill_id")
    remove_cmd.add_argument(
        "--reason",
        required=True,
        help=(
            "Operator-supplied justification for removing the skill. "
            "Recorded with the remove event."
        ),
    )
    remove_cmd.add_argument("--version", default=None)
    remove_cmd.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Actually perform the deletion. Without --apply the command "
            "is a dry-run that prints what would be removed."
        ),
    )

    log_run = sub.add_parser("log-run", help="Record skill run outcome")
    log_run.add_argument("--session-id", required=True)
    log_run.add_argument("--agent-id", required=True)
    log_run.add_argument("--skill-id", required=True)
    log_run.add_argument("--version", required=True)
    log_run.add_argument("--used-for", required=True, choices=["plan", "act", "verify"])
    log_run.add_argument(
        "--outcome", required=True, choices=["success", "fail", "partial"]
    )
    log_run.add_argument("--evidence", default="", help="Comma-separated evidence refs")

    proposal_list = sub.add_parser(
        "proposal-list",
        help="List persisted skill proposals (defaults to pending).",
    )
    proposal_list.add_argument(
        "--queue-state",
        default="pending",
        choices=["pending", "reviewed", "applied", "all"],
        help="Filter by queue state; 'all' returns every state.",
    )
    proposal_list.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of rows to return (1-500).",
    )

    proposal_inspect = sub.add_parser(
        "proposal-inspect",
        help="Show one persisted proposal (with review/apply state).",
    )
    proposal_inspect.add_argument("proposal_id")

    proposal_review = sub.add_parser(
        "proposal-review",
        help=(
            "Record an operator review for one proposal. Requires "
            "--reviewer-id and at least one --criterion."
        ),
    )
    proposal_review.add_argument("proposal_id")
    proposal_review.add_argument(
        "--reviewer-id",
        required=True,
        help=(
            "Operator-supplied reviewer id. Runtime reviewer ids "
            "('runtime', 'system', 'auto', 'automatic', 'self') are rejected."
        ),
    )
    proposal_review.add_argument(
        "--review-policy-id",
        default="",
        help="Optional operator review policy id (free-form text).",
    )
    proposal_review.add_argument(
        "--criterion",
        action="append",
        default=[],
        required=True,
        help=(
            "Per-criterion decision, in 'criterion_id:status:comment' form. "
            "Status must be one of accepted|rejected|deferred. Repeatable."
        ),
    )

    proposal_apply = sub.add_parser(
        "proposal-apply",
        help=(
            "Apply an accepted proposal to the catalog via the shipped "
            "apply_emergent_skill() seam."
        ),
    )
    proposal_apply.add_argument("proposal_id")

    _add_learning_subcommands(sub)

    suggestion_inbox = sub.add_parser(
        "suggestion-inbox",
        help="List currently-pending proposals as operator suggestions.",
    )
    suggestion_inbox.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of suggestions to return (1-500).",
    )

    sub.add_parser(
        "suggestion-status",
        help=(
            "Show typed suggestion-status payload: surfaced/accepted/"
            "rejected/deferred/auto-dismissed counts."
        ),
    )

    suggestion_surface = sub.add_parser(
        "suggestion-surface-pass",
        help=(
            "Run one structural surface pass — applies cooldown, batch cap, "
            "and dedup; writes audit rows."
        ),
    )
    suggestion_surface.add_argument(
        "--batch-cap",
        type=int,
        default=None,
        help="Override the surface pass batch cap (1-50).",
    )
    suggestion_surface.add_argument(
        "--cooldown-seconds",
        type=int,
        default=None,
        help="Override the per-signature cooldown window in seconds.",
    )

    add_storage_subcommands(sub)

    return parser


def _add_learning_subcommands(sub: Any) -> None:
    learning_scan = sub.add_parser(
        "learning-scan",
        help="Mine workflow shapes from a JSON list of evidence bundles.",
    )
    learning_scan.add_argument("--bundle-json", required=True)

    learning_inspect = sub.add_parser(
        "learning-inspect",
        help="Inspect one workflow-shape JSON file.",
    )
    learning_inspect.add_argument("--shape-json", required=True)

    learning_save = sub.add_parser(
        "learning-save-workflow",
        help="Emit an explicit save-workflow signal for one shape.",
    )
    learning_save.add_argument("--shape-json", required=True)
    learning_save.add_argument("--actor-id", required=True)
    learning_save.add_argument("--source-run-ref", default="")

    learning_propose = sub.add_parser(
        "learning-propose",
        help="Stage a workflow shape through the existing proposal queue.",
    )
    learning_propose.add_argument("--shape-json", required=True)

    learning_replay = sub.add_parser(
        "learning-replay-proof",
        help="Emit a deterministic replay proof payload.",
    )
    learning_replay.add_argument("--proposal-id", required=True)
    learning_replay.add_argument("--shape-id", required=True)
    learning_replay.add_argument("--proof-id", required=True)
    learning_replay.add_argument(
        "--status", required=True, choices=_REPLAY_STATUS_CHOICES
    )
    learning_replay.add_argument("--evidence", default="")

    learning_apply = sub.add_parser(
        "learning-apply-proved",
        help="Apply an accepted proposal only when replay proof passed.",
    )
    learning_apply.add_argument("--proposal-id", required=True)
    learning_apply.add_argument("--shape-id", required=True)
    learning_apply.add_argument("--proof-id", required=True)
    learning_apply.add_argument(
        "--proof-status",
        required=True,
        choices=_REPLAY_STATUS_CHOICES,
    )
    learning_apply.add_argument("--evidence", default="")

    learning_trust = sub.add_parser(
        "learning-trust-status",
        help="Show initial execution-trust status for a learned skill.",
    )
    learning_trust.add_argument("--skill-id", required=True)
    learning_trust.add_argument("--shape-id", required=True)


def _dispatch(ctl: Skill, args: argparse.Namespace) -> None:
    if args.cmd == "ingest":
        skill_id, version_hash, warnings = ctl.ingest_file(
            args.file,
            name=args.name,
            scope=args.scope,
            agent_id=args.agent_id,
            trust=getattr(args, "trust", None),
            promotion_path="operator",
        )
        _print_json(
            {
                "ok": True,
                "skill_id": skill_id,
                "version_hash": version_hash,
                "warnings": warnings,
            }
        )
        return

    if args.cmd == "ingest-text":
        skill_id, version_hash, warnings = ctl.ingest_text(
            name=args.name,
            markdown=args.markdown,
            scope=args.scope,
            agent_id=args.agent_id,
            trust=getattr(args, "trust", None),
            promotion_path="operator",
        )
        _print_json(
            {
                "ok": True,
                "skill_id": skill_id,
                "version_hash": version_hash,
                "warnings": warnings,
            }
        )
        return

    if args.cmd == "show":
        package = ctl.get_skill(args.skill_id, args.version)
        _print_json({"ok": True, "skill": package.to_dict()})
        return

    if args.cmd == "list":
        filters: dict[str, Any] = {
            "scope": args.scope,
            "agent_id": args.agent_id,
            "tag": args.tag,
            "tool": args.tool,
        }
        if args.status:
            filters["status"] = [
                item.strip() for item in args.status.split(",") if item.strip()
            ]

        skills = ctl.list_skills(filters)
        _print_json({"ok": True, "skills": skills})
        return

    if args.cmd == "match":
        status_filter = None
        if args.status_filter:
            status_filter = [
                item.strip() for item in args.status_filter.split(",") if item.strip()
            ]

        step_hint: dict[str, Any] = {
            "risk": args.risk,
            "verify": bool(args.verify),
        }
        if args.tool_id:
            step_hint["tool_id"] = args.tool_id

        matches = ctl.match(
            intent_text=args.intent,
            step_hint=step_hint,
            agent_id=args.agent_id,
            k=args.k,
            status_filter=status_filter,
        )
        _print_json({"ok": True, "matches": [item.to_dict() for item in matches]})
        return

    if args.cmd == "snippet":
        text, snippet_hash = ctl.render_snippet(
            skill_id=args.skill_id,
            version_hash=args.version,
            purpose=args.purpose,
            max_tokens=int(args.max_tokens),
        )
        _print_json({"ok": True, "snippet": text, "snippet_hash": snippet_hash})
        return

    if args.cmd == "lint":
        report = ctl.lint(args.skill_id, args.version)
        _print_json({"ok": True, "report": report})
        return

    if args.cmd == "validate":
        from openminion.modules.skill.authoring import (
            build_skill_validation_report,
        )

        package = ctl.get_skill(args.skill_id, args.version)
        lint_report = ctl.lint(args.skill_id, args.version)
        report = build_skill_validation_report(
            package,
            lint_report=lint_report,
            harness_result=None,
        )
        _print_json({"ok": True, "report": report.to_dict()})
        return

    if args.cmd == "test":
        from openminion.modules.skill.authoring import build_skill_test_report

        report = build_skill_test_report(
            args.skill_root,
            harness_report=None,
            regression_refs=tuple(args.regression_ref or ()),
        )
        _print_json({"ok": True, "report": report.to_dict()})
        return

    if args.cmd == "debug":
        from openminion.modules.skill.authoring import (
            build_skill_authoring_debug_view,
        )

        package = ctl.get_skill(args.skill_id, args.version)
        debug_payload = {
            "module": "openminion-skill",
            "status": "ok",
            "last_error": None,
        }
        view = build_skill_authoring_debug_view(
            args.skill_id,
            package=package,
            debug_payload=debug_payload,
        )
        _print_json({"ok": True, "view": view.to_dict()})
        return

    if args.cmd == "inspect":
        package = ctl.get_skill(args.skill_id, args.version)
        _print_json({"ok": True, "skill": package.to_dict()})
        return

    if args.cmd == "disable":
        reason = str(getattr(args, "reason", "") or "").strip()
        if not reason:
            raise SkillError(
                "INVALID_ARGUMENT",
                "--reason is required for disable",
            )
        package = ctl.get_skill(args.skill_id, None)
        updated = ctl.set_skill_status(
            skill_id=package.skill_id,
            new_status=SKILL_STATUS_DEPRECATED,
            promotion_path="operator",
        )
        _print_json(
            {
                "ok": True,
                "disabled": {
                    "skill_id": package.skill_id,
                    "previous_status": package.status,
                    "new_status": SKILL_STATUS_DEPRECATED,
                    "reason": reason,
                    "disabled_at": updated.updated_at,
                },
            }
        )
        return

    if args.cmd == "remove":
        reason = str(getattr(args, "reason", "") or "").strip()
        if not reason:
            raise SkillError(
                "INVALID_ARGUMENT",
                "--reason is required for remove",
            )
        package = ctl.get_skill(args.skill_id, args.version)
        if not args.apply:
            _print_json(
                {
                    "ok": True,
                    "dry_run": True,
                    "would_remove": {
                        "skill_id": package.skill_id,
                        "version_hash": package.version_hash if args.version else None,
                        "reason": reason,
                    },
                }
            )
            return
        counts = ctl.delete_skill(
            skill_id=args.skill_id,
            version_hash=args.version,
        )
        _print_json(
            {
                "ok": True,
                "dry_run": False,
                "removed": {
                    "skill_id": package.skill_id,
                    "version_hash": package.version_hash if args.version else None,
                    "reason": reason,
                    "deleted_counts": counts,
                },
            }
        )
        return

    if args.cmd == "log-run":
        evidence = [item.strip() for item in args.evidence.split(",") if item.strip()]
        run_id = ctl.log_run(
            session_id=args.session_id,
            agent_id=args.agent_id,
            skill_id=args.skill_id,
            version_hash=args.version,
            used_for=args.used_for,
            outcome=args.outcome,
            evidence_refs=evidence,
        )
        _print_json({"ok": True, "run_id": run_id})
        return

    if args.cmd in {
        "proposal-list",
        "proposal-inspect",
        "proposal-review",
        "proposal-apply",
    }:
        _dispatch_proposal_cmd(ctl, args)
        return

    if args.cmd in _LEARNING_COMMANDS:
        _dispatch_learning_cmd(ctl, args)
        return

    if args.cmd in {"suggestion-inbox", "suggestion-status", "suggestion-surface-pass"}:
        _dispatch_suggestion_cmd(ctl, args)
        return

    raise SkillError("INVALID_ARGUMENT", "Unsupported command")


def _dispatch_suggestion_cmd(ctl: Skill, args: argparse.Namespace) -> None:
    from openminion.modules.skill.suggestion import (
        DEFAULT_SUGGESTION_BATCH_CAP,
        DEFAULT_SUGGESTION_COOLDOWN_SECONDS,
        list_active_suggestions,
        run_suggestion_surface_pass,
        suggestion_status,
    )

    if args.cmd == "suggestion-inbox":
        limit = max(1, min(500, int(args.limit)))
        rows = list_active_suggestions(ctl.store, limit=limit)
        _print_json(
            {
                "ok": True,
                "suggestions": [row.to_dict() for row in rows],
            }
        )
        return

    if args.cmd == "suggestion-status":
        status = suggestion_status(ctl.store)
        _print_json({"ok": True, "status": status.to_dict()})
        return

    if args.cmd == "suggestion-surface-pass":
        cap = (
            DEFAULT_SUGGESTION_BATCH_CAP
            if args.batch_cap is None
            else max(1, min(50, int(args.batch_cap)))
        )
        cooldown = (
            DEFAULT_SUGGESTION_COOLDOWN_SECONDS
            if args.cooldown_seconds is None
            else max(0, int(args.cooldown_seconds))
        )
        report = run_suggestion_surface_pass(
            ctl.store,
            batch_cap=cap,
            cooldown_seconds=cooldown,
        )
        _print_json(
            {
                "ok": True,
                "surfaced": [row.to_dict() for row in report.surfaced],
                "auto_dismissed": list(report.auto_dismissed),
                "pending_remaining": int(report.pending_remaining),
            }
        )
        return

    raise SkillError("INVALID_ARGUMENT", "Unsupported suggestion command")


def _dispatch_proposal_cmd(ctl: Skill, args: argparse.Namespace) -> None:
    from openminion.modules.skill.proposal.queue import (
        ProposalQueueError,
        apply_proposal,
        get_proposal,
        list_proposals,
        record_proposal_review,
    )

    if args.cmd == "proposal-list":
        queue_state = None if args.queue_state == "all" else str(args.queue_state)
        limit = max(1, min(500, int(args.limit)))
        try:
            rows = list_proposals(ctl.store, queue_state=queue_state, limit=limit)
        except ProposalQueueError as exc:
            raise SkillError("INVALID_ARGUMENT", str(exc)) from exc
        _print_json({"ok": True, "proposals": rows})
        return

    if args.cmd == "proposal-inspect":
        try:
            record = get_proposal(ctl.store, proposal_id=args.proposal_id)
        except ProposalQueueError as exc:
            raise SkillError("INVALID_ARGUMENT", str(exc)) from exc
        if record is None:
            raise SkillError(
                "NOT_FOUND",
                "Proposal not found",
                {"proposal_id": args.proposal_id},
            )
        _print_json({"ok": True, "proposal": record})
        return

    if args.cmd == "proposal-review":
        criteria = _parse_criterion_args(args.criterion)
        if not criteria:
            raise SkillError(
                "INVALID_ARGUMENT",
                "at least one --criterion is required",
            )
        try:
            review = record_proposal_review(
                ctl.store,
                proposal_id=args.proposal_id,
                reviewer_id=args.reviewer_id,
                review_policy_id=args.review_policy_id,
                criterion_decisions=criteria,
            )
        except ProposalQueueError as exc:
            raise SkillError("INVALID_ARGUMENT", str(exc)) from exc
        except ValueError as exc:
            raise SkillError("INVALID_ARGUMENT", str(exc)) from exc
        _print_json(
            {
                "ok": True,
                "review": review.model_dump(mode="json"),
                "proposal_id": args.proposal_id,
            }
        )
        return

    if args.cmd == "proposal-apply":
        catalog_rows = ctl.list_skills({}) or []
        try:
            addition = apply_proposal(
                ctl.store,
                proposal_id=args.proposal_id,
                current_catalog=catalog_rows,
            )
        except ProposalQueueError as exc:
            raise SkillError("INVALID_ARGUMENT", str(exc)) from exc
        _print_json(
            {
                "ok": True,
                "addition": addition.model_dump(mode="json"),
            }
        )
        return

    raise SkillError("INVALID_ARGUMENT", "Unsupported proposal command")


def _dispatch_learning_cmd(ctl: Skill, args: argparse.Namespace) -> None:
    from openminion.modules.skill.learning import (
        SkillExecutionTrustRecord,
        WorkflowEvidenceBundle,
        WorkflowShape,
        WorkflowShapeMiner,
        apply_proposal_with_replay,
        stage_shape_as_skill_proposal,
    )
    from openminion.modules.skill.learning.replay import ReplayGateError

    if args.cmd == "learning-scan":
        raw = _read_json_path(args.bundle_json)
        bundles = [
            WorkflowEvidenceBundle.model_validate(item)
            for item in (raw if isinstance(raw, list) else [raw])
        ]
        shapes = WorkflowShapeMiner().mine(bundles)
        _print_json(
            {
                "ok": True,
                "shapes": [shape.model_dump(mode="json") for shape in shapes],
            }
        )
        return

    if args.cmd == "learning-inspect":
        shape = WorkflowShape.model_validate(_read_json_path(args.shape_json))
        _print_json({"ok": True, "shape": shape.model_dump(mode="json")})
        return

    if args.cmd == "learning-save-workflow":
        shape = WorkflowShape.model_validate(_read_json_path(args.shape_json))
        actor_id = str(args.actor_id or "").strip()
        if not actor_id:
            raise SkillError("INVALID_ARGUMENT", "--actor-id is required")
        _print_json(
            {
                "ok": True,
                "save_workflow": {
                    "shape_id": shape.shape_id,
                    "actor_id": actor_id,
                    "source_run_ref": str(args.source_run_ref or ""),
                    "explicit_save": True,
                },
            }
        )
        return

    if args.cmd == "learning-propose":
        shape = WorkflowShape.model_validate(_read_json_path(args.shape_json))
        result = stage_shape_as_skill_proposal(
            shape,
            store=ctl.store,
            current_catalog=ctl.list_skills({}) or [],
        )
        _print_json({"ok": True, "result": result.model_dump(mode="json")})
        return

    if args.cmd == "learning-replay-proof":
        proof = _learning_replay_proof_from_args(
            proposal_id=args.proposal_id,
            shape_id=args.shape_id,
            proof_id=args.proof_id,
            status=args.status,
            evidence=args.evidence,
        )
        _print_json({"ok": True, "proof": proof.model_dump(mode="json")})
        return

    if args.cmd == "learning-apply-proved":
        proof = _learning_replay_proof_from_args(
            proposal_id=args.proposal_id,
            shape_id=args.shape_id,
            proof_id=args.proof_id,
            status=args.proof_status,
            evidence=args.evidence,
        )
        try:
            addition = apply_proposal_with_replay(
                ctl.store,
                proposal_id=args.proposal_id,
                current_catalog=ctl.list_skills({}) or [],
                replay_proof=proof,
            )
        except (ReplayGateError, ValueError) as exc:
            raise SkillError("INVALID_ARGUMENT", str(exc)) from exc
        _print_json({"ok": True, "addition": addition.model_dump(mode="json")})
        return

    if args.cmd == "learning-trust-status":
        record = SkillExecutionTrustRecord(
            skill_id=args.skill_id,
            shape_id=args.shape_id,
        )
        _print_json({"ok": True, "trust": record.model_dump(mode="json")})
        return

    raise SkillError("INVALID_ARGUMENT", "Unsupported learning command")


def _read_json_path(path: str) -> Any:
    text = Path(path).read_text(encoding="utf-8")
    return json.loads(text)


def _learning_replay_proof_from_args(
    *,
    proposal_id: str,
    shape_id: str,
    proof_id: str,
    status: str,
    evidence: str,
) -> Any:
    from openminion.modules.skill.learning import ReplayProof

    evidence_refs = [item.strip() for item in evidence.split(",") if item.strip()]
    return ReplayProof(
        proof_id=proof_id,
        proposal_id=proposal_id,
        shape_id=shape_id,
        status=status,
        evidence_refs=evidence_refs,
    )


def _parse_criterion_args(raw_values: list[str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for raw in raw_values or []:
        text = str(raw or "").strip()
        if not text:
            continue
        parts = text.split(":", 2)
        if len(parts) != 3:
            raise SkillError(
                "INVALID_ARGUMENT",
                "--criterion must be 'criterion_id:status:comment'",
                {"criterion": text},
            )
        criterion_id, status, comment = (
            parts[0].strip(),
            parts[1].strip(),
            parts[2].strip(),
        )
        if not criterion_id or not comment:
            raise SkillError(
                "INVALID_ARGUMENT",
                "--criterion id and comment must be non-empty",
                {"criterion": text},
            )
        if status not in {"accepted", "rejected", "deferred"}:
            raise SkillError(
                "INVALID_ARGUMENT",
                "--criterion status must be one of accepted|rejected|deferred",
                {"criterion": text},
            )
        out.append(
            {
                "criterion_id": criterion_id,
                "status": status,
                "comment": comment,
            }
        )
    return out


def _print_json(payload: dict[str, Any]) -> None:
    print_json_payload(payload, sort_keys=False, ensure_ascii=True)


if __name__ == "__main__":
    raise SystemExit(main())
