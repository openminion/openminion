"""Pinned memory campaign setup through the existing Focus test entrypoint."""

from __future__ import annotations

from functools import partial
import hashlib
from importlib.metadata import version
from importlib.util import find_spec
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
from urllib.parse import urlsplit
from unittest.mock import patch

from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario
from tests.e2e.cli.focus.harness.assertions import final_answer_text

ALLOWED_TOOLS = ["memory.write", "memory.search", "memory.forget"]
PHASES = {
    "explicit": ("teach", "recall"),
    "automatic": ("teach", "recall"),
    "correction": ("teach", "correct", "recall"),
    "forget": ("teach", "forget", "recall"),
    "relevance": ("teach", "recall"),
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def process_slots() -> list[str]:
    return [
        f"{case}-{pair:02d}-{arm}-{phase}"
        for case, phases in PHASES.items()
        for pair in range(1, 6)
        for arm in (("memory", "control") if pair % 2 else ("control", "memory"))
        for phase in phases
    ]


def load_campaign(path: Path, *, require_approval: bool = True) -> dict:
    _reject_campaign_overrides()
    manifest = json.loads(path.read_text())
    if require_approval:
        if (
            os.environ.get("OPENMINION_LIVE_CLI_FOCUS_E2E") != "1"
            or os.environ.get("CSMA_APPROVED_MANIFEST_SHA256") != digest(path)
            or not manifest["approval_ready"]
        ):
            raise ValueError("Gate B approval and exact manifest hash required")
    root = Path(__file__).resolve().parents[5]
    if platform.python_version() != manifest["python_version"]:
        raise ValueError("campaign interpreter changed")
    if (
        Path(find_spec("openminion").origin).resolve()
        != root / "src/openminion/__init__.py"
    ):
        raise ValueError("campaign is not using pinned worktree source")
    if require_approval:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        if revision != manifest["source_revision"]:
            raise ValueError("campaign source revision changed")
        if subprocess.check_output(
            ["git", "status", "--porcelain", "--", "src"], cwd=root, text=True
        ).strip():
            raise ValueError("campaign production source changed")
    for name, expected in manifest["dependencies"].items():
        if version(name) != expected:
            raise ValueError(f"changed campaign dependency: {name}")
    for relative, expected in manifest["source_files_sha256"].items():
        if digest(root / relative) != expected:
            raise ValueError(f"changed campaign source: {relative}")
    for name in ("config", "corpus", "credential_profile"):
        if digest(Path(manifest[f"{name}_path"])) != manifest[f"{name}_sha256"]:
            raise ValueError(f"changed campaign {name}")
    if manifest["process_slots"] != process_slots():
        raise ValueError("campaign process inventory changed")
    return manifest


def campaign_config(manifest: dict, slot: str) -> tuple[dict, Path]:
    case, pair, arm, phase = slot.split("-")
    arm_root = Path(manifest["run_root"]) / "agents" / f"{case}-{pair}-{arm}"
    data_root = arm_root / "data"
    config = json.loads(Path(manifest["config_path"]).read_text())
    config["storage"]["path"] = str(data_root / "state" / "openminion.db")
    config["identity"]["db_path"] = str(data_root / "identity" / "identity.db")
    if arm == "control" and phase == "recall":
        config["runtime"]["memory_enabled"] = False
    return config, arm_root


def restricted_runtime(config_path, *, agent_id: str, **kwargs):
    from openminion.api.runtime import APIRuntime
    from openminion.modules.identity.runtime.service import IdentityCtl
    from openminion.modules.identity.storage import SQLiteIdentityStore
    from openminion.services.identity import ensure_default_profile

    _reject_campaign_overrides()
    config = json.loads(Path(config_path).read_text())
    identity_path = Path(config["identity"]["db_path"])
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    identity = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(identity_path)))
    try:
        ensure_default_profile(identity, agent_id)
    finally:
        identity.close()
    runtime = APIRuntime.from_config_path(config_path, **kwargs)
    try:
        service = runtime.resolve_agent_service(agent_id)
        identity = service._identityctl
        assert identity is not None, "campaign identity unavailable"
        profile = identity.get_profile(service._identity_agent_id)
        assert profile is not None, "campaign identity profile unavailable"
        identity.upsert_profile(
            profile.model_copy(
                update={
                    "tool_posture": profile.tool_posture.model_copy(
                        update={
                            "tool_use": "restricted",
                            "allowed_tools": ALLOWED_TOOLS,
                        }
                    )
                }
            )
        )
        service._refresh_identity_runtime_state()
        assert service._identity_tool_filter["allowed_tools"] == ALLOWED_TOOLS
        return runtime
    except BaseException:
        runtime.close()
        raise


def _reject_campaign_overrides() -> None:
    if any(
        os.environ.get(name)
        for name in (
            "OPENMINION_IDENTITY_DB",
            "OPENMINION_IDENTITY_ROOT",
            "OPENMINION_MEMORY_PROVIDER",
            "OPENMINION_MEMORY_CAPSULE_STRATEGY",
            "OPENMINION_MEMORY_DYNAMIC_RETRIEVAL_ENABLED",
        )
    ):
        raise ValueError("campaign forbids inherited identity or memory overrides")


def read_state(
    config_path: Path, data_root: Path, slot: str, tokens: dict[str, str]
) -> dict:
    from openminion.api.runtime import APIRuntime
    from openminion.cli.interactive.runtime.messages import RuntimeMessageMixin
    from openminion.modules.memory.storage.base import CandidateListOptions
    from tests.e2e.cli.focus.harness.assertions import read_focus_evidence

    home = data_root.parent / "home-roots" / slot
    _reject_campaign_overrides()
    with patch(
        "socket.socket.connect",
        side_effect=AssertionError("inspection must stay offline"),
    ):
        runtime = APIRuntime.from_config_path(
            str(config_path), home_root=home, data_root=data_root
        )
        try:
            service = runtime.runtime_memory_assembly.service
            records = service._store.list_all()
            candidates = service.candidate_list(CandidateListOptions(session_id=slot))
            messages = runtime.sessions.list_messages(
                session_id=slot, limit=runtime.sessions.count_messages(session_id=slot)
            )
            receipts = [
                json.loads(message.metadata["memory_capture_bundle_result"])
                for message in messages
                if message.role == "outbound"
                and "memory_capture_bundle_result" in message.metadata
            ]
            events, _, brain_session = read_focus_evidence(
                {"OPENMINION_HOME": str(home), "OPENMINION_DATA_ROOT": str(data_root)},
                slot,
            )
            manifests = [
                {
                    key: event.data.get(key)
                    for key in (
                        "llm_call_id",
                        "prompt_context_id",
                        "recalled_memory",
                        "facts",
                        "memory",
                        "recent_session_artifacts",
                        "included_segment_ids",
                        "dropped_segment_ids",
                    )
                }
                for event in events
                if event.event_type == "context.manifest.created"
            ]
            return {
                "session_id": slot,
                "brain_session_id": brain_session,
                "memory_write_events": [
                    {
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "tool_call_id": event.data.get("tool_call_id"),
                        "status": event.data.get("status"),
                    }
                    for event in events
                    if event.data.get("tool_name") == "memory.write"
                    and event.event_type
                    in {
                        "tool.execution.started",
                        "tool.execution.completed",
                        "tool.execution.failed",
                    }
                ],
                "response_ids": [
                    message.id for message in messages if message.role == "outbound"
                ],
                "response_hashes": [
                    hashlib.sha256(
                        " ".join(
                            RuntimeMessageMixin._strip_sender_prefix(
                                message.body
                            ).split()
                        ).encode()
                    ).hexdigest()
                    for message in messages
                    if message.role == "outbound"
                ],
                "capture": [
                    {
                        key: receipt.get(key)
                        for key in (
                            "capture_id",
                            "disposition",
                            "output_ids",
                            "result_hash",
                        )
                    }
                    for receipt in receipts
                ],
                "candidates": [
                    {
                        "id": candidate.candidate_id,
                        "status": candidate.status,
                        "capture_id": candidate.meta.get("capture_id"),
                        "root_turn_id": candidate.meta.get("source_root_turn_id"),
                        "matches": [
                            label
                            for label, token in tokens.items()
                            if token in str(candidate.content)
                        ],
                    }
                    for candidate in candidates
                ],
                "records": [
                    {
                        "id": record.id,
                        "type": record.type,
                        "capture_id": record.meta.get("capture_id"),
                        "continuity_key": record.key == "fact:continuity_code",
                        "weather_key": record.key == "fact:weather"
                        and record.type == "fact",
                        "active": not (
                            record.is_deleted
                            or record.valid_to
                            or record.superseded_by_id
                        ),
                        "matches": [
                            label
                            for label, token in tokens.items()
                            if token in str(record.content)
                            or token in str(record.title)
                        ],
                    }
                    for record in records
                ],
                "context_manifests": manifests,
                "retained_carriers": _carrier_inventory(
                    runtime, slot, brain_session, messages, tokens
                ),
                "canonical_use": "not_claimed",
            }
        finally:
            runtime.close()


def _carrier_inventory(runtime, slot, brain_session, messages, tokens):
    from openminion.modules.brain.paths import resolve_brain_sessions_db_path
    from openminion.modules.session.storage.store import SQLiteSessionStore

    carriers = [
        (
            {
                "kind": "transcript",
                "id": message.id,
                "eligibility": "protected_history",
            },
            (message.body, message.metadata),
        )
        for message in messages
    ]
    context = runtime.sessions.get_session_context(session_id=slot)
    if context is not None:
        carriers.append(
            (
                {
                    "kind": "rolling_context",
                    "id": slot,
                    "version": context.version,
                    "eligibility": "session_local",
                },
                (
                    context.rolling_summary,
                    context.summary_short,
                    context.pinned_context,
                ),
            )
        )
    store = SQLiteSessionStore(
        resolve_brain_sessions_db_path(storage_path=runtime.storage_path)
    )
    try:
        state = store.get_latest_working_state(
            brain_session, agent_id=runtime.config.default_agent
        )
    finally:
        store.close()
    if state is not None:
        if state["state_ref"] and state["state_inline"] is None:
            raise AssertionError("external_working_state_inventory_unavailable")
        carriers.append(
            (
                {
                    "kind": "working_state",
                    "id": brain_session,
                    "version": state["version"],
                    "eligibility": "resume_only",
                },
                state["state_inline"],
            )
        )
    ctl = runtime.retrieve_ctl
    for row in ctl.store.fetchall(
        "SELECT d.doc_id, d.scope_key, d.title, u.unit_id, u.fts_text, u.text_ref, u.context_text_ref "
        "FROM retrievectl_docs d JOIN retrievectl_units u ON d.doc_id=u.doc_id WHERE d.source_type='episode'"
    ):
        texts = [row["title"], row["fts_text"]]
        for field in ("text_ref", "context_text_ref"):
            if row[field]:
                with ctl.blob_store.open(row[field]) as handle:
                    texts.append(handle.read().decode("utf-8"))
        carriers.append(
            (
                {
                    "kind": "episode",
                    "id": row["unit_id"],
                    "doc_id": row["doc_id"],
                    "scope_key": row["scope_key"],
                    "eligibility": "scope_key_filtered",
                },
                texts,
            )
        )
    return [
        {
            **facts,
            "matches": [
                label for label, token in tokens.items() if token in str(value)
            ],
        }
        for facts, value in carriers
    ]


def run_phase(
    *,
    manifest: dict,
    slot: str,
    config_path: Path,
    data_root: Path,
    workdir: Path,
    evaluator_root: Path,
    prompt: str,
    target_hashes: dict[str, str],
    deadline_monotonic: float,
) -> tuple[str, list[dict]]:
    if slot not in manifest["process_slots"]:
        raise ValueError("unapproved process slot")
    # Exclusive claims prevent failed attempts being replaced by a rerun.
    (evaluator_root / f"{slot}.claimed").touch(exist_ok=False)
    descriptor = {
        **manifest["per_process"],
        "endpoint": manifest["endpoint"],
        "model": manifest["model"],
        "session_id": slot,
        "target_hashes": target_hashes,
        "evidence_path": str(evaluator_root / f"{slot}.jsonl"),
        "config_sha256": digest(config_path),
        "deadline_monotonic": deadline_monotonic,
    }
    child_spec = evaluator_root / f"{slot}.json"
    child_spec.write_text(json.dumps(descriptor), encoding="utf-8")
    probe = FocusProbe(
        python_bin=Path(sys.executable),
        openminion_root=Path(__file__).resolve().parents[5],
        framework_root=Path(__file__).resolve().parents[6],
        data_root=data_root,
        config_path=config_path,
        agent_id=manifest["agent_id"],
        workdir=workdir,
        session_id=slot,
        include_project_context=False,
        allow_unsandboxed_exec=False,
        entrypoint_module=__name__,
    )
    session = probe.session()
    session.env["CSMA_CHILD_SPEC"] = str(child_spec)
    session.env.update({"OPENMINION_TRACE_REQUESTS": "0", "OPENMINION_LLM_DEBUG": "0"})
    try:
        with session:
            probe.wait_ready(session, timeout=30)
            transcript = probe.run_turn(
                session,
                FocusScenario(
                    scenario_id=slot,
                    prompt=prompt,
                    expected_markers=(),
                    requires_approval=True,
                    timeout=manifest["per_process"]["lifetime_seconds"] - 10,
                    include_project_context=False,
                ),
            )
            answer = final_answer_text(transcript, prompt)
            session.type_line("/exit")
            if session.wait(timeout=manifest["per_process"]["lifetime_seconds"]) != 0:
                raise AssertionError("campaign_child_failed")
    except Exception as exc:
        # Do not export the PTY transcript or nonce in pytest failure output.
        raise AssertionError(
            f"{slot}: {type(exc).__name__}; attempt retained"
        ) from None
    evidence = [
        json.loads(line)
        for line in Path(descriptor["evidence_path"]).read_text().splitlines()
    ]
    if (
        not evidence
        or evidence[-1].get("event") != "completed"
        or any(row.get("event") == "rejected" for row in evidence)
    ):
        raise AssertionError(f"{slot}: incomplete_request_observation")
    return answer, evidence


def main() -> int:
    if not all(
        os.environ.get(name)
        for name in (
            "OPENMINION_HOME",
            "OPENMINION_DATA_ROOT",
            "OPENMINION_GENERATED_ROOT",
        )
    ):
        raise ValueError("campaign requires explicit isolated runtime roots")
    from openminion.base.config.env import EnvironmentConfig
    from openminion.cli.commands.interactive import run_interactive
    from openminion.cli.main import _prepare_runtime_roots
    from openminion.cli.parser.base import build_parser
    from tests.e2e.cli.focus.harness.memory_observation import observe_requests

    spec = json.loads(Path(os.environ["CSMA_CHILD_SPEC"]).read_text())
    manifest = None
    if urlsplit(spec["endpoint"]).hostname != "127.0.0.1":
        manifest = load_campaign(Path(os.environ["CSMA_MANIFEST"]))
        if (
            spec["session_id"] not in manifest["process_slots"]
            or spec["endpoint"] != manifest["endpoint"]
            or spec["model"] != manifest["model"]
            or any(spec[k] != v for k, v in manifest["per_process"].items())
        ):
            raise ValueError("child differs from approved manifest")
    args = build_parser().parse_args()
    if args.command or args.allow_unsandboxed_exec or not args.no_context:
        raise ValueError(
            "campaign requires bare Focus, no context and no host execution"
        )
    if (
        args.session != spec["session_id"]
        or digest(Path(args.config)) != spec["config_sha256"]
    ):
        raise ValueError("child session/config mismatch")
    if manifest is not None:
        expected, arm_root = campaign_config(manifest, spec["session_id"])
        if json.loads(Path(args.config).read_text()) != expected:
            raise ValueError("child config differs from approved recipe")
        expected_roots = {
            "OPENMINION_HOME": arm_root / "home-roots" / spec["session_id"],
            "OPENMINION_DATA_ROOT": arm_root / "data",
            "OPENMINION_GENERATED_ROOT": arm_root / "data" / "runtime",
        }
        if any(Path(os.environ[name]) != root for name, root in expected_roots.items()):
            raise ValueError("child runtime roots differ from approved recipe")
        if (
            Path(args.dir).resolve() != arm_root / "work"
            or args.agent != manifest["agent_id"]
        ):
            raise ValueError("child workdir/agent differs from approved recipe")
        started = json.loads((Path(manifest["run_root"]) / "started.json").read_text())
        pair_id = "-".join(spec["session_id"].split("-")[:2])
        if (
            spec["deadline_monotonic"]
            != started["monotonic"] + manifest["max_admission_seconds"]
            or spec["target_hashes"] != manifest["target_hashes"][pair_id]
            or Path(spec["evidence_path"])
            != Path(manifest["run_root"]) / "evaluator" / f"{spec['session_id']}.jsonl"
        ):
            raise ValueError(
                "child deadline/oracle/evidence differs from approved recipe"
            )
    home, data, _, _, _ = _prepare_runtime_roots(args, EnvironmentConfig.from_sources())
    args.home_root, args.data_root = home, data
    args.onboarding_checked = True
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.alarm(spec["lifetime_seconds"])
    # The observer remains installed until process exit, including late summaries.
    with observe_requests(
        endpoint=spec["endpoint"],
        model=spec["model"],
        request_limit=spec["request_limit"],
        body_limit=spec["body_limit"],
        output_limit=spec["output_limit"],
        timeout_limit=spec["timeout_limit"],
        deadline_monotonic=spec["deadline_monotonic"],
        session_id=spec["session_id"],
        target_hashes=spec["target_hashes"],
        evidence_path=Path(spec["evidence_path"]),
    ) as observation:
        result = 1
        try:
            result = run_interactive(
                args, runtime_factory=partial(restricted_runtime, agent_id=args.agent)
            )
            if result != 0:
                raise AssertionError("interactive_runtime_failed")
            observation.finish()
        finally:
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            finally:
                os._exit(
                    result if observation.closed and not observation.failure else 1
                )


if __name__ == "__main__":
    raise SystemExit(main())
