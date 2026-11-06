"""Operator learning commands over stored observations and reviewed proposals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from openminion.modules.cli_common import print_json_payload
from openminion.modules.skill.errors import SkillError
from openminion.modules.skill.runtime.skill import Skill

LEARNING_COMMANDS = frozenset(
    {
        "learning-scan",
        "learning-inspect",
        "learning-save-workflow",
        "learning-observation-list",
        "learning-shape-list",
        "learning-propose",
        "learning-replay-proof",
        "learning-apply-proved",
        "learning-trust-status",
    }
)


def _dispatch_runtime_observation_cmd(ctl: Skill, args: argparse.Namespace) -> bool:
    from openminion.modules.skill.learning.runtime import WorkflowObservationError

    if args.cmd == "learning-observation-list":
        try:
            observations = ctl.list_workflow_observations(agent_id=args.agent_id)
        except WorkflowObservationError as exc:
            raise SkillError("INVALID_ARGUMENT", str(exc)) from exc
        print_json_payload(
            {"ok": True, "observations": observations},
            sort_keys=False,
            ensure_ascii=True,
        )
        return True

    if args.cmd == "learning-shape-list":
        from openminion.modules.skill.learning import WorkflowShapeMiner

        try:
            shapes = ctl.list_workflow_shapes(agent_id=args.agent_id)
        except WorkflowObservationError as exc:
            raise SkillError("INVALID_ARGUMENT", str(exc)) from exc
        miner = WorkflowShapeMiner()
        print_json_payload(
            {
                "ok": True,
                "shapes": [
                    {
                        **shape.model_dump(mode="json"),
                        "state": (
                            "authoring_ready"
                            if miner.is_skill_ready(shape)
                            else "observed"
                        ),
                    }
                    for shape in shapes
                ],
            },
            sort_keys=False,
            ensure_ascii=True,
        )
        return True
    return False


def dispatch_learning_command(ctl: Skill, args: argparse.Namespace) -> None:
    from openminion.modules.skill.learning import (
        execution_trust_diagnostic,
        WorkflowEvidenceBundle,
        WorkflowShape,
        WorkflowShapeMiner,
    )

    if _dispatch_runtime_observation_cmd(ctl, args):
        return

    if args.cmd == "learning-scan":
        raw = _read_json_path(args.bundle_json)
        bundles = [
            WorkflowEvidenceBundle.model_validate(item)
            for item in (raw if isinstance(raw, list) else [raw])
        ]
        shapes = WorkflowShapeMiner().mine(bundles)
        print_json_payload(
            {
                "ok": True,
                "shapes": [shape.model_dump(mode="json") for shape in shapes],
            },
            sort_keys=False,
            ensure_ascii=True,
        )
        return

    if args.cmd == "learning-inspect":
        shape = WorkflowShape.model_validate(_read_json_path(args.shape_json))
        print_json_payload(
            {"ok": True, "shape": shape.model_dump(mode="json")},
            sort_keys=False,
            ensure_ascii=True,
        )
        return

    if args.cmd == "learning-save-workflow":
        shape = WorkflowShape.model_validate(_read_json_path(args.shape_json))
        actor_id = str(args.actor_id or "").strip()
        if not actor_id:
            raise SkillError("INVALID_ARGUMENT", "--actor-id is required")
        print_json_payload(
            {
                "ok": True,
                "save_workflow": {
                    "shape_id": shape.shape_id,
                    "actor_id": actor_id,
                    "source_run_ref": str(args.source_run_ref or ""),
                    "explicit_save": True,
                },
            },
            sort_keys=False,
            ensure_ascii=True,
        )
        return

    if args.cmd == "learning-propose":
        _propose(ctl, args)
        return

    if args.cmd == "learning-replay-proof":
        from openminion.modules.artifact.control import ArtifactCtl
        from openminion.modules.artifact.errors import ArtifactCtlError
        from openminion.modules.skill.proposal.queue import record_replay_proof

        try:
            with ArtifactCtl({}) as artifactctl:
                retained = record_replay_proof(
                    ctl.store,
                    proposal_id=args.proposal_id,
                    result_ref=args.result_ref,
                    artifactctl=artifactctl,
                    measurement_manifest_ref=args.measurement_manifest_ref,
                )
        except (ArtifactCtlError, ValueError) as exc:
            raise SkillError("INVALID_ARGUMENT", str(exc)) from exc
        print_json_payload(
            {"ok": True, "proof": retained}, sort_keys=False, ensure_ascii=True
        )
        return

    if args.cmd == "learning-apply-proved":
        _dispatch_learning_apply_proved(ctl, args)
        return

    if args.cmd == "learning-trust-status":
        record = execution_trust_diagnostic(
            skill_id=args.skill_id,
            shape_id=args.shape_id,
            lifecycle_facts=ctl.store.get_skill_lifecycle_facts(skill_id=args.skill_id),
        )
        print_json_payload(
            {"ok": True, "trust": record.model_dump(mode="json")},
            sort_keys=False,
            ensure_ascii=True,
        )
        return

    raise SkillError("INVALID_ARGUMENT", "Unsupported learning command")


def _dispatch_learning_apply_proved(ctl: Skill, args: argparse.Namespace) -> None:
    from openminion.modules.skill.learning import apply_proposal_with_replay
    from openminion.modules.skill.learning.replay import ReplayGateError

    try:
        addition = apply_proposal_with_replay(
            ctl.store,
            proposal_id=args.proposal_id,
            current_catalog=ctl.list_skills({}) or [],
        )
    except (ReplayGateError, ValueError) as exc:
        raise SkillError("INVALID_ARGUMENT", str(exc)) from exc
    print_json_payload(
        {"ok": True, "addition": addition.model_dump(mode="json")},
        sort_keys=False,
        ensure_ascii=True,
    )


def _read_json_path(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _propose(ctl: Skill, args: argparse.Namespace) -> None:
    from openminion.modules.skill.learning import (
        WorkflowShape,
        stage_shape_as_skill_proposal,
    )
    from openminion.modules.skill.learning.proposer import (
        stage_authored_skill_proposal,
    )
    from openminion.modules.skill.learning.runtime import WorkflowObservationError
    from openminion.modules.skill.proposal.base import SkillProposalDraft

    try:
        if args.shape_json:
            if args.agent_id is not None or args.procedure_file is not None:
                raise ValueError("--shape-json cannot be combined with authored inputs")
            shape = WorkflowShape.model_validate(_read_json_path(args.shape_json))
            result = stage_shape_as_skill_proposal(
                shape,
                store=ctl.store,
                current_catalog=ctl.list_skills({}) or [],
            )
        else:
            if not args.agent_id or not args.procedure_file:
                raise ValueError("--shape-id requires --agent-id and --procedure-file")
            result = stage_authored_skill_proposal(
                store=ctl.store,
                agent_id=args.agent_id,
                shape_id=args.shape_id,
                draft=SkillProposalDraft.model_validate(
                    _read_json_path(args.procedure_file)
                ),
            )
    except (ValueError, OSError, WorkflowObservationError) as exc:
        raise SkillError("INVALID_ARGUMENT", str(exc)) from exc
    print_json_payload(
        {"ok": True, "result": result.model_dump(mode="json")},
        sort_keys=False,
        ensure_ascii=True,
    )
