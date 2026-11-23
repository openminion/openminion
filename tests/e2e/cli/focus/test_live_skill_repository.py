"""Bounded repository availability smoke; independent of the SKCP-05 campaign."""

from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from types import SimpleNamespace

import pytest

from openminion.cli.commands.daemon import daemon_stop
from openminion.modules.skill.interfaces import (
    SkillIngestAuthority,
    SkillVerificationEvidence,
)
from openminion.modules.skill.runtime.skill import Skill
from openminion.tools.skill.plugin import _h_skill_get
from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.cli.focus.harness.assertions import (
    assert_recorded_answer,
    current_turn_events,
    final_answer_text,
    read_focus_evidence,
)
from tests.e2e.cli.focus.test_live_goal_only import _events, _usage
from tests.e2e.cli.focus.test_live_skill_catalog import _assert_resource
from tests.e2e.cli.focus.test_live_skill_confirmed_save import _skill_config
from tests.e2e.test_live_skill_dense_catalog_matrix import _load_events

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(780)]

SKILL_ID = "repository_probe"
RESOURCE = "references/fact.txt"
FACT = "amber-orbit-7319"
SCENARIOS = (
    FocusScenario(
        "repository-a",
        "Use the repository_probe skill. Read references/fact.txt through skill.get "
        "with the skill's exact selected version_hash. Reply only with the fact in "
        "that reference. Do not read workspace files, write files, run commands, "
        "or use any other capability.",
        timeout=300,
        include_project_context=False,
    ),
    FocusScenario(
        "repository-b",
        "Use the repository_probe skill if it is available in this repository. "
        "Read references/fact.txt only through skill.get with an exact version_hash "
        "provided by the available skill. If the skill is unavailable, reply only "
        "SKILL_UNAVAILABLE. Do not guess its contents, read workspace files, write "
        "files, run commands, or use any other capability.",
        timeout=300,
        include_project_context=False,
    ),
)
LIMITS = {
    "MAX_TOTAL_LLM_TOKENS": 80000,
    "MAX_TICKS": 8,
    "MAX_TOOL_CALLS": 8,
    "MAX_A2A_CALLS": 0,
    "MAX_ELAPSED_MS": 300000,
}
_TOOLS = {"skill.get", "respond", "clarify"}
_DISCOVERY = {"tool.request", "tool.search", "tool.list"}


def _seed_repository(probe: FocusProbe, repository: Path) -> dict:
    bundle = repository / ".agents/skills/repository-probe"
    (bundle / "references").mkdir(parents=True)
    (bundle / RESOURCE).write_text(FACT + "\n")
    source = bundle / "SKILL.md"
    source.write_text(
        "---\nname: Repository Probe\nid: repository_probe\n"
        "description: Read the repository probe fact from its captured reference.\n"
        "---\n\n# Procedure\n"
        "1. Read references/fact.txt using skill.get with this skill's exact "
        "selected version_hash.\n2. Return only the fact from the reference.\n\n"
        "# Verification\nThe answer equals the captured reference fact.\n"
    )
    assert FACT not in source.read_text()
    authority = SkillIngestAuthority.local_operator(
        surface="test.focus.repository", principal_id="local:test"
    )
    with closing(Skill(_skill_config(probe))) as skill:
        assert (
            skill.catalog_summaries(probe.agent_id, repository_root=str(repository))
            == []
        )
        skill_id, version, warnings = skill.ingest_file(
            source,
            scope="project",
            repository_root=str(repository),
            authority=authority,
        )
        assert skill_id == SKILL_ID, "frozen prompt must name the actual admitted ID"
        package = skill.get_skill(skill_id, version)
        source_hash = sha256(source.read_bytes()).hexdigest()
        assert package.source_artifact_ref.rsplit("/", 1)[-1] == source_hash
        assert len(package.resources) == 1
        assert (
            package.resources[0]["sha256"]
            == sha256((bundle / RESOURCE).read_bytes()).hexdigest()
        )
        owner = skill.get_skill_owner(skill_id=skill_id)
        assert owner == {
            "scope": "project",
            "agent_id": None,
            "repository_root": str(repository),
        }
        receipt = skill.admit_skill_version(
            skill_id=skill_id,
            version_hash=version,
            expected_active_version_hash=None,
            target_status="verified",
            reason="Harmless repository fixture admission, not live qualification",
            authority=authority,
            verification_evidence=SkillVerificationEvidence(
                check="Captured source and reference match the frozen fixture",
                result="passed",
                evidence_ref=source.as_uri(),
            ),
        )
        assert receipt["active_version_hash"] == version
        return {
            "skill_id": skill_id,
            "version_hash": version,
            "owner": owner,
            "resources": package.resources,
            "admission": receipt,
            "warnings": warnings,
            "source_sha256": source_hash,
        }


def _scope_reads(probe: FocusProbe, repository: Path | None, manifest: dict) -> dict:
    root = str(repository) if repository is not None else None
    with closing(Skill(_skill_config(probe))) as skill:
        ctx = SimpleNamespace(
            skill_api=skill, agent_id=probe.agent_id, repository_root=root
        )
        args = {
            "skill_id": manifest["skill_id"],
            "version_hash": manifest["version_hash"],
        }
        package = _h_skill_get(args, ctx)
        resource = _h_skill_get({**args, "resource_path": RESOURCE}, ctx)
        catalog = skill.catalog_summaries(probe.agent_id, repository_root=root)
        if root == manifest["owner"]["repository_root"]:
            assert [(r["id"], r["version_hash"]) for r in catalog] == [
                (manifest["skill_id"], manifest["version_hash"])
            ]
            assert package["ok"] is True
            _assert_resource(
                [("skill.get", {**args, "resource_path": RESOURCE}, resource)],
                manifest,
                RESOURCE,
            )
            assert resource["content"].strip() == FACT
        else:
            assert catalog == []
            for output in (package, resource):
                assert output["ok"] is False and output["error"]["code"] == "NOT_FOUND"
                assert FACT not in json.dumps(output)
        return {"catalog": catalog, "package": package, "resource": resource}


def _assert_turn(evidence: dict, manifest: dict, *, available: bool) -> None:
    events = evidence["brain_events"]
    routing = [e["payload"] for e in events if e["type"] == "skill.prerouting"]
    summaries = [p for p in routing if "effective_skill_count" in p]
    assert summaries and all(
        p["effective_skill_count"] == int(available) for p in summaries
    )
    assert all(p.get("fail_closed_reason") is None for p in routing)
    selected = [e["payload"] for e in events if e["type"] == "skill.selected"]
    if available:
        assert selected
        for payload in selected:
            assert payload["selected_skill_ids"] == [manifest["skill_id"]]
            assert payload["skill_ref"]["id"] == manifest["skill_id"]
            assert payload["skill_ref"]["version"] == manifest["version_hash"]
        assert evidence["answer"] == FACT
    else:
        assert selected == []
        assert all(p["selected_skill_count"] == 0 for p in summaries)
        assert evidence["answer"] == "SKILL_UNAVAILABLE"
    requests = {
        e["payload"]["call_id"]: e["payload"]
        for e in events
        if e["type"] == "tool.call.requested"
    }
    for request in requests.values():
        name = request["canonical_name"]
        assert name in _TOOLS | _DISCOVERY, f"unplanned capability: {name}"
        if name == "tool.request":
            assert request["sanitized_normalized_arguments"]["name"] in _TOOLS
    reads = []
    for event in events:
        if event["type"] not in {"tool.call.completed", "tool.call.blocked"}:
            continue
        result = event["payload"]
        request = requests[result["call_id"]]
        if event["type"] == "tool.call.blocked":
            assert not available and request["canonical_name"] == "skill.get"
            assert result["error"]["code"] == "NOT_FOUND"
            continue
        if request["canonical_name"] != "skill.get":
            continue
        assert available, "foreign skill read completed successfully"
        output = result["output"]["outputs"]
        assert result["status"] == "success" and output["ok"] is True
        reads.append(("skill.get", request["sanitized_normalized_arguments"], output))
    if available:
        _assert_resource(reads, manifest, RESOURCE)
    else:
        assert FACT not in json.dumps(events), (
            "foreign reference leaked into session events"
        )


def _run_session(
    probe: FocusProbe, scenario: FocusScenario, root: Path, manifest: dict
) -> None:
    root.mkdir()
    evidence = {
        "prompt": scenario.prompt,
        "session_id": probe.session_id,
        "status": "failed",
        "billed_cost": None,
    }
    started = time.monotonic()
    telemetry_path = probe.data_root / "telemetry/telemetry.db"
    before = _events(telemetry_path, 0)
    after_id = before[-1]["id"] if before else 0
    try:
        with probe.session(rows=52, cols=170) as session:
            try:
                probe.wait_ready(session)
                evidence["catalog_display"] = probe.run_slash_turn(
                    session, "/skills", marker=r"Skills:|\(no skills\)", timeout=30
                )
                evidence["detail_display"] = probe.run_slash_turn(
                    session,
                    f"/skills {SKILL_ID}",
                    marker="version" if scenario == SCENARIOS[0] else "NOT_FOUND",
                    timeout=30,
                )
                if scenario == SCENARIOS[0]:
                    assert SKILL_ID in evidence["catalog_display"]
                    assert manifest["version_hash"] in evidence["detail_display"]
                else:
                    assert SKILL_ID not in evidence["catalog_display"]
                    assert manifest["version_hash"] not in evidence["detail_display"]
                    assert "Procedure:" not in evidence["detail_display"]
                transcript = probe.run_turn(session, scenario)
                evidence["answer"] = final_answer_text(transcript, scenario.prompt)
            finally:
                write_transcript(root, "focus", session.visible_transcript)
        events, messages, brain_id = read_focus_evidence(
            probe.environment(), probe.session_id
        )
        _, turn_id = current_turn_events(events, set())
        assert_recorded_answer(
            messages,
            session_id=probe.session_id,
            turn_scope_id=turn_id,
            previous_ids=set(),
            answer=evidence["answer"],
        )
        evidence["brain_events"] = _load_events(
            data_root=probe.data_root, event_session_id=brain_id
        )
        _assert_turn(evidence, manifest, available=scenario == SCENARIOS[0])
        evidence["status"] = "automated_oracles_passed"
    except Exception as exc:
        evidence["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        evidence["elapsed_seconds"] = time.monotonic() - started
        try:
            evidence["daemon_stop_result"] = daemon_stop(
                str(probe.config_path),
                home_root=probe.environment()["OPENMINION_HOME"],
                data_root=probe.data_root,
            )
            assert evidence["daemon_stop_result"] == 0
            telemetry = _events(telemetry_path, after_id)
            evidence["telemetry"] = telemetry
            evidence["usage"] = _usage(telemetry, probe.session_id, probe.agent_id)
            _, _, brain_id = read_focus_evidence(probe.environment(), probe.session_id)
            evidence["brain_events"] = _load_events(
                data_root=probe.data_root, event_session_id=brain_id
            )
        except (OSError, sqlite3.Error, AssertionError) as exc:
            evidence["capture_error"] = str(exc)
            evidence["status"] = "failed"
        (root / "evidence.json").write_text(json.dumps(evidence, indent=2))
    assert "capture_error" not in evidence, evidence.get("capture_error")


def test_live_skill_repository(focus_probe, tmp_path, monkeypatch):
    require_live_focus()
    root = (artifact_root(tmp_path) / focus_probe.session_id / "repository").resolve()
    root.mkdir(parents=True)
    config = json.loads(focus_probe.config_path.read_text())
    agent = config["agents"][focus_probe.agent_id]
    provider = {
        **config["providers"][agent["provider"]],
        **agent.get("provider_config_overrides", {}),
    }
    assert (
        focus_probe.agent_id == "minimax-m2-7" and provider["model"] == "MiniMax-M2.7"
    )
    assert provider["base_url"] == "https://api.minimax.io/v1"
    assert provider.get("api_key") or os.getenv(provider.get("api_key_env", ""))
    assert agent.get("skill") is None and not agent.get("skill_catalog")
    assert not agent.get("structural_tool_results", False)
    agent["action_policy"] = {
        "mode": "ask",
        "allow_read_only_without_prompt": False,
        "rules": [
            {"match": {"tool_name": name}, "mode": "auto"}
            for name in sorted(_TOOLS | _DISCOVERY)
        ],
    }
    focus_probe.config_path.write_text(json.dumps(config))
    for key, value in LIMITS.items():
        monkeypatch.setenv(f"OPENMINION_BRAIN_{key}", str(value))
    probes = []
    for scenario in SCENARIOS:
        repository = root / scenario.scenario_id
        subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
        probe = focus_probe.for_workdir(
            repository, include_project_context=False
        ).for_session(f"{focus_probe.session_id}-{scenario.scenario_id}")
        probe.allow_unsandboxed_exec = False
        assert (
            "--no-context" in probe.command()
            and "--allow-unsandboxed-exec" not in probe.command()
        )
        probes.append(probe)
    stores = [_skill_config(p) for p in probes]
    assert all(c.provider == "sqlite" for c in stores)
    assert (
        Path(stores[0].sqlite_path).resolve() == Path(stores[1].sqlite_path).resolve()
    )
    assert Path(stores[0].blob_root).resolve() == Path(stores[1].blob_root).resolve()
    manifest = _seed_repository(probes[0], probes[0].workdir)
    checks = {
        "a": _scope_reads(probes[0], probes[0].workdir, manifest),
        "b": _scope_reads(probes[1], probes[1].workdir, manifest),
        "no-root": _scope_reads(probes[1], None, manifest),
    }
    packet = {
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=focus_probe.openminion_root, text=True
        ).strip(),
        "source_status": subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=focus_probe.openminion_root, text=True
        ),
        "test_source_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "manifest": manifest,
        "deterministic_checks": checks,
        "sqlite_path": stores[0].sqlite_path,
        "blob_root": stores[0].blob_root,
        "runtime_limits_per_turn": LIMITS,
        "prompts": [s.prompt for s in SCENARIOS],
        "model": provider["model"],
        "agent_id": focus_probe.agent_id,
        "status": "incomplete",
        "completed_sessions": [],
    }
    (root / "manifest.json").write_text(json.dumps(packet, indent=2))
    frozen = {
        str(p): sha256(p.read_bytes()).hexdigest()
        for probe in probes
        for p in probe.workdir.rglob("*")
        if p.is_file()
    }
    packet["workspace_hashes_before"] = frozen
    try:
        for probe, scenario in zip(probes, SCENARIOS, strict=True):
            try:
                _run_session(
                    probe, scenario, root / f"{scenario.scenario_id}-evidence", manifest
                )
                _scope_reads(probe, probe.workdir, manifest)
            finally:
                packet["workspace_hashes_after"] = {
                    str(p): sha256(p.read_bytes()).hexdigest()
                    for pr in probes
                    for p in pr.workdir.rglob("*")
                    if p.is_file()
                }
                packet["workspaces_unchanged"] = (
                    packet["workspace_hashes_after"] == frozen
                )
                assert packet["workspaces_unchanged"], "repository files changed"
            packet["completed_sessions"].append(probe.session_id)
        packet["status"] = "automated_oracles_passed"
    except Exception as exc:
        packet["status"] = "failed"
        packet["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        (root / "manifest.json").write_text(json.dumps(packet, indent=2))
