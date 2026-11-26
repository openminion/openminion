"""Fresh Focus children use configured OpenAI transport against loopback only.

PYTEST_DONT_REWRITE keeps request bodies out of assertion failure reports.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
import time
from uuid import uuid4

import pytest

from openminion.base.config import OpenMinionConfig, save_config
from tests._csc_fixtures import _csc_install_default_agent
from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario
from tests.e2e.cli.focus.harness.assertions import final_answer_text
from tests.e2e.cli.focus.harness.memory_campaign import read_state
from tests.e2e.cli.focus.test_live_memory_continuity import _phase_outcomes
from tests.runtime.test_turn_capture_authorization import (
    _Provider,
    _call,
    _completion,
    _text,
)

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(240)]
MODEL = "MiniMax-M3.1-Flash"
ENTRYPOINT = "tests.e2e.cli.focus.harness.memory_campaign"


class _MemoryProvider(_Provider):
    def __init__(self):
        self.phase = "teach"
        self.tool_sent = False
        self.bodies = []
        self.kinds = []
        self.answer = ""
        self.forbidden_attempts = 0
        super().__init__()

    def respond(self, body):
        self.bodies.append(body)
        functions = [tool["function"] for tool in body.get("tools", [])]
        structured = next(
            (tool for tool in functions if tool["name"] == "submit_output"), None
        )
        text = _text(body)
        if structured:
            schema = structured["parameters"]
            title = schema.get("title", "")
            self.kinds.append(title)
            if title == "UserMessageCandidateReport":
                match = re.search(r"continuity code is ([0-9a-f]{32})", text)
                items = (
                    []
                    if match is None
                    else [
                        {
                            "kind": "fact",
                            "normalized_key": "fact:continuity_code",
                            "title": "Continuity code",
                            "content": f"The continuity code is {match[1]}.",
                            "tags": [],
                        }
                    ]
                )
                return _call("submit_output", {"items": items})
            if title == "FreshnessContract":
                return _call(
                    "submit_output",
                    {
                        "intent": "memory fixture",
                        "domain": "general",
                        "time_sensitive": False,
                        "needs_live_data": False,
                        "needs_sources": False,
                        "needs_exact_date": False,
                        "answer_mode": "direct",
                        "reason": "Local memory only.",
                        "confidence": 1.0,
                    },
                )
            if title == "ClosureJudgment":
                return _call(
                    "submit_output",
                    {
                        "satisfied": True,
                        "next_action": "close",
                        "reason": "Fixture operation completed.",
                        "final_answer": self.answer,
                        "memory_use_refs": [],
                        "mutation_claimed": False,
                    },
                )
            raise AssertionError(f"unexpected fixture schema: {title}")

        self.kinds.append("turn")
        assert {tool["name"] for tool in functions} <= {
            "memory_write",
            "memory_search",
            "memory_forget",
        }
        if not self.tool_sent:
            self.tool_sent = True
            if self.phase == "teach":
                match = re.search(r"continuity code is ([0-9a-f]{32})", text)
                assert match is not None
                assert "memory_write" in {tool["name"] for tool in functions}
                return _call(
                    "memory_write",
                    {
                        "record_type": "fact",
                        "key": "fact:continuity_code",
                        "title": "Continuity code",
                        "content": f"The continuity code is {match[1]}.",
                    },
                )
            self.forbidden_attempts += 1
            return _call("file_read", {"path": "forbidden.txt"})
        if self.phase == "teach":
            self.answer = "CSMA_STORED"
        else:
            match = re.search(r"continuity code is ([0-9a-f]{32})", text)
            self.answer = match[1] if match else "UNKNOWN"
        return _completion(
            {
                "role": "assistant",
                "content": self.answer
                + '\n<finalization_status>{"status":"final_answer",'
                '"reasoning":"Fixture completed."}</finalization_status>',
            },
            "stop",
        )


def _focus_child(probe, descriptor, prompt, monkeypatch):
    spec_path = Path(descriptor["evidence_path"]).with_suffix(".json")
    spec_path.write_text(json.dumps(descriptor), encoding="utf-8")
    monkeypatch.setenv("CSMA_CHILD_SPEC", str(spec_path))
    with probe.session() as session:
        pid = session.process_id
        stage = "startup"
        try:
            probe.wait_ready(session, timeout=15)
            stage = "turn"
            transcript = probe.run_turn(
                session,
                FocusScenario(
                    scenario_id=probe.session_id,
                    prompt=prompt,
                    expected_markers=(),
                    requires_approval=True,
                    timeout=30,
                    include_project_context=False,
                ),
            )
            answer = final_answer_text(transcript, prompt)
            stage = "shutdown"
            session.type_line("/exit")
            assert session.wait(timeout=15) == 0
        except Exception as exc:
            raise AssertionError(
                f"Focus fixture {stage} failed: {type(exc).__name__}"
            ) from None
    evidence_text = Path(descriptor["evidence_path"]).read_text()
    return pid, answer, evidence_text


def _observed_requests(evidence, bodies, targets):
    assert all(token not in evidence for token in targets.values())
    rows = [json.loads(line) for line in evidence.splitlines()]
    assert rows[-1]["event"] == "completed"
    assert sum(row["event"] == "completed" for row in rows) == 1
    assert not any(row["event"] == "rejected" for row in rows)
    requests = [row for row in rows if row["event"] == "request"]
    assert len(requests) == len(bodies) > 0
    assert rows[-1]["reservations"] == len(requests)
    for request, body in zip(requests, bodies, strict=True):
        assert request["target_matches"] == sorted(
            label for label, token in targets.items() if token in json.dumps(body)
        )
    return requests


def test_fresh_focus_children_join_memory_capture_context_and_denied_tool(
    tmp_path,
    monkeypatch,
    python_bin,
    openminion_root,
    framework_root,
):
    provider = _MemoryProvider()
    try:
        nonce = uuid4().hex
        forbidden = uuid4().hex
        targets = {"old": nonce, "forbidden": forbidden}
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "forbidden.txt").write_text(forbidden)
        data_root = tmp_path / "data"
        evaluator = tmp_path / "evaluator"
        evaluator.mkdir()
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="openai", model=MODEL)
        config.providers.openai.model = MODEL
        config.providers.openai.base_url = provider.base_url
        config.providers.openai.api_key_env = "CSMA_FIXTURE_KEY"
        config.providers.openai.http_connection_reuse_enabled = False
        config.providers.openai.timeout_seconds = 60
        config.providers.openai.max_tokens = 8192
        monkeypatch.setenv("CSMA_FIXTURE_KEY", "loopback-fixture-key")
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        monkeypatch.setenv("no_proxy", "127.0.0.1")
        monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "0")
        monkeypatch.setenv("OPENMINION_LLM_DEBUG", "0")
        config.runtime.process_mode = "single-process"
        config.runtime.daemon_auto_start = False
        config.runtime.memory_enabled = True
        config.runtime.log_level = "ERROR"
        config.identity.db_path = str(data_root / "identity" / "identity.db")
        config.storage.path = str(data_root / "state" / "openminion.db")
        config_path = tmp_path / "config.json"
        save_config(config, str(config_path))
        probe = FocusProbe(
            python_bin=python_bin,
            openminion_root=openminion_root,
            framework_root=framework_root,
            data_root=data_root,
            config_path=config_path,
            agent_id="openminion",
            workdir=workspace,
            session_id="csma-offline-teach",
            include_project_context=False,
            allow_unsandboxed_exec=False,
            entrypoint_module=ENTRYPOINT,
        )
        descriptor = {
            "endpoint": provider.base_url + "/chat/completions",
            "model": MODEL,
            "request_limit": 12,
            "body_limit": 262144,
            "output_limit": 32768,
            "timeout_limit": 60,
            "lifetime_seconds": 180,
            "deadline_monotonic": time.monotonic() + 240,
            "target_hashes": {
                "old": sha256(nonce.encode()).hexdigest(),
                "forbidden": sha256(forbidden.encode()).hexdigest(),
            },
            "config_sha256": sha256(config_path.read_bytes()).hexdigest(),
            "session_id": probe.session_id,
            "evidence_path": str(evaluator / "teach.jsonl"),
        }
        teach_pid, answer, teach_evidence = _focus_child(
            probe,
            descriptor,
            f"The continuity code is {nonce}. Store it using memory.write.",
            monkeypatch,
        )
        assert answer.strip() == "CSMA_STORED"
        assert "UserMessageCandidateReport" in provider.kinds
        _observed_requests(teach_evidence, provider.bodies, targets)
        provider.phase, provider.tool_sent = "recall", False
        provider.bodies.clear()
        recall_probe = probe.for_session("csma-offline-recall")
        descriptor.update(
            session_id=recall_probe.session_id,
            evidence_path=str(evaluator / "recall.jsonl"),
        )
        recall_pid, answer, recall_evidence = _focus_child(
            recall_probe,
            descriptor,
            "What is the continuity code? Do not read any files.",
            monkeypatch,
        )
        assert recall_pid != teach_pid
        assert answer.strip() == nonce
        wire_text = json.dumps(provider.bodies)
        assert nonce in wire_text
        assert forbidden not in wire_text
        assert provider.forbidden_attempts == 1
        requests = _observed_requests(recall_evidence, provider.bodies, targets)
        assert any("old" in row["target_matches"] for row in requests)
        native = [
            row for row in requests if row["canonical"].get("context_llm_call_id")
        ]
        assert len(native) >= 2
        assert len({row["canonical"]["llm_call_id"] for row in native}) == len(native)
        assert all("forbidden" not in row["target_matches"] for row in requests)

        teach = read_state(config_path, data_root, probe.session_id, targets)
        (receipt,) = teach["capture"]
        assert receipt["disposition"] == "succeeded"
        (candidate,) = [c for c in teach["candidates"] if "old" in c["matches"]]
        assert candidate["status"] == "proposed"
        assert candidate["id"] in receipt["output_ids"]
        assert candidate["capture_id"] == receipt["capture_id"]
        (record,) = [
            r for r in teach["records"] if r["active"] and "old" in r["matches"]
        ]
        assert record["continuity_key"]
        recall = read_state(config_path, data_root, recall_probe.session_id, targets)
        manifest_ids = {row["llm_call_id"] for row in recall["context_manifests"]}
        assert all(
            row["canonical"]["context_llm_call_id"] in manifest_ids for row in native
        )
        recall_outcomes, _ = _phase_outcomes(
            "explicit",
            "recall",
            "memory",
            targets,
            record["id"],
            answer,
            requests,
            recall,
        )

        config.runtime.memory_enabled = False
        save_config(config, str(config_path))
        control_probe = probe.for_session("csma-offline-control")
        provider.tool_sent = False
        provider.bodies.clear()
        descriptor.update(
            config_sha256=sha256(config_path.read_bytes()).hexdigest(),
            session_id=control_probe.session_id,
            evidence_path=str(evaluator / "control.jsonl"),
        )
        control_pid, answer, control_evidence = _focus_child(
            control_probe,
            descriptor,
            "What is the continuity code? Do not read any files.",
            monkeypatch,
        )
        assert control_pid not in {teach_pid, recall_pid}
        assert provider.forbidden_attempts == 2
        assert answer.strip() == "UNKNOWN"
        assert nonce not in json.dumps(provider.bodies)
        assert forbidden not in json.dumps(provider.bodies)
        requests = _observed_requests(control_evidence, provider.bodies, targets)
        assert all(not row["target_matches"] for row in requests)
        assert all(recall_outcomes.values()), {
            name for name, passed in recall_outcomes.items() if not passed
        }
    finally:
        provider.close()
