"""Real admission/handler controls and synthetic event-oracle checks, no provider."""

from pathlib import Path
import json
import subprocess
from types import SimpleNamespace

import pytest

from openminion.modules.brain.bootstrap.skill.selection import (
    SkillRef,
    _emit_skill_selection_event,
)
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus import test_live_skill_repository as repository

pytestmark = pytest.mark.e2e


@pytest.fixture
def admitted_repository(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "providers": {"echo": {"provider": "echo"}},
                "agents": {"repository-test": {"provider": "echo"}},
                "default_agent": "repository-test",
            }
        )
    )
    source = Path(__file__).resolve().parents[4]
    probes = []
    for name in ("a", "b"):
        workspace = tmp_path / name
        subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
        probes.append(
            FocusProbe(
                python_bin=source / ".venv/bin/python3.11",
                openminion_root=source,
                framework_root=source.parent,
                config_path=config,
                data_root=tmp_path / "shared-data",
                agent_id="repository-test",
                workdir=workspace,
                session_id=name,
                allow_unsandboxed_exec=False,
                include_project_context=False,
            )
        )
    manifest = repository._seed_repository(probes[0], probes[0].workdir)
    return probes, manifest


def test_shared_store_denies_foreign_and_absent_binding(admitted_repository):
    probes, manifest = admitted_repository
    configs = [repository._skill_config(p) for p in probes]
    assert configs[0].sqlite_path == configs[1].sqlite_path
    assert configs[0].blob_root == configs[1].blob_root
    assert probes[0].session_id != probes[1].session_id
    for probe in probes:
        repository._scope_reads(probe, probe.workdir, manifest)
    repository._scope_reads(probes[1], None, manifest)
    repository._scope_reads(probes[0], probes[0].workdir, manifest)


@pytest.mark.parametrize(
    ("available", "defect"),
    [
        (True, None),
        (True, "missing-routing"),
        (True, "failed-selector"),
        (True, "missing-selection"),
        (True, "wrong-version"),
        (True, "missing-resource"),
        (True, "unpinned-resource"),
        (True, "corrupt-resource"),
        (True, "wrong-answer"),
        (True, "wrong-list-id"),
        (True, "wrong-list-version"),
        (False, None),
        (False, "missing-routing"),
        (False, "failed-selector"),
        (False, "foreign-selection"),
        (False, "foreign-read"),
        (False, "foreign-list"),
        (False, "wrong-denial"),
        (False, "wrong-answer"),
        (False, "forbidden-tool"),
    ],
)
def test_turn_oracle_rejects_missing_or_unsafe_proof(
    admitted_repository, available, defect
):
    probes, manifest = admitted_repository
    probe = probes[0] if available else probes[1]
    checks = repository._scope_reads(probe, probe.workdir, manifest)
    events = []
    refs = (
        [SkillRef(manifest["skill_id"], manifest["version_hash"], "direct-named")]
        if available
        else []
    )
    if defect == "foreign-selection":
        refs = [
            SkillRef(manifest["skill_id"], manifest["version_hash"], "direct-named")
        ]
    _emit_skill_selection_event(
        logger=SimpleNamespace(
            emit=lambda name, payload, **_: events.append(
                {"type": name, "payload": payload}
            )
        ),
        state=SimpleNamespace(trace_id="oracle"),
        model="fixture",
        selection_mode="direct-named" if available else "none",
        selected_refs=refs,
        effective_count=int(available),
        capacity=4,
        routed_intent="synthetic oracle control",
        fail_closed_reason="parse_error" if defect == "failed-selector" else None,
        context_budget="medium",
        shortlisted_ids=[r.skill_id for r in refs],
    )
    args = {
        "skill_id": manifest["skill_id"],
        "version_hash": manifest["version_hash"],
        "resource_path": repository.RESOURCE,
    }
    output = checks["resource"]
    if defect == "foreign-read":
        output = repository._scope_reads(probes[0], probes[0].workdir, manifest)[
            "resource"
        ]
    elif defect == "unpinned-resource":
        args.pop("version_hash")
    elif defect == "corrupt-resource":
        output["content"] = "incorrect fact"
    elif defect == "wrong-denial":
        output["error"]["code"] = "INVALID_ARGUMENT"
    listing = checks["listing"]
    if defect == "foreign-list":
        listing = repository._scope_reads(probes[0], probes[0].workdir, manifest)[
            "listing"
        ]
    elif defect == "wrong-list-id":
        listing["skills"][0]["skill_id"] = "foreign_skill"
    elif defect == "wrong-list-version":
        listing["skills"][0]["version_hash"] = "0" * 64
    events.extend(
        [
            {
                "type": "tool.call.requested",
                "payload": {
                    "call_id": "list",
                    "canonical_name": "skill.list",
                    "sanitized_normalized_arguments": {"limit": 50},
                },
            },
            {
                "type": "tool.call.completed",
                "payload": {
                    "call_id": "list",
                    "status": "success",
                    "output": {"outputs": listing},
                },
            },
            {
                "type": "tool.call.requested",
                "payload": {
                    "call_id": "read",
                    "canonical_name": "skill.get",
                    "sanitized_normalized_arguments": args,
                },
            },
            {
                "type": "tool.call.completed" if output["ok"] else "tool.call.blocked",
                "payload": {
                    "call_id": "read",
                    "status": "success",
                    "output": {"outputs": output},
                }
                if output["ok"]
                else {
                    "call_id": "read",
                    "status": "error",
                    "error": output["error"],
                },
            },
        ]
    )
    if defect == "missing-routing":
        events = [e for e in events if e["type"] != "skill.prerouting"]
    elif defect == "missing-selection":
        events = [e for e in events if e["type"] != "skill.selected"]
    elif defect == "wrong-version":
        next(e["payload"] for e in events if e["type"] == "skill.selected")[
            "skill_ref"
        ]["version"] = "0" * 64
    elif defect == "missing-resource":
        events = [e for e in events if not e["type"].startswith("tool.call.")]
    elif defect == "forbidden-tool":
        events.append(
            {
                "type": "tool.call.requested",
                "payload": {
                    "call_id": "write",
                    "canonical_name": "file.write",
                    "sanitized_normalized_arguments": {},
                },
            }
        )
    evidence = {
        "brain_events": events,
        "answer": repository.FACT if available else "SKILL_UNAVAILABLE",
    }
    if defect == "wrong-answer":
        evidence["answer"] = "incorrect"
    if defect:
        with pytest.raises(AssertionError):
            repository._assert_turn(evidence, manifest, available=available)
    else:
        repository._assert_turn(evidence, manifest, available=available)


def test_prompts_and_limits_do_not_leak_fact_or_authorize_effects():
    assert len(repository.SCENARIOS) == 2
    for scenario in repository.SCENARIOS:
        assert repository.FACT not in scenario.prompt
        assert scenario.timeout == 300 and scenario.max_auto_continuations == 0
        assert not scenario.requires_approval and not scenario.include_project_context
    assert repository.LIMITS == {
        "MAX_TOTAL_LLM_TOKENS": 80000,
        "MAX_TICKS": 8,
        "MAX_TOOL_CALLS": 8,
        "MAX_A2A_CALLS": 0,
        "MAX_ELAPSED_MS": 300000,
    }
