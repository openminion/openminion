"""Frozen Focus catalog cases with exact-version and local-artifact evidence."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time

import pytest

from openminion.cli.commands.daemon import daemon_stop
from openminion.cli.interactive.terminal.shell.approval import (
    format_terminal_approval_prompt,
)
from openminion.modules.skill.interfaces import (
    SkillIngestAuthority,
    SkillVerificationEvidence,
)
from openminion.modules.skill.runtime.skill import Skill
from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.cli.focus.harness.assertions import (
    assert_recorded_answer,
    current_turn_events,
    final_answer_text,
    read_focus_evidence,
    visible_text,
)
from tests.e2e.cli.focus.test_live_goal_only import _events, _usage
from tests.e2e.cli.focus.test_live_skill_confirmed_save import _skill_config
from tests.e2e.test_live_skill_dense_catalog_matrix import (
    _load_events,
    _positive_skill_fixture_paths,
)

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(420)]

SCENARIOS = (
    FocusScenario(
        "explicit",
        "Use the swiftui-performance-audit skill. Read its bundled source reference "
        "and write audit.md with two source-backed performance findings. Do not edit the source.",
        timeout=300,
        requires_approval=True,
        max_auto_approvals=1,
        include_project_context=False,
    ),
    FocusScenario(
        "implicit",
        "Audit the bundled SwiftUI view for avoidable render and state-update work. "
        "Read its source reference and write audit.md with two source-backed "
        "performance findings. Do not edit the source.",
        timeout=300,
        requires_approval=True,
        max_auto_approvals=1,
        include_project_context=False,
    ),
    FocusScenario(
        "ambiguous",
        "I need browser regression-testing help. Outline the applicable workflow "
        "options without opening a browser or performing actions.",
        timeout=120,
        include_project_context=False,
    ),
    FocusScenario(
        "no-match",
        "What is 17 plus 25? Reply with only the number.",
        timeout=120,
        include_project_context=False,
    ),
    FocusScenario(
        "complex-resource",
        "Use the system-inventory skill. Read its collection guide, collect local "
        "operating-system, architecture, total memory, total disk, and Python-version "
        "facts, and write inventory.json and inventory.md containing those same facts. "
        "Do not collect credentials, environment variables, or unrelated files.",
        timeout=300,
        requires_approval=True,
        max_auto_approvals=2,
        include_project_context=False,
    ),
)
_OUTPUTS = {
    "explicit": ("audit.md",),
    "implicit": ("audit.md",),
    "ambiguous": (),
    "no-match": (),
    "complex-resource": ("inventory.json", "inventory.md"),
}
_LOCAL_TOOLS = {"skill.get", "host.metrics", "file.write", "respond", "clarify"}
_DISCOVERY_TOOLS = {"tool.request", "tool.search", "tool.list"}


def _fixture_hashes() -> dict[str, str]:
    return {
        str(path): sha256(path.read_bytes()).hexdigest()
        for skill in _positive_skill_fixture_paths()
        for path in sorted(skill.parent.rglob("*"))
        if path.is_file()
    }


def _seed_catalog(probe: FocusProbe, manifest: list[dict]) -> None:
    paths = _positive_skill_fixture_paths()
    assert len(paths) == 22
    authority = SkillIngestAuthority.local_operator(
        surface="test.focus.catalog", principal_id="local:test"
    )
    skill = Skill(_skill_config(probe))
    try:
        assert skill.catalog_summaries(probe.agent_id) == []
        for path in paths:
            skill_id, version, warnings = skill.ingest_file(
                path, scope="agent", agent_id=probe.agent_id, authority=authority
            )
            package = skill.get_skill(skill_id, version)
            assert package.scope == "agent" and package.agent_id == probe.agent_id
            assert (
                package.source_artifact_ref.rsplit("/", 1)[-1]
                == sha256(path.read_bytes()).hexdigest()
            )
            for resource in package.resources:
                assert (
                    resource["sha256"]
                    == sha256((path.parent / resource["path"]).read_bytes()).hexdigest()
                )
            receipt = skill.admit_skill_version(
                skill_id=skill_id,
                version_hash=version,
                expected_active_version_hash=None,
                target_status="verified",
                reason="Frozen adapted fixture admission, not live qualification",
                authority=authority,
                verification_evidence=SkillVerificationEvidence(
                    check="Fixture source and resource hashes match checked-in bytes",
                    result="passed",
                    evidence_ref=path.as_uri(),
                ),
            )
            assert receipt["active_version_hash"] == version
            manifest.append(
                {
                    "fixture": str(path),
                    "skill_id": skill_id,
                    "version_hash": version,
                    "source_artifact_ref": package.source_artifact_ref,
                    "scope": package.scope,
                    "agent_id": package.agent_id,
                    "content_fingerprint": package.to_content_fingerprint(),
                    "resources": package.resources,
                    "admission": receipt,
                    "warnings": warnings,
                }
            )
        catalog = skill.catalog_summaries(probe.agent_id)
        assert {(row["id"], row["version_hash"]) for row in catalog} == {
            (row["skill_id"], row["version_hash"]) for row in manifest
        }
    finally:
        skill.close()


def _check_write_approval(
    events: list[dict],
    workspace: Path,
    outputs: tuple[str, ...],
    screen: str,
) -> dict:
    settled = {
        e["payload"]["call_id"]
        for e in events
        if e["type"] in {"tool.call.completed", "tool.call.blocked"}
    }
    pending = [
        e["payload"]
        for e in events
        if e["type"] == "tool.call.requested" and e["payload"]["call_id"] not in settled
    ]
    shown = (
        "Approval required:" + visible_text(screen).rsplit("Approval required:", 1)[-1]
    )
    matches = [
        request
        for request in pending
        if "".join(
            format_terminal_approval_prompt(
                request["canonical_name"], request["sanitized_normalized_arguments"]
            )
            .splitlines()[0]
            .split()
        )
        in "".join(shown.split())
    ]
    assert len(matches) == 1, "approval must bind one pending request"
    request = matches[0]
    assert request["canonical_name"] == "file.write", "only local file.write approved"
    args = request["sanitized_normalized_arguments"]
    target = Path(args["path"])
    target = target if target.is_absolute() else workspace / target
    assert not target.is_symlink()
    assert target.resolve() in {workspace / name for name in outputs}, "unapproved path"
    return {"call_id": request["call_id"], "path": str(target.resolve())}


def _completed_tools(events: list[dict]) -> list[tuple[str, dict, dict]]:
    requests = {
        e["payload"]["call_id"]: e["payload"]
        for e in events
        if e["type"] == "tool.call.requested"
    }
    completed = []
    for event in events:
        if event["type"] != "tool.call.completed":
            continue
        result = event["payload"]
        request = requests[result["call_id"]]
        assert result["status"] == "success"
        completed.append(
            (
                request["canonical_name"],
                request["sanitized_normalized_arguments"],
                result["output"]["outputs"],
            )
        )
    return completed


def _assert_resource(tools: list[tuple], row: dict, resource_path: str) -> None:
    resource = next(r for r in row["resources"] if r["path"] == resource_path)
    reads = [
        (args, output)
        for name, args, output in tools
        if name == "skill.get" and args.get("resource_path") == resource_path
    ]
    assert reads, "missing successful resource read"
    for args, output in reads:
        assert args["skill_id"] == output["skill_id"] == row["skill_id"]
        assert (
            args.get("version_hash") == output["version_hash"] == row["version_hash"]
        ), "resource read must pin the admitted version"
        assert output["ok"] is True and output["truncated"] is False
        assert output["resource"]["sha256"] == resource["sha256"]
        assert sha256(output["content"].encode()).hexdigest() == resource["sha256"]


def _assert_artifacts(workspace: Path, case: str, tools: list[tuple]) -> None:
    for name in _OUTPUTS[case]:
        path = workspace / name
        assert path.is_file() and not path.is_symlink(), (
            f"missing regular artifact: {name}"
        )
        assert path.stat().st_size > 0
    if case == "complex-resource":
        facts = [output["data"] for name, _, output in tools if name == "host.metrics"]
        assert facts, "missing captured host.metrics facts"
        report = json.loads((workspace / "inventory.json").read_text())
        assert isinstance(report, dict) and report, (
            "inventory must be a nonempty JSON object"
        )
        # Human review compares all five facts and units against this capture.
        captured = facts[-1]
        for key in ("system", "machine", "python"):
            assert captured["platform"][key], f"missing captured {key}"
        assert captured["memory"]["total_bytes"] > 0
        assert captured["disk"] and all(d["total_bytes"] > 0 for d in captured["disk"])


def _assert_case(case: str, evidence: dict, workspace: Path) -> None:
    events = evidence["brain_events"]
    selected = [e["payload"] for e in events if e["type"] == "skill.selected"]
    refs = [p["skill_ref"] for p in selected]
    manifest = {row["skill_id"]: row for row in evidence["manifest"]}
    prerouting = [e["payload"] for e in events if e["type"] == "skill.prerouting"]
    summaries = [p for p in prerouting if "effective_skill_count" in p]
    assert summaries and all(p["effective_skill_count"] == 22 for p in summaries)
    assert all(p.get("fail_closed_reason") is None for p in prerouting), (
        "selector failure is not successful selection or no-match"
    )
    for ref in refs:
        assert ref["version"] == manifest[ref["id"]]["version_hash"]
    tools = _completed_tools(events)
    requested = [e["payload"] for e in events if e["type"] == "tool.call.requested"]
    for request in requested:
        name = request["canonical_name"]
        if name == "tool.request":
            assert request["sanitized_normalized_arguments"]["name"] in _LOCAL_TOOLS
        else:
            assert name in _LOCAL_TOOLS | _DISCOVERY_TOOLS, f"unplanned tool: {name}"
    ids = {
        skill_id for payload in selected for skill_id in payload["selected_skill_ids"]
    }
    approved = {item["call_id"] for item in evidence["approvals"]}
    assert all(
        r["call_id"] in approved
        for r in requested
        if r["canonical_name"] == "file.write"
    ), "write lacks exact test approval"
    if case == "no-match":
        assert evidence["answer"] == "42" and not refs
        assert not [
            r for r in requested if r["canonical_name"] not in {"respond", "clarify"}
        ]
        assert any(
            e["type"] == "skill.prerouting"
            and e["payload"].get("selected_skill_count") == 0
            for e in events
        )
    elif case == "ambiguous":
        assert ids <= {"playwright", "webapp-testing"}
        assert not [
            r
            for r in requested
            if r["canonical_name"]
            not in {"skill.get", "respond", "clarify"} | _DISCOVERY_TOOLS
        ]
    else:
        expected = (
            "system-inventory"
            if case == "complex-resource"
            else "swiftui-performance-audit"
        )
        assert ids == {expected}
        assert all(p["selected_skill_ids"] == [expected] for p in selected)
        if case == "implicit":
            assert all(
                p["strategy"] not in {"direct", "direct-named"} for p in prerouting
            )
            assert all(
                p["selection_reason"] in {"llm-select", "retrieval-select"}
                for p in selected
            )
        resource = (
            "references/collection-guide.md"
            if case == "complex-resource"
            else "references/ContentView.swift"
        )
        _assert_resource(tools, manifest[expected], resource)
    _assert_artifacts(workspace, case, tools)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.scenario_id for s in SCENARIOS])
def test_live_skill_catalog(focus_probe, scenario, tmp_path, monkeypatch):
    require_live_focus()
    root = (
        artifact_root(tmp_path) / focus_probe.session_id / scenario.scenario_id
    ).resolve()
    root.mkdir(parents=True)
    workspace = root / "workspace"
    workspace.mkdir()
    probe = focus_probe.for_workdir(workspace, include_project_context=False)
    probe.allow_unsandboxed_exec = False
    evidence = {
        "case": scenario.scenario_id,
        "prompt": scenario.prompt,
        "status": "failed",
        "human_review": "required",
        "manifest": [],
        "approvals": [],
        "billed_cost": None,
    }
    frozen = _fixture_hashes()
    started = time.monotonic()
    session_started = False
    try:
        config = json.loads(probe.config_path.read_text())
        agent = config["agents"][probe.agent_id]
        provider = {
            **config["providers"][agent["provider"]],
            **agent.get("provider_config_overrides", {}),
        }
        assert (probe.agent_id, provider["model"]) in {
            ("minimax-m2-7", "MiniMax-M2.7"),
            ("hello-agent", "anthropic/claude-haiku-4.5"),
        }
        assert provider.get("api_key") or os.getenv(provider.get("api_key_env", "")), (
            "credential binding unavailable"
        )
        assert agent.get("skill") is None and not agent.get("skill_catalog")
        assert not agent.get("structural_tool_results", False), (
            "resource evidence would be omitted"
        )
        agent["action_policy"] = {
            "mode": "ask",
            "allow_read_only_without_prompt": False,
            "rules": [
                {"match": {"tool_name": name}, "mode": "auto"}
                for name in (
                    "skill.get",
                    "host.metrics",
                    "respond",
                    "clarify",
                    "tool.request",
                    "tool.search",
                    "tool.list",
                )
            ],
        }
        probe.config_path.write_text(json.dumps(config))
        evidence["model"] = provider["model"]
        evidence["agent_id"] = probe.agent_id
        evidence["source_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=probe.openminion_root, text=True
        ).strip()
        evidence["test_source_sha256"] = sha256(Path(__file__).read_bytes()).hexdigest()
        limits = {
            "MAX_TOTAL_LLM_TOKENS": 80000,
            "MAX_TICKS": 8,
            "MAX_TOOL_CALLS": 8,
            "MAX_A2A_CALLS": 0,
            "MAX_ELAPSED_MS": scenario.timeout * 1000,
        }
        evidence["runtime_limits"] = limits
        for key, value in limits.items():
            monkeypatch.setenv(f"OPENMINION_BRAIN_{key}", str(value))
        _seed_catalog(probe, evidence["manifest"])
        (root / "manifest.json").write_text(json.dumps(evidence["manifest"], indent=2))

        def check_approval(kind, screen):
            assert kind != "sidecar", "sidecar activation not authorized"
            _, _, brain_id = read_focus_evidence(probe.environment(), probe.session_id)
            events = _load_events(data_root=probe.data_root, event_session_id=brain_id)
            evidence["approvals"].append(
                _check_write_approval(
                    events, workspace, _OUTPUTS[scenario.scenario_id], screen
                )
            )

        session_started = True
        with probe.session(rows=52, cols=170) as session:
            try:
                probe.wait_ready(session)
                transcript = probe.run_turn(
                    session, scenario, check_approval=check_approval
                )
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
        _assert_case(scenario.scenario_id, evidence, workspace)
        assert _fixture_hashes() == frozen, "checked-in fixture changed"
        evidence["status"] = "automated_oracles_passed"
    except Exception as exc:
        evidence["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        evidence["elapsed_seconds"] = time.monotonic() - started
        evidence["fixture_hashes"] = frozen
        evidence["fixtures_unchanged"] = _fixture_hashes() == frozen
        evidence["artifacts"] = {
            str(p.relative_to(workspace)): sha256(p.read_bytes()).hexdigest()
            for p in workspace.rglob("*")
            if p.is_file() and not p.is_symlink()
        }
        try:
            if session_started:
                evidence["daemon_stop_result"] = daemon_stop(
                    str(probe.config_path),
                    home_root=probe.environment()["OPENMINION_HOME"],
                    data_root=probe.data_root,
                )
                assert evidence["daemon_stop_result"] == 0, "case daemon did not stop"
            telemetry = _events(probe.data_root / "telemetry/telemetry.db", 0)
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
