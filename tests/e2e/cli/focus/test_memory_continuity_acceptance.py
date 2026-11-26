"""Offline boundary checks for the separately gated memory campaign."""

from __future__ import annotations

import socket
import subprocess
import json
import hashlib
import io
import platform
from types import SimpleNamespace

import pytest

from openminion.api.runtime import APIRuntime
from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.memory import REGISTRAR
from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.test_memory_capture_recall_reliability import _echo_config
from tests.e2e.cli.focus.harness.memory_campaign import (
    digest,
    load_campaign,
    process_slots,
    read_state,
    restricted_runtime,
    _carrier_inventory,
    run_phase,
)
from tests.e2e.cli.focus.test_live_memory_continuity import (
    _campaign_prompt,
    _phase_outcomes,
)

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def no_live_effects(monkeypatch):
    def deny(*_args, **_kwargs):
        pytest.fail("offline preflight attempted network or subprocess execution")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(subprocess, "Popen", deny)


def test_memory_campaign_without_live_opt_in_is_denied(monkeypatch) -> None:
    monkeypatch.delenv("OPENMINION_LIVE_CLI_FOCUS_E2E", raising=False)
    with pytest.raises(pytest.skip.Exception, match="not set"):
        require_live_focus()


def test_restricted_tools_deny_inspection_and_preserve_memory_operations(
    tmp_path,
) -> None:
    runtime = APIRuntime.from_config_path(str(_echo_config(tmp_path)))
    registry = ToolRegistry()
    REGISTRAR.register(registry)
    try:
        adapter = ToolAdapter(
            workspace_root=tmp_path,
            runtime_registry=registry,
            memory_service=runtime.runtime_memory_assembly.service,
            agent_id=runtime.runtime_memory_assembly.gateway._agent_id,
        )
        with adapter.restrict_tools(("memory.write", "memory.search", "memory.forget")):
            for name in ("file.read", "exec.run", "ops.file.read", "exec.list"):
                denied = adapter.execute(
                    command={"tool_name": name, "args": {}},
                    session_id="csma-preflight",
                    trace_id="csma-preflight",
                )
                assert denied["status"] == "error"
                assert denied["error"]["code"] == "POLICY_DENIED"
            written = adapter.execute(
                command={
                    "tool_name": "memory.write",
                    "args": {
                        "record_type": "fact",
                        "key": "fact:preflight",
                        "title": "Preflight fact",
                        "content": "synthetic fixture",
                    },
                },
                session_id="csma-preflight",
                trace_id="csma-preflight",
            )
            assert written["status"] == "success", written
            record_id = written["outputs"]["data"]["record_id"]
            found = adapter.execute(
                command={"tool_name": "memory.search", "args": {"query": "synthetic"}},
                session_id="csma-preflight",
                trace_id="csma-preflight",
            )
            assert found["status"] == "success"
            assert [row["id"] for row in found["outputs"]["data"]["records"]] == [
                record_id
            ]
            forgotten = adapter.execute(
                command={
                    "tool_name": "memory.forget",
                    "args": {"record_id": record_id, "reason": "fixture complete"},
                },
                session_id="csma-preflight",
                trace_id="csma-preflight",
            )
            assert forgotten["status"] == "success"
            assert forgotten["outputs"]["data"]["deleted"]
    finally:
        runtime.close()


def _manifest(tmp_path):
    config = tmp_path / "config.json"
    config.write_text("{}")
    path = tmp_path / "manifest.json"
    payload = {
        "approval_ready": True,
        "python_version": platform.python_version(),
        "dependencies": {},
        "source_files_sha256": {},
        "process_slots": process_slots(),
        **{
            f"{name}_{field}": str(config) if field == "path" else digest(config)
            for name in ("config", "corpus", "credential_profile")
            for field in ("path", "sha256")
        },
    }
    path.write_text(json.dumps(payload))
    return path, payload


def test_campaign_requires_both_opt_in_and_exact_manifest_approval(
    tmp_path, monkeypatch
):
    path, _ = _manifest(tmp_path)
    monkeypatch.delenv("CSMA_APPROVED_MANIFEST_SHA256", raising=False)
    monkeypatch.setenv("OPENMINION_LIVE_CLI_FOCUS_E2E", "1")
    with pytest.raises(ValueError, match="Gate B"):
        load_campaign(path)
    monkeypatch.setenv("CSMA_APPROVED_MANIFEST_SHA256", digest(path))
    monkeypatch.delenv("OPENMINION_LIVE_CLI_FOCUS_E2E")
    with pytest.raises(ValueError, match="Gate B"):
        load_campaign(path)


def test_campaign_binds_inputs_and_all_process_slots(tmp_path):
    path, payload = _manifest(tmp_path)
    assert len(load_campaign(path, require_approval=False)["process_slots"]) == 120
    assert len(set(process_slots())) == 120
    payload["process_slots"].pop()
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="process inventory"):
        load_campaign(path, require_approval=False)
    payload["process_slots"] = process_slots()
    payload["config_sha256"] = "changed"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="config"):
        load_campaign(path, require_approval=False)


def test_campaign_never_replaces_an_attempt(tmp_path):
    slot = process_slots()[0]
    (tmp_path / f"{slot}.claimed").touch()
    with pytest.raises(FileExistsError):
        run_phase(
            manifest={"process_slots": process_slots()},
            slot=slot,
            config_path=tmp_path,
            data_root=tmp_path,
            workdir=tmp_path,
            evaluator_root=tmp_path,
            prompt="unused",
            target_hashes={},
            deadline_monotonic=100000000,
        )


def test_consumer_and_forget_prompts_have_no_oracle():
    tokens = {"old": "old-value", "new": "new-value", "distractor": "unrelated-value"}
    for phase in ("recall", "forget"):
        prompt = _campaign_prompt("forget", phase, tokens, "record-1")
        assert all(token not in prompt for token in tokens.values())


@pytest.mark.parametrize(
    "events", [["request", "completed"], ["request"], ["rejected", "completed"]]
)
def test_run_phase_waits_for_shutdown_and_rejects_incomplete_observation(
    tmp_path, monkeypatch, events
):
    from tests.e2e.cli.focus.harness import memory_campaign

    waits = []

    class Session:
        env = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def type_line(self, line):
            assert line == "/exit"

        def wait(self, timeout):
            waits.append(timeout)
            return 0

    slot = process_slots()[0]
    config = tmp_path / "config.json"
    config.write_text("{}")

    def turn(*args):
        (tmp_path / f"{slot}.jsonl").write_text(
            "\n".join(json.dumps({"event": e}) for e in events)
        )
        return "fixture transcript"

    probe = SimpleNamespace(
        session=Session, wait_ready=lambda *args, **kwargs: None, run_turn=turn
    )
    monkeypatch.setattr(memory_campaign, "FocusProbe", lambda **kwargs: probe)
    monkeypatch.setattr(memory_campaign, "final_answer_text", lambda *args: "done")
    manifest = {
        "process_slots": process_slots(),
        "per_process": {"lifetime_seconds": 180},
        "endpoint": "https://invalid.example/chat/completions",
        "model": "fixture",
        "agent_id": "csma",
    }
    kwargs = dict(
        manifest=manifest,
        slot=slot,
        config_path=config,
        data_root=tmp_path,
        workdir=tmp_path,
        evaluator_root=tmp_path,
        prompt="fixture",
        target_hashes={},
        deadline_monotonic=100000000,
    )
    if events == ["request", "completed"]:
        assert run_phase(**kwargs)[0] == "done"
    else:
        with pytest.raises(AssertionError, match="incomplete_request_observation"):
            run_phase(**kwargs)
    assert waits == [180]
    assert (tmp_path / f"{slot}.claimed").exists()


def _phase_evidence():
    return (
        {"old": "old-value", "new": "new-value", "distractor": "other-value"},
        [
            {
                "event": "request",
                "canonical": {"llm_call_id": "call-1"},
                "target_matches": ["new"],
            }
        ],
        {
            "session_id": "producer",
            "memory_write_events": [],
            "retained_carriers": [],
            "response_ids": ["outbound-1"],
            "response_hashes": [
                hashlib.sha256(value.encode()).hexdigest()
                for value in ("new-value", "UNKNOWN", "stored", "ack")
            ],
            "records": [
                {
                    "id": "new-record",
                    "type": "fact",
                    "active": True,
                    "continuity_key": True,
                    "weather_key": False,
                    "matches": ["new"],
                }
            ],
            "context_manifests": [
                {
                    "llm_call_id": "call-1",
                    "recalled_memory": ["new-record"],
                    "facts": [],
                    "memory": [],
                    "recent_session_artifacts": [],
                }
            ],
            "candidates": [],
            "capture": [],
        },
    )


@pytest.mark.parametrize("case", ["correction", "forget"])
@pytest.mark.parametrize(
    "field", ["recalled_memory", "facts", "memory", "recent_session_artifacts"]
)
def test_retired_record_id_fails_even_without_nonce_exposure(case, field):
    tokens, requests, state = _phase_evidence()
    if case == "forget":
        requests[0]["target_matches"] = []
    state["context_manifests"][0][field].append("old-record")
    outcomes, _ = _phase_outcomes(
        case,
        "recall",
        "memory",
        tokens,
        "old-record",
        tokens["new"] if case == "correction" else "UNKNOWN",
        requests,
        state,
    )
    assert outcomes["answer_correct"]
    assert outcomes["no_forbidden_exposure"]
    assert not outcomes["old_record_not_recalled"]


@pytest.mark.parametrize(
    "answer",
    [
        "new-value",
        "prefix new-value",
        "new-value suffix",
        "old-value",
        "UNKNOWN",
        "UNKNOWN extra",
        "",
    ],
)
def test_recall_acceptance_requires_exact_answer_despite_valid_exposure(answer):
    tokens, requests, state = _phase_evidence()
    state["response_hashes"] = [hashlib.sha256(answer.encode()).hexdigest()]
    outcomes, _ = _phase_outcomes(
        "correction", "recall", "memory", tokens, "old-record", answer, requests, state
    )
    assert outcomes["recorded_answer_matches"]
    assert outcomes["required_exposure"] and outcomes["target_context_join"]
    assert outcomes["answer_correct"] == (answer == tokens["new"])
    assert all(outcomes.values()) == (answer == tokens["new"])


def test_native_context_join_cannot_be_replaced_with_observational_id():
    tokens, requests, state = _phase_evidence()
    requests[0]["canonical"] = {}
    requests[0]["observer_session_id"] = "call-1"
    outcomes, _ = _phase_outcomes(
        "correction",
        "recall",
        "memory",
        tokens,
        "old-record",
        tokens["new"],
        requests,
        state,
    )
    assert not outcomes["native_context_join"]
    assert not outcomes["target_context_join"]


def test_unjoined_exposure_cannot_substitute_for_record_context_join():
    tokens, requests, state = _phase_evidence()
    requests.append({"event": "request", "canonical": {}, "target_matches": ["new"]})
    requests[0]["target_matches"] = []
    outcomes, _ = _phase_outcomes(
        "correction",
        "recall",
        "memory",
        tokens,
        "old-record",
        "new-value",
        requests,
        state,
    )
    assert outcomes["native_context_join"] and outcomes["required_exposure"]
    assert not outcomes["target_context_join"]
    requests[0]["target_matches"] = ["new"]
    state["context_manifests"][0]["recalled_memory"] = []
    outcomes, _ = _phase_outcomes(
        "correction",
        "recall",
        "memory",
        tokens,
        "old-record",
        "new-value",
        requests,
        state,
    )
    assert not outcomes["target_context_join"]


def test_relevance_requires_a_distinct_distractor_record():
    tokens, requests, state = _phase_evidence()
    state["records"][0]["matches"] = ["old", "distractor"]
    outcomes, _ = _phase_outcomes(
        "relevance", "teach", "memory", tokens, None, "stored", requests, state
    )
    assert outcomes["unique_public_write"]
    assert not outcomes["distractor_stored"]


def _automatic_evidence():
    tokens, requests, state = _phase_evidence()
    state["candidates"] = [
        {
            "id": "candidate-1",
            "status": "proposed",
            "capture_id": "capture-1",
            "root_turn_id": "turn-1",
            "matches": ["old"],
        }
    ]
    state["capture"] = [
        {
            "capture_id": "capture-1",
            "disposition": "succeeded",
            "output_ids": ["candidate-1"],
        }
    ]
    state["records"] = [
        {
            "id": "summary-1",
            "type": "session_summary",
            "active": True,
            "continuity_key": False,
            "matches": ["old"],
        }
    ]
    return tokens, requests, state


def test_automatic_capture_does_not_conflate_summary_copy_with_admission():
    tokens, requests, state = _automatic_evidence()
    outcomes, _ = _phase_outcomes(
        "automatic", "teach", "memory", tokens, None, "ack", requests, state
    )
    assert outcomes["capture_lineage"] and outcomes["not_admitted"]
    state["capture"][0]["capture_id"] = "different-capture"
    outcomes, _ = _phase_outcomes(
        "automatic", "teach", "memory", tokens, None, "ack", requests, state
    )
    assert not outcomes["capture_lineage"]
    state["candidates"][0]["status"] = "promoted"
    outcomes, _ = _phase_outcomes(
        "automatic", "teach", "memory", tokens, None, "ack", requests, state
    )
    assert not outcomes["not_admitted"]
    outcomes, _ = _phase_outcomes(
        "automatic",
        "recall",
        "memory",
        tokens,
        None,
        "UNKNOWN",
        [{**requests[0], "target_matches": ["old"]}],
        state,
    )
    assert not outcomes["no_forbidden_exposure"]


@pytest.mark.parametrize("status", ["running", "succeeded", "failed"])
def test_automatic_capture_rejects_explicit_write_even_with_proposed_candidate(status):
    tokens, requests, state = _automatic_evidence()
    state["memory_write_events"] = [{"tool_call_id": "write-1", "status": status}]
    outcomes, _ = _phase_outcomes(
        "automatic", "teach", "memory", tokens, None, "ack", requests, state
    )
    assert outcomes["capture_lineage"] and outcomes["not_admitted"]
    assert not outcomes["no_explicit_write_attempt"]
    assert not all(outcomes.values())


def test_proposed_status_does_not_hide_active_target_fact_or_missing_candidate():
    tokens, requests, state = _automatic_evidence()
    state["records"].append(
        {
            "id": "fact-1",
            "type": "fact",
            "active": True,
            "continuity_key": True,
            "matches": ["old"],
        }
    )
    outcomes, _ = _phase_outcomes(
        "automatic", "teach", "memory", tokens, None, "ack", requests, state
    )
    assert outcomes["capture_lineage"] and outcomes["no_explicit_write_attempt"]
    assert not outcomes["not_admitted"]
    state["records"].pop()
    state["candidates"] = []
    outcomes, _ = _phase_outcomes(
        "automatic", "teach", "memory", tokens, None, "ack", requests, state
    )
    assert not outcomes["not_admitted"]


@pytest.mark.parametrize("arm", ["memory", "control"])
@pytest.mark.parametrize("copy", ["none", "record", "episode", "missing_inventory"])
def test_forget_qualification_requires_clean_preconsumer_inventory(arm, copy):
    tokens, requests, state = _phase_evidence()
    requests[0]["target_matches"] = []
    old_record = {"id": "old-record", "active": True, "matches": ["old"]}
    producer_states = [
        {"session_id": "teach", "records": [old_record], "retained_carriers": []},
        {
            "session_id": "forget",
            "records": [{**old_record, "active": False}],
            "retained_carriers": [
                {"kind": kind, "eligibility": eligibility, "matches": ["old"]}
                for kind, eligibility in (
                    ("transcript", "protected_history"),
                    ("rolling_context", "session_local"),
                    ("working_state", "resume_only"),
                )
            ]
            + [
                {
                    "kind": "episode",
                    "eligibility": "scope_key_filtered",
                    "scope_key": "session:teach",
                    "matches": ["old"],
                }
            ],
        },
    ]
    if copy == "record":
        producer_states[-1]["records"].append(
            {"id": "independent-summary", "active": True, "matches": ["old"]}
        )
    elif copy == "episode":
        producer_states[-1]["retained_carriers"].append(
            {
                "kind": "episode",
                "eligibility": "scope_key_filtered",
                "scope_key": "agent:csma",
                "matches": ["old"],
            }
        )
    elif copy == "missing_inventory":
        producer_states = []
    outcomes, _ = _phase_outcomes(
        "forget",
        "recall",
        arm,
        tokens,
        "old-record",
        "UNKNOWN",
        requests,
        state,
        producer_states=producer_states,
    )
    assert outcomes["answer_correct"] and outcomes["no_forbidden_exposure"]
    assert outcomes["old_record_not_recalled"]
    assert outcomes["forget_inventory_present"] == (copy != "missing_inventory")
    assert outcomes["forget_clean_fixture"] == (copy == "none")
    assert all(outcomes.values()) == (copy == "none")


def test_read_state_exports_only_memory_write_event_facts(tmp_path, monkeypatch):
    from tests.e2e.cli.focus.harness import assertions, memory_campaign

    events = [
        SimpleNamespace(
            event_id=f"event-{index}",
            event_type=f"tool.execution.{stage}",
            data={
                "tool_name": tool,
                "tool_call_id": "write-1",
                "status": status,
                "arguments": "private-target",
                "output": "private-target",
            },
        )
        for index, (tool, stage, status) in enumerate(
            [
                ("memory.write", "started", "running"),
                ("memory.write", "completed", "succeeded"),
                ("memory.write", "failed", "failed"),
                ("memory.search", "completed", "succeeded"),
            ]
        )
    ]
    monkeypatch.setattr(
        assertions, "read_focus_evidence", lambda *a: (events, [], "brain")
    )
    monkeypatch.setattr(memory_campaign, "_carrier_inventory", lambda *a: [])
    state = read_state(
        _echo_config(tmp_path), tmp_path / "data", "producer", {"old": "private-target"}
    )
    assert state["memory_write_events"] == [
        {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "tool_call_id": "write-1",
            "status": event.data["status"],
        }
        for event in events[:3]
    ]
    assert "private-target" not in json.dumps(state)


def test_inherited_identity_cannot_mutate_external_profile(tmp_path, monkeypatch):
    external = tmp_path / "external.db"
    external.write_bytes(b"external sentinel")
    monkeypatch.setenv("OPENMINION_IDENTITY_DB", str(external))
    with pytest.raises(ValueError, match="inherited identity"):
        restricted_runtime(tmp_path / "absent.json", agent_id="csma")
    assert external.read_bytes() == b"external sentinel"


@pytest.mark.parametrize(
    "name,value",
    [
        ("OPENMINION_MEMORY_PROVIDER", "memory_v2_smoke"),
        ("OPENMINION_MEMORY_CAPSULE_STRATEGY", "frozen_session"),
        ("OPENMINION_MEMORY_DYNAMIC_RETRIEVAL_ENABLED", "1"),
    ],
)
def test_memory_overrides_rejected_before_loading_or_runtime(
    tmp_path, monkeypatch, name, value
):
    monkeypatch.setenv(name, value)
    monkeypatch.delenv("OPENMINION_LIVE_CLI_FOCUS_E2E", raising=False)
    monkeypatch.delenv("CSMA_APPROVED_MANIFEST_SHA256", raising=False)
    missing = tmp_path / "absent.json"
    for require_approval in (False, True):
        with pytest.raises(ValueError, match="memory overrides"):
            load_campaign(missing, require_approval=require_approval)
    with pytest.raises(ValueError, match="memory overrides"):
        restricted_runtime(missing, agent_id="csma")
    with pytest.raises(ValueError, match="memory overrides"):
        read_state(missing, tmp_path / "data", "producer", {})
    assert not (tmp_path / "data").exists()


def test_unset_memory_overrides_preserve_offline_loading_and_gate_b_denial(
    tmp_path, monkeypatch
):
    for name in (
        "OPENMINION_IDENTITY_DB",
        "OPENMINION_IDENTITY_ROOT",
        "OPENMINION_MEMORY_PROVIDER",
        "OPENMINION_MEMORY_CAPSULE_STRATEGY",
        "OPENMINION_MEMORY_DYNAMIC_RETRIEVAL_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("OPENMINION_LIVE_CLI_FOCUS_E2E", raising=False)
    monkeypatch.delenv("CSMA_APPROVED_MANIFEST_SHA256", raising=False)
    path, payload = _manifest(tmp_path)
    assert load_campaign(path, require_approval=False) == payload
    with pytest.raises(ValueError, match="Gate B"):
        load_campaign(path)


@pytest.mark.parametrize(
    "answer,containing,exact",
    [
        (None, [], []),
        ("synthetic-private-value", ["old"], ["old"]),
        ("prefix synthetic-private-value suffix", ["old"], []),
        ("UNKNOWN", [], []),
        ("UNKNOWN extra", [], []),
        ("wrong", [], []),
        ("", [], []),
        ("new-value", ["new"], ["new"]),
        ("synthetic-private-value new-value", ["old", "new"], []),
    ],
)
@pytest.mark.parametrize("readback_succeeds", [False, True])
def test_campaign_retains_content_free_report_and_cleans_private_roots(
    tmp_path, monkeypatch, answer, containing, exact, readback_succeeds
):
    from tests.e2e.cli.focus.harness import memory_campaign
    from tests.e2e.cli.focus import test_live_memory_continuity as live

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"storage": {}, "identity": {}, "runtime": {}}))
    corpus = tmp_path / "corpus.json"
    secret = "synthetic-private-value"
    corpus.write_text(
        json.dumps(
            {"explicit-01": {"old": secret, "new": "new-value", "distractor": "other"}}
        )
    )
    credentials = tmp_path / "credentials.json"
    credentials.write_text("{}")
    run_root = tmp_path / "run"
    manifest = {
        "run_root": str(run_root),
        "config_path": str(config),
        "corpus_path": str(corpus),
        "credential_profile_path": str(credentials),
        "elapsed_seconds": 60,
        "max_admission_seconds": 60,
    }
    monkeypatch.setenv("CSMA_MANIFEST", "unused")
    monkeypatch.setenv("MINIMAX_API_KEY", "offline")
    monkeypatch.setattr(live, "require_live_focus", lambda: None)
    monkeypatch.setattr(memory_campaign, "load_campaign", lambda _: manifest)
    monkeypatch.setattr(memory_campaign, "PHASES", {"explicit": ("recall",)})

    def run(**kwargs):
        (kwargs["data_root"] / "private.txt").write_text(secret)
        if answer is not None:
            _, requests, _ = _phase_evidence()
            requests[0]["target_matches"] = ["old"]
            return answer, requests
        raise RuntimeError(secret)

    def readback(*args):
        if not readback_succeeds:
            raise RuntimeError(secret)
        _, _, state = _phase_evidence()
        state["response_hashes"] = [hashlib.sha256(answer.encode()).hexdigest()]
        state["records"][0]["matches"] = ["old"]
        return state

    monkeypatch.setattr(memory_campaign, "run_phase", run)
    monkeypatch.setattr(memory_campaign, "read_state", readback)
    finalizers = []
    with pytest.raises(AssertionError, match="phase_failed") as error:
        live.test_live_focus_memory_campaign(
            SimpleNamespace(addfinalizer=finalizers.append)
        )
    assert secret not in str(error.value)
    report = (
        run_root / "evaluator" / "explicit-01-memory-recall.result.json"
    ).read_text()
    payload = json.loads(report)
    if answer is None or not readback_succeeds:
        assert payload["status"] == "incomplete"
    else:
        assert payload["status"] == ("passed" if answer == secret else "failed")
    if answer is None:
        assert "answer_observation" not in payload
    else:
        assert payload["answer_observation"] == {
            "character_count": len(answer),
            "containing_labels": containing,
            "exact_labels": exact,
            "exact_unknown": answer == "UNKNOWN",
        }
    assert secret not in report
    assert "new-value" not in report
    assert not (run_root / "agents" / "explicit-01-memory").exists()
    for finalize in finalizers:
        finalize()
    assert not (run_root / "agents").exists()
    assert (run_root / "evaluator").exists()


@pytest.mark.parametrize(
    "carrier",
    [
        "transcript",
        "rolling",
        "pinned",
        "working",
        "episode_title",
        "episode_text",
        "episode_context",
    ],
)
def test_carrier_inventory_is_complete_without_exporting_content(
    tmp_path, monkeypatch, carrier
):
    from openminion.modules.session.storage import store

    token = "private-fixture-nonce"

    def selected(name):
        return token if carrier == name else "unrelated"

    working = {
        "state_ref": None,
        "state_inline": {"goal": selected("working")},
        "version": 1,
    }
    monkeypatch.setattr(
        store,
        "SQLiteSessionStore",
        lambda _: SimpleNamespace(
            get_latest_working_state=lambda *a, **kw: working, close=lambda: None
        ),
    )
    episode = {
        "doc_id": "doc-1",
        "scope_key": "session:producer",
        "title": selected("episode_title"),
        "unit_id": "unit-1",
        "fts_text": "",
        "text_ref": "text",
        "context_text_ref": "context",
    }
    blobs = {"text": selected("episode_text"), "context": selected("episode_context")}
    runtime = SimpleNamespace(
        storage_path=tmp_path / "state.db",
        config=SimpleNamespace(default_agent="csma"),
        sessions=SimpleNamespace(
            get_session_context=lambda **kw: SimpleNamespace(
                version=1,
                rolling_summary=selected("rolling"),
                summary_short="",
                pinned_context=selected("pinned"),
            )
        ),
        retrieve_ctl=SimpleNamespace(
            store=SimpleNamespace(fetchall=lambda _: [episode]),
            blob_store=SimpleNamespace(
                open=lambda ref: io.BytesIO(blobs[ref].encode())
            ),
        ),
    )
    messages = [
        SimpleNamespace(
            id="message-1", body="", metadata={"value": selected("transcript")}
        )
    ]
    rows = _carrier_inventory(
        runtime, "producer", "brain-producer", messages, {"target": token}
    )
    assert sum(row["matches"] == ["target"] for row in rows) == 1
    assert token not in json.dumps(rows)
    working.update(state_ref="external", state_inline=None)
    with pytest.raises(AssertionError, match="external_working_state"):
        _carrier_inventory(
            runtime, "producer", "brain-producer", messages, {"target": token}
        )
    working.update(state_ref=None, state_inline={})
    del blobs["context"]
    with pytest.raises(KeyError):
        _carrier_inventory(
            runtime, "producer", "brain-producer", messages, {"target": token}
        )
