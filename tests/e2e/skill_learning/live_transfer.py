"""Three explicit stages: author, independent review/prepare, then finite evaluation."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from pydantic import ValidationError

from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.skill.interfaces import SkillIngestAuthority
from openminion.modules.skill.learning.measurement import (
    MeasurementAttempt,
    MeasurementIdentity,
    MeasurementLimits,
    MeasurementManifest,
    MeasurementObservation,
    MeasurementReport,
    MeasurementTrial,
    TrialKey,
    _within_turn_limits,
    measurement_verdict,
)
from openminion.modules.skill.learning.proposer import stage_authored_skill_proposal
from openminion.modules.skill.learning.replay import proposal_draft_hash
from openminion.modules.skill.learning.runtime import list_runtime_workflow_shapes
from openminion.modules.skill.models import SkillPackage, stable_hash
from openminion.modules.skill.proposal.base import SkillProposal, SkillProposalDraft
from openminion.modules.skill.proposal.catalog import prepare_emergent_skill
from openminion.modules.skill.proposal.queue import (
    _persist_pending_package,
    get_proposal,
    record_proposal_review,
)
from openminion.modules.skill.runtime.skill import Skill
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.test_live_skill_confirmed_save import _skill_config
from tests.e2e.skill_learning.live_evidence import (
    focus_turn,
    observation,
    oracle,
    retain,
    verify_freeze,
    write_json,
)
from tests.helpers.runtime_roots import configure_runtime_roots


def experiment_config(original: dict, agent: str) -> dict:
    config = deepcopy(original)
    config.setdefault("storage", {})["path"] = "state/openminion.db"
    runtime = config.setdefault("runtime", {})
    runtime.update(
        process_mode="single-process",
        telemetry_enabled=True,
        brain_turn_timeout_seconds=300,
        chat_turn_timeout_seconds=300,
    )
    runtime.setdefault("env", {}).update(
        OPENMINION_BRAIN_MAX_TOTAL_LLM_TOKENS="40000",
        OPENMINION_BRAIN_MAX_TOOL_CALLS="40",
        OPENMINION_BRAIN_MAX_ELAPSED_MS="300000",
    )
    profile = config["agents"][agent]
    profile["default_act_profile"] = "general"
    profile.pop("skill", None)
    profile.pop("skill_catalog", None)
    config.setdefault("security", {}).setdefault("tool_policy", {})[
        "max_calls_per_run"
    ] = 40
    config.setdefault("skill", {})["runtime_workflow_observation_enabled"] = True
    config.setdefault("brain", {}).setdefault("tool_policy", {})["tools"] = {
        "allow_exact": ["exec.run"],
        "allow_prefix": [],
        "deny_exact": [],
        "deny_prefix": [],
    }
    return config


class TransferCampaign:
    def __init__(self, inputs: dict):
        self.inputs = inputs
        self.root = Path(inputs["attempt_root"]).resolve()
        self.source = Path(inputs["source_root"]).resolve()
        self.evaluation = Path(inputs["evaluation_root"]).resolve()
        self.agent = inputs["agent_id"]
        self.evaluator = inputs["evaluator_id"]
        assert self.agent != self.evaluator
        self.python = Path(inputs.get("python", sys.executable)).absolute()
        self.protocol = json.loads(
            (self.evaluation / "protocol-freeze.json").read_text()
        )
        verify_freeze(self.evaluation, self.protocol)
        self.config = experiment_config(
            json.loads(Path(inputs["config_path"]).read_text()), self.agent
        )
        for name, value in self.config["runtime"]["env"].items():
            os.environ[name] = str(value)
        self.revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.source, text=True
        ).strip()
        assert not subprocess.check_output(
            ["git", "status", "--porcelain", "--", "src", "tests"],
            cwd=self.source,
            text=True,
        ).strip(), "source and harness must be committed before the first live call"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        generated = configure_runtime_roots(self.root / "acquisition")
        self.state_path = self.root / "attempt.json"
        self.state = (
            json.loads(self.state_path.read_text())
            if self.state_path.exists()
            else {
                "stage": "new",
                "slots": [],
                "source_revision": self.revision,
                "config_digest": stable_hash(self.config),
                "protocol_digest": sha256(
                    (self.evaluation / "protocol-freeze.json").read_bytes()
                ).hexdigest(),
            }
        )
        assert self.state["source_revision"] == self.revision
        assert self.state["config_digest"] == stable_hash(self.config)
        assert (
            self.state["protocol_digest"]
            == sha256(
                (self.evaluation / "protocol-freeze.json").read_bytes()
            ).hexdigest()
        )
        self.save()
        self.ctl = ArtifactCtl(
            {
                "blob_store": {"root_dir": str(generated.parent / "artifact")},
                "index": {"sqlite_path": str(generated.parent / "artifact/index.db")},
                "views": {"auto_generate": []},
            }
        )

    def save(self) -> None:
        write_json(self.state_path, self.state)

    def has_budget(self) -> bool:
        limits = MeasurementLimits()
        observed = [
            (slot["phase"], MeasurementObservation.model_validate(slot["observation"]))
            for slot in self.state["slots"]
        ]
        if any(not _within_turn_limits(value, limits) for _, value in observed):
            return False
        for rows, tokens, seconds in (
            (
                [value for _, value in observed],
                limits.attempt_tokens,
                limits.attempt_seconds,
            ),
            (
                [value for phase, value in observed if phase is None],
                limits.campaign_tokens,
                limits.campaign_seconds,
            ),
        ):
            if (
                sum(value.tokens for value in rows) >= tokens
                or sum(value.elapsed_seconds for value in rows) >= seconds
            ):
                return False
        return True

    def probe(
        self, label: str, workspace: Path, *, acquisition: bool = False
    ) -> FocusProbe:
        data = self.root / (
            "acquisition/.openminion" if acquisition else f"turns/{label}/data"
        )
        config_path = self.root / "config.json"
        config_path.touch(mode=0o600)
        write_json(config_path, self.config)
        return FocusProbe(
            python_bin=self.python,
            openminion_root=self.source,
            framework_root=self.source.parent,
            data_root=data,
            config_path=config_path,
            agent_id=self.agent,
            workdir=workspace,
            session_id=f"vlr-{label}",
            include_project_context=False,
        )

    def turn(
        self,
        label: str,
        *,
        phase: str | None = None,
        case: str | None = None,
        package: SkillPackage | None = None,
        prompt: str | None = None,
    ) -> dict:
        assert not any(slot["label"] == label for slot in self.state["slots"]), (
            "turn slots cannot be retried"
        )
        assert len(self.state["slots"]) < 19
        assert self.has_budget(), "remaining frozen budget is unavailable"
        directory = self.root / "turns" / label
        directory.mkdir(parents=True)
        workspace = directory / "workspace"
        if case:
            request = oracle(
                self.evaluation,
                self.python,
                "materialize",
                case,
                workspace,
                directory / "request.json",
            )
            prompt = request["prompt"]
        else:
            shutil.copytree(self.root / "authoring-input", workspace)
        assert prompt
        probe = self.probe(label, workspace, acquisition=phase == "acquisition")
        if package is not None:
            skill = Skill(_skill_config(probe))
            try:
                _persist_pending_package(skill.store, package)
                skill.admit_skill_version(
                    skill_id=package.skill_id,
                    version_hash=package.version_hash,
                    expected_active_version_hash=None,
                    target_status="verified",
                    reason="Independent review accepted; disposable evaluator admission only",
                    authority=SkillIngestAuthority.local_operator(
                        surface="vlr-evaluation", principal_id=self.state["reviewer_id"]
                    ),
                )
            finally:
                skill.close()
        slot = {"label": label, "phase": phase, "case_id": case, "status": "started"}
        self.state["slots"].append(slot)
        self.save()
        events, result, elapsed = focus_turn(
            probe, prompt, directory / "transcript.txt"
        )
        if case:
            try:
                external = oracle(
                    self.evaluation,
                    self.python,
                    "evaluate",
                    case,
                    workspace,
                    directory / "oracle.json",
                )
            except (subprocess.SubprocessError, OSError, ValueError) as error:
                external = {"external_outcome": "unavailable", "error": str(error)}
        else:
            external = self.authored_candidate(workspace)
        measured = observation(
            self.ctl,
            probe=probe,
            prompt=prompt,
            events=events,
            result=result,
            external=external,
            elapsed=elapsed,
            evaluator=self.evaluator,
            provider=self.inputs["provider"],
            model=self.inputs["model"],
            evaluation=self.evaluation,
        )
        verify_freeze(self.evaluation, self.protocol)
        slot.update(status="finished", observation=measured.model_dump(mode="json"))
        self.save()
        return slot

    def authored_candidate(self, workspace: Path) -> dict:
        try:
            inputs = self.root / "authoring-input"
            assert all(
                (workspace / path.relative_to(inputs)).read_bytes() == path.read_bytes()
                for path in inputs.rglob("*")
                if path.is_file()
            ), "authoring changed supplied evidence"
            draft = SkillProposalDraft.model_validate_json(
                (workspace / "candidate.json").read_text()
            )
            assert draft.procedure is not None
            write_json(self.root / "candidate.json", draft.model_dump(mode="json"))
            return {
                "external_outcome": "correct",
                "draft_digest": stable_hash(draft.model_dump(mode="json")),
            }
        except (OSError, ValidationError, AssertionError) as error:
            return {"external_outcome": "incorrect", "error": str(error)}

    def author(self) -> None:
        assert self.state["stage"] == "new", "authoring stage cannot be repeated"
        self.state["stage"] = "author_started"
        self.save()
        shape = None
        for number in range(1, 4):
            self.turn(f"acquire-{number}", phase="acquisition", case=f"dev-{number}")
            if not self.has_budget():
                break
            skill = Skill(
                _skill_config(
                    self.probe("acquisition-inspect", self.root, acquisition=True)
                )
            )
            try:
                passed = {
                    "trace:" + slot["observation"]["trace_id"]
                    for slot in self.state["slots"]
                    if slot["observation"]["outcome"] == "correct"
                    and slot["observation"]["trace_id"]
                }
                shape = next(
                    (
                        item
                        for item in list_runtime_workflow_shapes(
                            skill.store, agent_id=self.agent
                        )
                        if item.success_count >= 2
                        and len(passed.intersection(item.evidence_refs)) >= 2
                    ),
                    None,
                )
            finally:
                skill.close()
            if shape:
                break
        if shape is None:
            self.state["stage"] = "blocked_acquisition"
            self.save()
            return
        authoring = self.root / "authoring-input"
        shutil.copytree(self.evaluation / "development", authoring / "development")
        for slot in self.state["slots"]:
            if slot["observation"]["outcome"] == "correct":
                shutil.copytree(
                    self.root / "turns" / slot["label"] / "workspace",
                    authoring / "completed" / slot["case_id"],
                )
        write_json(authoring / "workflow-shape.json", shape.model_dump(mode="json"))
        write_json(
            authoring / "draft-schema.json", SkillProposalDraft.model_json_schema()
        )
        slot = self.turn(
            "author",
            phase="authoring",
            prompt=(
                "Review the development project contracts and completed local repairs in this workspace. "
                "Derive a concise reusable coding procedure from those observed workflows. Read workflow-shape.json "
                "and draft-schema.json. Write candidate.json as one complete SkillProposalDraft matching that schema, "
                "with substantive when_to_use, do_not_use, steps, and validation. Preserve the observed risk class; "
                "use only observed tools. Describe applicable project conventions and how to verify them, without "
                "embedding a case answer or fixture identifier. Do not read outside this workspace or use the network. "
                "Do not modify development/completed files. Stop after writing and validating candidate.json."
            ),
        )
        if slot["observation"]["outcome"] != "correct" or not self.has_budget():
            self.state["stage"] = "blocked_authoring"
            self.save()
            return
        skill = Skill(
            _skill_config(
                self.probe("acquisition-inspect", self.root, acquisition=True)
            )
        )
        try:
            staged = stage_authored_skill_proposal(
                store=skill.store,
                agent_id=self.agent,
                shape_id=shape.shape_id,
                draft=SkillProposalDraft.model_validate_json(
                    (self.root / "candidate.json").read_text()
                ),
            )
            assert staged.status == "staged" and staged.proposal is not None
            self.state.update(
                stage="awaiting_review",
                proposal=staged.proposal.model_dump(mode="json"),
                acquisition_evidence_refs=sorted(
                    passed.intersection(shape.evidence_refs)
                ),
            )
            write_json(self.root / "proposal.json", self.state["proposal"])
            self.save()
        except ValueError as error:
            slot["observation"]["outcome"] = "incorrect"
            slot["observation"]["result_ref"] = retain(
                self.ctl,
                {
                    "session_id": slot["observation"]["session_id"],
                    "authoring_result_ref": slot["observation"]["result_ref"],
                    "proposal_error": str(error),
                },
                evaluator=self.evaluator,
            )
            self.state["stage"] = "blocked_authoring"
            self.save()
        finally:
            skill.close()

    def prepare(self) -> None:
        assert self.state["stage"] == "awaiting_review"
        proposal = SkillProposal.model_validate(self.state["proposal"])
        review_input = json.loads(Path(self.inputs["review_path"]).read_text())
        skill = Skill(
            _skill_config(
                self.probe("acquisition-inspect", self.root, acquisition=True)
            )
        )
        try:
            assert (
                get_proposal(skill.store, proposal_id=proposal.proposal_id)["proposal"]
                == self.state["proposal"]
            )
            review = record_proposal_review(
                skill.store, proposal_id=proposal.proposal_id, **review_input
            )
            _, package = prepare_emergent_skill(
                review,
                skill_definition=proposal.proposed_skill_definition,
                proposal=proposal,
            )
        finally:
            skill.close()
        identity = MeasurementIdentity(
            source_revision=self.revision,
            proposal_id=proposal.proposal_id,
            shape_id=proposal.source_task_shape_ref,
            agent_id=self.agent,
            candidate_hash=proposal_draft_hash(proposal),
            skill_id=package.skill_id,
            package_hash=package.version_hash,
            corpus_digest=self.protocol["corpus_sha256"],
            oracle_digest=self.protocol["oracle_sha256"],
            model=self.inputs["model"],
            provider=self.inputs["provider"],
            config_digest=stable_hash(self.config),
            permission_digest=stable_hash(
                {
                    "security": self.config["security"],
                    "brain": self.config["brain"],
                    "action_policy": self.config.get("action_policy"),
                    "agent_policy": self.config["agents"][self.agent].get(
                        "action_policy"
                    ),
                }
            ),
        )
        manifest = MeasurementManifest(
            identity=identity,
            cases={
                case["case_id"]: case["applicable"]
                for case in self.protocol["cases"]
                if case["phase"] == "heldout"
            },
            acquisition_evidence_refs=self.state["acquisition_evidence_refs"],
            trial_order=[
                TrialKey(case_id=case, arm=arm, repeat=repeat)
                for case, arm, repeat in self.protocol["order"]
            ],
        )
        write_json(self.root / "package.json", package.to_dict())
        write_json(self.root / "manifest.json", manifest.model_dump(mode="json"))
        self.state.update(
            stage="prepared",
            reviewer_id=review.reviewer_id,
            manifest_ref=retain(
                self.ctl, manifest.model_dump(mode="json"), evaluator=self.evaluator
            ),
            manifest_digest=stable_hash(manifest.model_dump(mode="json")),
        )
        self.save()

    def evaluate(self) -> None:
        assert self.state["stage"] == "prepared", "evaluation stage cannot be repeated"
        manifest = MeasurementManifest.model_validate_json(
            (self.root / "manifest.json").read_text()
        )
        assert (
            stable_hash(manifest.model_dump(mode="json"))
            == self.state["manifest_digest"]
        )
        package = SkillPackage.from_dict(
            json.loads((self.root / "package.json").read_text())
        )
        assert package.to_version_hash() == manifest.identity.package_hash
        self.state["stage"] = "evaluation_started"
        self.save()
        for number in range(1, 4):
            self.turn(
                f"replay-{number}",
                phase="replay",
                case=f"dev-{number}",
                package=package,
            )
            if not self.has_budget():
                break
        replays = [slot for slot in self.state["slots"] if slot["phase"] == "replay"]
        if (
            len(replays) == 3
            and self.has_budget()
            and all(
                slot["observation"]["outcome"] == "correct"
                and _within_turn_limits(
                    MeasurementObservation.model_validate(slot["observation"]),
                    manifest.limits,
                )
                for slot in replays
            )
        ):
            for key in manifest.trial_order:
                slot = self.turn(
                    f"{key.case_id}-{key.arm}-{key.repeat}",
                    case=key.case_id,
                    package=package if key.arm == "candidate" else None,
                )
                slot["key"] = key.model_dump(mode="json")
                self.save()
                if not self.has_budget():
                    break
        report = MeasurementReport(
            identity=manifest.identity,
            manifest_ref=self.state["manifest_ref"],
            manifest_digest=self.state["manifest_digest"],
            attempts=[
                MeasurementAttempt(phase=slot["phase"], observation=slot["observation"])
                for slot in self.state["slots"]
                if slot["phase"]
            ],
            trials=[
                MeasurementTrial(
                    key=slot["key"],
                    applicable=manifest.cases[slot["case_id"]],
                    observation=slot["observation"],
                )
                for slot in self.state["slots"]
                if "key" in slot
            ],
            verdict="inconclusive",
        )
        report.verdict = measurement_verdict(manifest, report)
        write_json(self.root / "measurement.json", report.model_dump(mode="json"))
        self.state.update(
            stage="evaluated",
            verdict=report.verdict,
            measurement_ref=retain(
                self.ctl, report.model_dump(mode="json"), evaluator=self.evaluator
            ),
        )
        self.save()


def run_stage(inputs_path: Path, stage: str) -> dict:
    campaign = TransferCampaign(json.loads(inputs_path.read_text()))
    try:
        if stage == "author":
            campaign.author()
        elif stage == "prepare":
            campaign.prepare()
        elif stage == "evaluate":
            campaign.evaluate()
        else:
            raise ValueError(f"unknown stage: {stage}")
        return campaign.state
    finally:
        campaign.ctl.close()
