from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

from openminion.cli.presentation.json_output import print_json_payload
from openminion.modules.brain.improvement import (
    ImprovementCandidate,
    InstructionApprovalRecord,
    InstructionOpportunity,
    InstructionProposalEvent,
    InstructionProposalStore,
    InstructionTargetSnapshot,
    apply_instruction_proposal,
    build_instruction_proposal,
    reject_instruction_proposal,
    rollback_instruction_proposal,
)
from openminion.modules.runtime.project_instructions import (
    resolve_project_instruction_target,
)


def run_project_learning(args: argparse.Namespace) -> int:
    store = _store_from_args(args)
    try:
        command = str(args.project_learning_command)
        if command == "stage-opportunity":
            return _stage_opportunity(args, store)
        if command == "stage-proposal":
            return _stage_proposal(args, store)
        if command == "author-handoff":
            return _author_handoff(args, store)
        if command == "list":
            return _list_proposals(args, store)
        if command == "inspect":
            return _inspect(args, store)
        if command == "approve":
            return _approve(args, store)
        if command == "apply":
            return _apply(args, store)
        if command == "reject":
            return _reject(args, store)
        if command == "rollback":
            return _rollback(args, store)
    except (KeyError, ValueError) as exc:
        print_json_payload({"ok": False, "error": str(exc)}, stream=sys.stderr)
        return 2
    print_json_payload({"ok": False, "error": f"unknown command: {command}"})
    return 2


def _stage_opportunity(
    args: argparse.Namespace,
    store: InstructionProposalStore,
) -> int:
    opportunity = InstructionOpportunity(
        opportunity_id=args.opportunity_id or f"opp-{uuid.uuid4()}",
        source_kind=args.source_kind,
        evidence_refs=list(args.evidence_ref or []),
        observed_count=args.observed_count,
        target_hint=args.target_hint or "",
    )
    store.stage_opportunity(opportunity)
    store.append_event(
        InstructionProposalEvent(
            event_id=f"plip-event-{uuid.uuid4()}",
            event_type="instruction.opportunity_staged",
            candidate_id="",
            evidence_refs=list(opportunity.evidence_refs),
        )
    )
    print_json_payload({"ok": True, "opportunity": opportunity.model_dump(mode="json")})
    return 0


def _stage_proposal(args: argparse.Namespace, store: InstructionProposalStore) -> int:
    target = resolve_project_instruction_target(args.dir, target_name=args.target_name)
    candidate_id = args.candidate_id or f"instruction-{uuid.uuid4()}"
    text = _text_from_args(args)
    proposal = build_instruction_proposal(
        candidate_id=candidate_id,
        opportunity_id=args.opportunity_id or "",
        target_file=str(target.path),
        target_name=target.target_name,
        proposal_kind=args.proposal_kind,
        summary=args.summary,
        evidence_refs=list(args.evidence_ref or []),
        author_source=args.author_source,
        suggested_text=text,
        suggested_patch=args.suggested_patch or "",
        target_content_hash=target.content_hash,
        risk_level=args.risk_level,
        validation_hint=args.validation_hint or "",
    )
    candidate = ImprovementCandidate(
        candidate_id=proposal.candidate_id,
        target_type="instruction",
        target_owner="project_instructions",
        summary=proposal.summary,
        evidence_refs=list(proposal.evidence_refs),
        risk_level=proposal.risk_level,
        review_mode=proposal.review_mode,
        replay_eval_requirements=(
            [proposal.validation_hint] if proposal.validation_hint else []
        ),
        source=f"instruction:{proposal.author_source}",
    )
    store.stage_proposal(
        proposal,
        snapshot=InstructionTargetSnapshot(
            target_file=str(target.path),
            target_name=target.target_name,
            project_root=str(target.project_root),
            content_hash=target.content_hash,
            newline=target.newline,
            encoding=target.encoding,
            mode=target.mode,
            content=target.content,
        ),
        candidate=candidate,
    )
    _emit_stage_events(store, proposal.candidate_id, proposal.target_file)
    print_json_payload({"ok": True, "proposal": proposal.model_dump(mode="json")})
    return 0


def _author_handoff(args: argparse.Namespace, store: InstructionProposalStore) -> int:
    opportunity = store.get_opportunity(args.opportunity_id)
    if opportunity is None:
        raise KeyError(args.opportunity_id)
    target = resolve_project_instruction_target(args.dir, target_name=args.target_name)
    print_json_payload(
        {
            "ok": True,
            "handoff": {
                "opportunity": opportunity.model_dump(mode="json"),
                "target": {
                    "target_file": str(target.path),
                    "target_name": target.target_name,
                    "target_content_hash": target.content_hash,
                    "exists": target.exists,
                },
                "authoring_contract": {
                    "allowed_author_sources": ["llm", "operator", "imported"],
                    "allowed_proposal_kinds": [
                        "append_section",
                        "replace_section",
                        "append_bullet",
                        "manual_review",
                    ],
                    "runtime_may_not_infer_instruction_text": True,
                },
            },
        }
    )
    return 0


def _list_proposals(args: argparse.Namespace, store: InstructionProposalStore) -> int:
    proposals = store.list_proposals()
    print_json_payload(
        {
            "ok": True,
            "count": len(proposals),
            "proposals": [item.model_dump(mode="json") for item in proposals],
        }
    )
    return 0


def _inspect(args: argparse.Namespace, store: InstructionProposalStore) -> int:
    proposal = store.get_proposal(args.candidate_id)
    if proposal is None:
        raise KeyError(args.candidate_id)
    print_json_payload(
        {
            "ok": True,
            "proposal": proposal.model_dump(mode="json"),
            "candidate": _dump_optional(store.get_candidate(args.candidate_id)),
            "snapshot": _dump_optional(store.get_snapshot(args.candidate_id)),
            "rollback": _dump_optional(store.get_rollback(args.candidate_id)),
        }
    )
    return 0


def _approve(args: argparse.Namespace, store: InstructionProposalStore) -> int:
    if not args.yes:
        raise ValueError("trusted_approval_required")
    proposal = store.get_proposal(args.candidate_id)
    if proposal is None:
        raise KeyError(args.candidate_id)
    approval = InstructionApprovalRecord(
        approval_id=args.approval_id or f"approval-{uuid.uuid4()}",
        candidate_id=proposal.candidate_id,
        proposal_hash=proposal.proposal_hash,
        target_file=proposal.target_file,
        target_content_hash=proposal.target_content_hash,
        actor_id=args.actor_id,
        session_id=args.session_id,
        approval_source="cli_confirm",
    )
    store.approve(approval)
    store.update_state(proposal.candidate_id, "under_review")
    store.append_event(
        InstructionProposalEvent(
            event_id=f"plip-event-{uuid.uuid4()}",
            event_type="instruction.approval_issued",
            candidate_id=proposal.candidate_id,
            approval_id=approval.approval_id,
            target_file=proposal.target_file,
            state="under_review",
        )
    )
    print_json_payload({"ok": True, "approval": approval.model_dump(mode="json")})
    return 0


def _apply(args: argparse.Namespace, store: InstructionProposalStore) -> int:
    proposal = apply_instruction_proposal(store, approval_id=args.approval_id)
    print_json_payload({"ok": True, "proposal": proposal.model_dump(mode="json")})
    return 0


def _reject(args: argparse.Namespace, store: InstructionProposalStore) -> int:
    proposal = reject_instruction_proposal(
        store,
        candidate_id=args.candidate_id,
        reason_code=args.reason_code,
    )
    print_json_payload({"ok": True, "proposal": proposal.model_dump(mode="json")})
    return 0


def _rollback(args: argparse.Namespace, store: InstructionProposalStore) -> int:
    proposal = rollback_instruction_proposal(store, candidate_id=args.candidate_id)
    print_json_payload({"ok": True, "proposal": proposal.model_dump(mode="json")})
    return 0


def _store_from_args(args: argparse.Namespace) -> InstructionProposalStore:
    if args.store:
        return InstructionProposalStore(args.store)
    return InstructionProposalStore.default(
        home_root=getattr(args, "home_root", None) or ".",
        data_root=getattr(args, "data_root", None),
    )


def _text_from_args(args: argparse.Namespace) -> str:
    if args.text_file:
        return Path(args.text_file).read_text(encoding="utf-8")
    return str(args.text or "")


def _emit_stage_events(
    store: InstructionProposalStore,
    candidate_id: str,
    target_file: str,
) -> None:
    for event_type in (
        "instruction.proposal_authored",
        "instruction.proposal_staged",
    ):
        store.append_event(
            InstructionProposalEvent(
                event_id=f"plip-event-{uuid.uuid4()}",
                event_type=event_type,
                candidate_id=candidate_id,
                target_file=target_file,
                state="staged",
            )
        )


def _dump_optional(value: object | None) -> dict | None:
    if value is None:
        return None
    model_dump = getattr(value, "model_dump", None)
    if not callable(model_dump):
        return None
    dumped = model_dump(mode="json")
    return dumped if isinstance(dumped, dict) else None


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "project-learning",
        help="Review and apply project instruction learning proposals",
    )
    parser.add_argument("--store", default=None, help="Instruction proposal store path")
    commands = parser.add_subparsers(dest="project_learning_command", required=True)

    stage_opp = commands.add_parser(
        "stage-opportunity",
        help="Stage a structured instruction-learning opportunity",
    )
    stage_opp.add_argument("--opportunity-id", default=None)
    stage_opp.add_argument("--source-kind", required=True)
    stage_opp.add_argument("--evidence-ref", action="append", default=[])
    stage_opp.add_argument("--observed-count", type=int, default=1)
    stage_opp.add_argument("--target-hint", default="")
    stage_opp.set_defaults(handler=run_project_learning, needs_app=False)

    stage = commands.add_parser("stage-proposal", help="Stage an authored proposal")
    stage.add_argument("--dir", default=".")
    stage.add_argument("--candidate-id", default=None)
    stage.add_argument("--opportunity-id", default="")
    stage.add_argument("--target-name", default=None)
    stage.add_argument("--proposal-kind", required=True)
    stage.add_argument("--summary", required=True)
    stage.add_argument("--evidence-ref", action="append", default=[])
    stage.add_argument("--author-source", required=True)
    stage.add_argument("--text", default="")
    stage.add_argument("--text-file", default=None)
    stage.add_argument("--suggested-patch", default="")
    stage.add_argument("--risk-level", default="medium")
    stage.add_argument("--validation-hint", default="")
    stage.set_defaults(handler=run_project_learning, needs_app=False)

    handoff = commands.add_parser(
        "author-handoff",
        help="Render structured opportunity data for LLM/operator authoring",
    )
    handoff.add_argument("opportunity_id")
    handoff.add_argument("--dir", default=".")
    handoff.add_argument("--target-name", default=None)
    handoff.set_defaults(handler=run_project_learning, needs_app=False)

    for name, help_text in (
        ("list", "List staged proposals"),
        ("inspect", "Inspect one staged proposal"),
    ):
        cmd = commands.add_parser(name, help=help_text)
        if name == "inspect":
            cmd.add_argument("candidate_id")
        cmd.set_defaults(handler=run_project_learning, needs_app=False)

    approve = commands.add_parser("approve", help="Issue a trusted CLI approval")
    approve.add_argument("candidate_id")
    approve.add_argument("--approval-id", default=None)
    approve.add_argument("--actor-id", required=True)
    approve.add_argument("--session-id", required=True)
    approve.add_argument("--yes", action="store_true")
    approve.set_defaults(handler=run_project_learning, needs_app=False)

    apply_cmd = commands.add_parser("apply", help="Apply an approved proposal")
    apply_cmd.add_argument("approval_id")
    apply_cmd.set_defaults(handler=run_project_learning, needs_app=False)

    reject = commands.add_parser("reject", help="Reject a proposal")
    reject.add_argument("candidate_id")
    reject.add_argument("--reason-code", default="operator_rejected")
    reject.set_defaults(handler=run_project_learning, needs_app=False)

    rollback = commands.add_parser("rollback", help="Rollback an applied proposal")
    rollback.add_argument("candidate_id")
    rollback.set_defaults(handler=run_project_learning, needs_app=False)


__all__ = ["register", "run_project_learning"]
