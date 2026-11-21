"""Provider-free controls for the frozen catalog evidence and approval boundary."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.cli.interactive.terminal.shell.approval import (
    format_terminal_approval_prompt,
)
from openminion.modules.brain.bootstrap.skill.pipeline import (
    _resolve_unique_named_skill_refs,
)
from openminion.modules.brain.bootstrap.skill.selection import (
    _emit_skill_prerouting_failure,
    _emit_skill_selection_event,
)
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus import test_live_skill_catalog as catalog


def test_frozen_corpus_and_implicit_prompt():
    paths = catalog._positive_skill_fixture_paths()
    assert len(paths) == 22
    assert all(p.parent.name not in {"child", "template-negative"} for p in paths)
    entries = [
        {"id": p.parent.name, "name": p.parent.name, "version_hash": "a" * 64}
        for p in paths
    ]
    explicit, implicit = catalog.SCENARIOS[:2]
    assert (
        len(_resolve_unique_named_skill_refs(intent=explicit.prompt, catalog=entries))
        == 1
    )
    assert (
        _resolve_unique_named_skill_refs(intent=implicit.prompt, catalog=entries) == []
    )
    assert [s.timeout for s in catalog.SCENARIOS] == [300, 300, 120, 120, 300]
    assert all(
        s.max_auto_continuations == 0 and s.approval_reply == "yes"
        for s in catalog.SCENARIOS
    )


@pytest.fixture
def resource_evidence():
    content = "bundled source"
    resource = {
        "path": "references/ContentView.swift",
        "sha256": sha256(content.encode()).hexdigest(),
    }
    row = {
        "skill_id": "swiftui-performance-audit",
        "version_hash": "a" * 64,
        "resources": [resource],
    }
    args = {
        "skill_id": row["skill_id"],
        "version_hash": row["version_hash"],
        "resource_path": resource["path"],
    }
    output = {
        "ok": True,
        "skill_id": row["skill_id"],
        "version_hash": row["version_hash"],
        "resource": resource,
        "content": content,
        "truncated": False,
    }
    return row, [("skill.get", args, output)]


@pytest.mark.parametrize(
    "defect", [None, "missing", "unpinned", "wrong-version", "corrupt", "truncated"]
)
def test_resource_oracle_rejects_missing_or_wrong_proof(resource_evidence, defect):
    row, tools = deepcopy(resource_evidence)
    if defect == "missing":
        tools.clear()
    elif defect == "unpinned":
        tools[0][1].pop("version_hash")
    elif defect == "wrong-version":
        tools[0][2]["version_hash"] = "b" * 64
    elif defect == "corrupt":
        tools[0][2]["content"] = "different bytes"
    elif defect == "truncated":
        tools[0][2]["truncated"] = True
    if defect:
        with pytest.raises(AssertionError):
            catalog._assert_resource(tools, row, row["resources"][0]["path"])
    else:
        catalog._assert_resource(tools, row, row["resources"][0]["path"])


@pytest.mark.parametrize(
    ("strategy", "reason", "passes"),
    [
        ("llm", "llm-select", True),
        ("retrieval", "retrieval-select", True),
        ("direct", "direct-named", False),
        ("direct-named", "llm-select", False),
        ("llm", "direct-named", False),
    ],
)
def test_implicit_oracle_rejects_actual_fast_path(
    resource_evidence, tmp_path, strategy, reason, passes
):
    row, tools = resource_evidence
    name, args, output = tools[0]
    events = [
        {
            "type": "skill.prerouting",
            "payload": {"effective_skill_count": 22, "strategy": strategy},
        },
        {
            "type": "skill.selected",
            "payload": {
                "skill_ref": {"id": row["skill_id"], "version": row["version_hash"]},
                "selected_skill_ids": [row["skill_id"]],
                "selection_reason": reason,
            },
        },
        {
            "type": "tool.call.requested",
            "payload": {
                "call_id": "read",
                "canonical_name": name,
                "sanitized_normalized_arguments": args,
            },
        },
        {
            "type": "tool.call.completed",
            "payload": {
                "call_id": "read",
                "status": "success",
                "output": {"outputs": output},
            },
        },
    ]
    evidence = {"brain_events": events, "manifest": [row], "approvals": []}
    (tmp_path / "audit.md").write_text("Findings require independent semantic review.")
    if passes:
        catalog._assert_case("implicit", evidence, tmp_path)
    else:
        with pytest.raises(AssertionError):
            catalog._assert_case("implicit", evidence, tmp_path)


@pytest.mark.parametrize("requested_name", ["skill.get", "exec.run"])
def test_ambiguous_case_allows_only_frozen_tool_activation(tmp_path, requested_name):
    evidence = {
        "manifest": [],
        "approvals": [],
        "brain_events": [
            {
                "type": "skill.prerouting",
                "payload": {"effective_skill_count": 22, "strategy": "llm"},
            },
            {
                "type": "tool.call.requested",
                "payload": {
                    "call_id": "activate",
                    "canonical_name": "tool.request",
                    "sanitized_normalized_arguments": {"name": requested_name},
                },
            },
        ],
    }
    if requested_name == "skill.get":
        catalog._assert_case("ambiguous", evidence, tmp_path)
    else:
        with pytest.raises(AssertionError):
            catalog._assert_case("ambiguous", evidence, tmp_path)


@pytest.mark.parametrize("failure", [None, "model_unavailable", "timeout"])
def test_no_match_accepts_empty_diagnostic_not_selector_failure(tmp_path, failure):
    events = []
    logger = SimpleNamespace(
        emit=lambda name, payload, **kwargs: events.append(
            {"type": name, "payload": payload}
        )
    )
    state = SimpleNamespace(trace_id="oracle")
    _emit_skill_prerouting_failure(
        logger=logger,
        state=state,
        model="fixture",
        token_count=10,
        latency_ms=1,
        fail_closed_reason=failure,
    )
    _emit_skill_selection_event(
        logger=logger,
        state=state,
        model="fixture",
        selection_mode="llm-select",
        selected_refs=[],
        effective_count=22,
        capacity=4,
        routed_intent="What is 17 plus 25?",
        fail_closed_reason=failure,
        context_budget="medium",
        shortlisted_ids=[],
    )
    assert len(events) == 2 and "effective_skill_count" not in events[0]["payload"]
    evidence = {"brain_events": events, "manifest": [], "approvals": [], "answer": "42"}
    if failure:
        with pytest.raises(AssertionError, match="selector failure"):
            catalog._assert_case("no-match", evidence, tmp_path)
    else:
        catalog._assert_case("no-match", evidence, tmp_path)


@pytest.mark.parametrize(
    "case", [s.scenario_id for s in catalog.SCENARIOS if s.requires_approval]
)
def test_artifact_oracle_rejects_missing_files(tmp_path, case):
    with pytest.raises(AssertionError, match="missing regular artifact"):
        catalog._assert_artifacts(tmp_path, case, [])


def test_inventory_shape_and_units_remain_human_review(tmp_path):
    (tmp_path / "inventory.json").write_text(
        json.dumps({"os": "Darwin", "memory": "16 GiB"})
    )
    (tmp_path / "inventory.md").write_text("Darwin; memory: 16 GiB")
    facts = {
        "platform": {"system": "Darwin", "machine": "arm64", "python": "3.11.9"},
        "memory": {"total_bytes": 16 * 1024**3},
        "disk": [{"total_bytes": 100 * 1024**3}],
    }
    tools = [("host.metrics", {}, {"ok": True, "data": facts})]
    catalog._assert_artifacts(tmp_path, "complex-resource", tools)
    with pytest.raises(AssertionError, match="missing captured"):
        catalog._assert_artifacts(tmp_path, "complex-resource", [])
    (tmp_path / "inventory.json").write_text("[]")
    with pytest.raises(AssertionError, match="nonempty JSON object"):
        catalog._assert_artifacts(tmp_path, "complex-resource", tools)


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "outside",
        "symlink",
        "wrong-tool",
        "settled",
        "mismatched-screen",
        "ambiguous",
    ],
)
def test_approval_binds_actual_pending_local_write(tmp_path, defect):
    args = {"path": "audit.md", "content": "source findings"}
    name = "file.write"
    if defect == "outside":
        args["path"] = "../audit.md"
    elif defect == "symlink":
        (tmp_path / "audit.md").symlink_to(tmp_path.parent / "outside.md")
    elif defect == "wrong-tool":
        name = "exec.run"
    request = {
        "type": "tool.call.requested",
        "payload": {
            "call_id": "one",
            "canonical_name": name,
            "sanitized_normalized_arguments": args,
        },
    }
    events = [request]
    if defect == "settled":
        events.append({"type": "tool.call.completed", "payload": {"call_id": "one"}})
    elif defect == "ambiguous":
        events.append(deepcopy(request))
    screen = format_terminal_approval_prompt(name, args)
    if defect == "mismatched-screen":
        screen = format_terminal_approval_prompt(name, {"path": "other.md"})
    if defect:
        with pytest.raises(AssertionError):
            catalog._check_write_approval(events, tmp_path, ("audit.md",), screen)
    else:
        assert (
            catalog._check_write_approval(events, tmp_path, ("audit.md",), screen)[
                "call_id"
            ]
            == "one"
        )
        other = deepcopy(request)
        other["payload"].update(
            call_id="two", sanitized_normalized_arguments={"path": "inventory.md"}
        )
        events.append(other)
        assert (
            catalog._check_write_approval(events, tmp_path, ("audit.md",), screen)[
                "call_id"
            ]
            == "one"
        )


def test_public_admission_preserves_full_agent_catalog(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "agents": {"catalog-test": {"provider": "echo"}},
                "default_agent": "catalog-test",
            }
        )
    )
    root = Path(__file__).resolve().parents[4]
    probe = FocusProbe(
        python_bin=root / ".venv/bin/python3.11",
        openminion_root=root,
        framework_root=root.parent,
        data_root=tmp_path / "data",
        config_path=config,
        agent_id="catalog-test",
        workdir=tmp_path,
        session_id="catalog-oracle",
    )
    before = catalog._fixture_hashes()
    manifest = []
    catalog._seed_catalog(probe, manifest)
    assert len(manifest) == 22
    assert all(
        row["scope"] == "agent" and row["agent_id"] == "catalog-test"
        for row in manifest
    )
    assert all(row["admission"]["admission_state"] == "admitted" for row in manifest)
    assert catalog._fixture_hashes() == before


def test_approval_accepts_wrapping_but_rejects_ambiguous_preview(tmp_path):
    args = {"path": str(tmp_path / "audit.md"), "content": "findings"}
    event = {
        "type": "tool.call.requested",
        "payload": {
            "call_id": "one",
            "canonical_name": "file.write",
            "sanitized_normalized_arguments": args,
        },
    }
    prompt = format_terminal_approval_prompt("file.write", args)
    wrapped = "\n".join(prompt[i : i + 35] for i in range(0, len(prompt), 35))
    assert catalog._check_write_approval([event], tmp_path, ("audit.md",), wrapped)
    other = deepcopy(event)
    other["payload"]["call_id"] = "two"
    other["payload"]["sanitized_normalized_arguments"]["content"] = "different content"
    with pytest.raises(AssertionError, match="bind one pending"):
        catalog._check_write_approval([event, other], tmp_path, ("audit.md",), wrapped)


def test_preflight_failure_still_records_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "require_live_focus", lambda: None)
    monkeypatch.setattr(catalog, "artifact_root", lambda _: tmp_path)
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "agents": {"wrong-agent": {"provider": "echo"}},
                "providers": {"echo": {"model": "not-approved"}},
            }
        )
    )
    root = Path(__file__).resolve().parents[4]
    probe = FocusProbe(
        python_bin=root / ".venv/bin/python3.11",
        openminion_root=root,
        framework_root=root.parent,
        data_root=tmp_path / "data",
        config_path=config,
        agent_id="wrong-agent",
        workdir=tmp_path,
        session_id="failed-preflight",
    )
    with pytest.raises(AssertionError):
        catalog.test_live_skill_catalog(
            probe, catalog.SCENARIOS[0], tmp_path, monkeypatch
        )
    evidence = json.loads(
        (tmp_path / "failed-preflight/explicit/evidence.json").read_text()
    )
    assert evidence["status"] == "failed" and evidence["human_review"] == "required"
    assert evidence["failure"]["type"] == "AssertionError"
    assert evidence["manifest"] == [] and evidence["fixtures_unchanged"]
