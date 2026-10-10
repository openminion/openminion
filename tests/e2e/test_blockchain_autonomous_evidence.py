from __future__ import annotations

from copy import deepcopy
import json
import os
import re
import subprocess

import pytest

from tests.e2e.runners import (
    run_blockchain_autonomous_focus_minimax as focus_runner,
)
from tests.e2e.runners import run_blockchain_autonomous_local as autonomous_runner
from tests.e2e.runners import run_blockchain_debug_local_anvil as debug_runner
from tests.e2e.runners import run_blockchain_local_anvil as configured_runner
from tests.e2e.runners import run_blockchain_public_read as public_read_runner
from tests.e2e.runners import run_blockchain_testnet_write as testnet_runner
from tests.e2e.runners.run_blockchain_autonomous_local import PROMPT, _run

pytestmark = pytest.mark.e2e

_HEX_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SUBSTANTIVE_CALL_IDS = (
    "search",
    "fetch",
    "resolve",
    "inspect-read",
    "prepare",
    "send",
    "operation-status",
)


def _fail(message: str) -> None:
    raise ValueError(message)


def _tool_events(
    payload: dict,
) -> tuple[dict[str, tuple[int, dict]], dict[str, tuple[int, dict]]]:
    requested: dict[str, tuple[int, dict]] = {}
    completed: dict[str, tuple[int, dict]] = {}
    for index, event in enumerate(payload.get("events", ())):
        if not isinstance(event, dict):
            continue
        event_payload = event.get("payload")
        if not isinstance(event_payload, dict):
            continue
        call_id = str(event_payload.get("call_id", ""))
        if event.get("event_type") == "tool.call.requested":
            requested[call_id] = (index, event)
        elif event.get("event_type") == "tool.call.completed":
            completed[call_id] = (index, event)
    return requested, completed


def validate_blockchain_autonomous_evidence(payload: object) -> None:
    if not isinstance(payload, dict):
        _fail("blockchain evidence must be a structured object")
    if payload.get("schema_version") != "blockchain-autonomous-local-evidence-v1":
        _fail("blockchain evidence schema is missing")
    if not _HEX_COMMIT.fullmatch(str(payload.get("source_commit", ""))):
        _fail("source commit is missing")
    if payload.get("scenario_id") != "autonomous-local-anvil":
        _fail("blockchain evidence scenario is missing")
    if payload.get("terminal_result") != "completed":
        _fail("blockchain evidence terminal result is missing")
    if payload.get("prompt") != PROMPT or payload.get("hidden_context") != {}:
        _fail("the no-hints task boundary changed")
    route = payload.get("route")
    if route != {
        "act_profile": "general",
        "execution_target": "local",
        "handler": "ActLoopMode",
    }:
        _fail("the task did not use the current general act loop")

    executed = payload.get("executed_tools")
    if not isinstance(executed, list):
        _fail("executed tool evidence is missing")
    executed_names = {
        item.get("tool_name") for item in executed if isinstance(item, dict)
    }
    required_tools = {
        "web.search",
        "web.fetch",
        "blockchain.resolve_contract",
        "blockchain.inspect",
        "blockchain.prepare_transaction",
        "blockchain.send_transaction",
    }
    if not required_tools.issubset(executed_names):
        _fail("research and blockchain tool coverage is incomplete")
    resolver_call = next(
        item
        for item in executed
        if item.get("tool_name") == "blockchain.resolve_contract"
    )
    resources = resolver_call.get("arguments")
    if not isinstance(resources, dict):
        _fail("researched resolver inputs are missing")
    forbidden_hints = {
        str(resources.get("rpc_url", "")),
        str(resources.get("expected_chain_id", "")),
        str(resources.get("expected_genesis_hash", "")),
        str(resources.get("contract_address", "")),
        str(resources.get("explorer_contract_url", "")),
        "quote(uint256)",
        "swap((address,uint256,uint256))",
        "14",
        *required_tools,
    }
    if any(token and token in PROMPT for token in forbidden_hints):
        _fail("the task prompt contains a concrete discovery or answer hint")

    model_calls = payload.get("model_calls")
    if not isinstance(model_calls, list) or len(model_calls) < 2:
        _fail("model decision evidence is incomplete")
    if model_calls[0].get("tool_choice") != "none" or model_calls[0].get("tools") != []:
        _fail("the shortlist classifier must remain tool-free")
    if any(call.get("tool_choice") != "auto" for call in model_calls[1:]):
        _fail("tool-capable decisions must preserve automatic tool choice")
    initial_tools = set(model_calls[1].get("tools", ()))
    if not {"web.search", "web.fetch", "tool.request"}.issubset(initial_tools):
        _fail("the current research shortlist is missing")
    if "blockchain.resolve_contract" in initial_tools:
        _fail("the resolver bypassed current tool.request exposure")
    for tool_name, request_call_id in (
        ("blockchain.resolve_contract", "request-resolver"),
        ("blockchain.inspect", "request-inspect-read"),
        ("blockchain.prepare_transaction", "request-prepare"),
        ("blockchain.send_transaction", "request-send"),
    ):
        if not any(
            tool_name in call.get("tools", ())
            and request_call_id in call.get("tool_result_call_ids", ())
            for call in model_calls
        ):
            _fail(f"{tool_name} was not exposed through tool.request")
    for call_id in _SUBSTANTIVE_CALL_IDS:
        if not any(
            call_id in call.get("tool_result_call_ids", ()) for call in model_calls
        ):
            _fail(f"the model did not consume the {call_id} result")

    requested, completed = _tool_events(payload)
    for call_id in _SUBSTANTIVE_CALL_IDS:
        if call_id not in requested or call_id not in completed:
            _fail(f"canonical events are missing for {call_id}")
        request_index, request_event = requested[call_id]
        completed_index, completed_event = completed[call_id]
        if completed_index <= request_index:
            _fail(f"terminal event precedes request for {call_id}")
        if completed_event.get("parent_event_id") != request_event.get("event_id"):
            _fail(f"canonical event correlation is broken for {call_id}")
    for prior, later in (
        ("fetch", "resolve"),
        ("resolve", "inspect-read"),
        ("inspect-read", "prepare"),
        ("prepare", "send"),
        ("send", "operation-status"),
    ):
        if completed[prior][0] >= requested[later][0]:
            _fail(f"causal edge {prior} -> {later} is missing")

    resolution_digest = str(payload.get("resolution_digest", ""))
    preparation_digest = str(payload.get("preparation_digest", ""))
    if not _SHA256_DIGEST.fullmatch(resolution_digest):
        _fail("resolution digest is malformed")
    if not _SHA256_DIGEST.fullmatch(preparation_digest):
        _fail("preparation digest is malformed")
    session_records = payload.get("session_records")
    if not isinstance(session_records, dict) or session_records.get("before") != 0:
        _fail("the scenario did not start from empty session records")
    record_files = set(session_records.get("files", ()))
    if f"{resolution_digest.removeprefix('sha256:')}.json" not in record_files:
        _fail("resolution digest does not join to durable session state")
    if f"{preparation_digest.removeprefix('sha256:')}.json" not in record_files:
        _fail("preparation digest does not join to durable session state")
    if (
        payload.get("resolution", {}).get("data", {}).get("resolution_digest")
        != resolution_digest
    ):
        _fail("resolution result digest does not join")
    read = payload.get("read", {})
    if read.get("data", {}).get("resolution_digest") != resolution_digest:
        _fail("resolved read does not join to resolution")
    if read.get("data", {}).get("result") != ["14"]:
        _fail("resolved read result is wrong")
    preparation = payload.get("preparation", {})
    if preparation.get("resolution_digest") != resolution_digest:
        _fail("preparation does not join to resolution")
    if preparation.get("preparation_digest") != preparation_digest:
        _fail("preparation digest does not join to preparation")
    send = payload.get("send", {})
    if send.get("data", {}).get("preparation_digest") != preparation_digest:
        _fail("submission does not join to preparation")
    for key in ("operation_status", "restart_status"):
        status = payload.get(key, {})
        if status.get("state") != "succeeded":
            _fail(f"{key} is not succeeded")
        data = status.get("data", {})
        if data.get("resolution_digest") != resolution_digest:
            _fail(f"{key} does not join to resolution")
        if data.get("preparation_digest") != preparation_digest:
            _fail(f"{key} does not join to preparation")
        postconditions = data.get("postcondition_results", ())
        if len(postconditions) != 1 or postconditions[0].get("matched") is not True:
            _fail(f"{key} lacks a matched postcondition")
    if not any(
        resolution_digest in call.get("observed_digests", ()) for call in model_calls
    ):
        _fail("the model did not observe the resolution digest")
    if not any(
        preparation_digest in call.get("observed_digests", ()) for call in model_calls
    ):
        _fail("the model did not observe the preparation digest")

    transport = payload.get("transport_calls")
    if not isinstance(transport, list):
        _fail("transport evidence is missing")
    broadcasts = [
        call for call in transport if call.get("rpc_method") == "eth_sendRawTransaction"
    ]
    if len(broadcasts) != 1:
        _fail("the scenario must broadcast exactly once")
    if (
        payload.get("broadcasts_before_restart") != 1
        or payload.get("broadcasts_after_restart") != 1
    ):
        _fail("restart reconstruction resubmitted the transaction")
    owners = payload.get("execution_owners")
    if owners != {
        "command_executor": "RunnerCommandExecutor",
        "tool_adapter": "ToolAdapter",
        "policy_adapter": "PolicyCtlBrainAdapter",
        "send_scope": "POWER_USER",
    }:
        _fail("production command, policy, registry, and tool owners were bypassed")
    approved = payload.get("approved_policy")
    if not isinstance(approved, dict) or approved.get("action") != "allow_once":
        _fail("approved session lacks explicit one-time authorization")
    if not approved.get("grant_id"):
        _fail("approved session lacks a consumed policy grant")
    approved_preview = approved.get("preview", {})
    if approved_preview.get("preparation_digest") != preparation_digest:
        _fail("approved preview does not bind the exact preparation")
    denied = payload.get("denied_policy")
    if not isinstance(denied, dict) or denied.get("session_id") == payload.get(
        "session_id"
    ):
        _fail("denied proof must use a separate policy session")
    denied_approval = denied.get("approval", {})
    if (
        denied_approval.get("action") != "deny"
        or denied_approval.get("grant_id") is not None
    ):
        _fail("denied session lacks an exact denial")
    if not denied_approval.get("preview", {}).get("preparation_digest"):
        _fail("denied session lacks the exact send preview")
    if denied.get("broadcasts_before") != denied.get("broadcasts_after"):
        _fail("denied session attempted a broadcast")
    if denied.get("result_status") != "needs_user":
        _fail("denied policy result was not surfaced")
    audit_events = payload.get("audit_events")
    if not isinstance(audit_events, list) or len(audit_events) != 1:
        _fail("single authorized mutation audit evidence is missing")
    if audit_events[0].get("preparation_digest") != preparation_digest:
        _fail("mutation audit does not join to preparation")
    chain = payload.get("chain", {})
    if chain.get("chain_id") != 31337 or not re.fullmatch(
        r"[0-9a-f]{64}", str(chain.get("contract_code_sha256", ""))
    ):
        _fail("local Anvil fixture truth is missing")
    if payload.get("result", {}).get("status") != "done":
        _fail("the general loop did not complete")


@pytest.fixture(scope="module")
def evidence() -> dict:
    return _run()


def test_local_general_loop_evidence_is_causal_and_restart_safe(evidence) -> None:
    validate_blockchain_autonomous_evidence(evidence)


def test_validator_rejects_broken_digest_join(evidence) -> None:
    mutated = deepcopy(evidence)
    mutated["send"]["data"]["preparation_digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="submission does not join"):
        validate_blockchain_autonomous_evidence(mutated)


def test_validator_rejects_denied_broadcast(evidence) -> None:
    mutated = deepcopy(evidence)
    mutated["denied_policy"]["broadcasts_after"] += 1
    with pytest.raises(ValueError, match="denied session attempted a broadcast"):
        validate_blockchain_autonomous_evidence(mutated)


def test_focus_validator_requires_persisted_tool_evidence() -> None:
    payload = {
        "schema_version": "blockchain-autonomous-focus-evidence-v2",
        "scenario_id": "autonomous-public-discovery",
        "protocol": "general_loop_focus_cli",
        "source_commit": "a" * 40,
        "persisted_event_count": 4,
        "requested_tools": [
            "web.search",
            "blockchain.resolve_contract",
            "blockchain.inspect",
        ],
        "successful_resolution_digests": ["sha256:" + "1" * 64],
        "successful_resolved_reads": [
            {
                "resolution_digest": "sha256:" + "1" * 64,
                "block_number": "1",
                "raw_return_digest": "sha256:" + "2" * 64,
            }
        ],
        "response_sha256": "3" * 64,
        "response_bytes": 40,
        "permission_mode": "readonly",
        "allow_unsandboxed_exec": False,
        "approval_prompt_count": 0,
        "broadcast_count": 0,
        "elapsed_seconds": 10.0,
        "max_duration_seconds": 30,
        "terminal_result": "completed",
    }
    focus_runner.validate_evidence(payload)
    payload["requested_tools"] = ["web.search"]
    with pytest.raises(ValueError, match="resolution, and read"):
        focus_runner.validate_evidence(payload)


def test_focus_validator_rejects_unjoined_successful_read() -> None:
    payload = {
        "schema_version": "blockchain-autonomous-focus-evidence-v2",
        "scenario_id": "autonomous-public-discovery",
        "protocol": "general_loop_focus_cli",
        "source_commit": "a" * 40,
        "persisted_event_count": 4,
        "requested_tools": [
            "web.search",
            "blockchain.resolve_contract",
            "blockchain.inspect",
        ],
        "successful_resolution_digests": ["sha256:" + "1" * 64],
        "successful_resolved_reads": [
            {
                "resolution_digest": "sha256:" + "2" * 64,
                "block_number": "1",
                "raw_return_digest": "sha256:" + "3" * 64,
            }
        ],
        "response_sha256": "3" * 64,
        "response_bytes": 40,
        "permission_mode": "readonly",
        "allow_unsandboxed_exec": False,
        "approval_prompt_count": 0,
        "broadcast_count": 0,
        "elapsed_seconds": 10.0,
        "max_duration_seconds": 30,
        "terminal_result": "completed",
    }
    with pytest.raises(ValueError, match="does not join"):
        focus_runner.validate_evidence(payload)


def test_public_read_validator_joins_resolution_and_read() -> None:
    digest = "sha256:" + "1" * 64
    payload = {
        "schema_version": "blockchain-autonomous-public-read-evidence-v2",
        "scenario_id": "approved-public-contract-read",
        "protocol": "direct_runtime_protocol",
        "source_commit": "a" * 40,
        "write_attempts": 0,
        "resolution": {"ok": True, "data": {"resolution_digest": digest}},
        "read": {"ok": True, "data": {"resolution_digest": digest}},
        "broadcast_count": 0,
        "elapsed_seconds": 2.0,
        "max_duration_seconds": 30,
        "terminal_result": "completed",
    }
    public_read_runner.validate_evidence(payload)
    payload["read"]["data"]["resolution_digest"] = "sha256:" + "2" * 64
    with pytest.raises(ValueError, match="does not join"):
        public_read_runner.validate_evidence(payload)


def test_live_validators_reject_stale_approval_inputs() -> None:
    focus = {
        "schema_version": "blockchain-autonomous-focus-evidence-v2",
        "scenario_id": "autonomous-public-discovery",
        "protocol": "general_loop_focus_cli",
        "source_commit": "a" * 40,
        "persisted_event_count": 1,
        "requested_tools": [
            "web.search",
            "blockchain.resolve_contract",
            "blockchain.inspect",
        ],
        "successful_resolution_digests": ["sha256:" + "1" * 64],
        "successful_resolved_reads": [
            {
                "resolution_digest": "sha256:" + "1" * 64,
                "block_number": "1",
                "raw_return_digest": "sha256:" + "2" * 64,
            }
        ],
        "response_sha256": "3" * 64,
        "response_bytes": 40,
        "permission_mode": "readonly",
        "allow_unsandboxed_exec": False,
        "approval_prompt_count": 0,
        "broadcast_count": 0,
        "elapsed_seconds": 1.0,
        "max_duration_seconds": 30,
        "terminal_result": "completed",
    }
    with pytest.raises(ValueError, match="source_commit does not match"):
        focus_runner.validate_evidence(focus, {"source_commit": "b" * 40})


def test_focus_retained_evidence_scan_rejects_secret(tmp_path) -> None:
    sentinel = b"fake-sentinel-provider-key"
    (tmp_path / "evidence.json").write_bytes(b'{"value":"' + sentinel + b'"}')
    with pytest.raises(RuntimeError, match="credential material"):
        focus_runner._assert_retained_evidence_safe(tmp_path, {sentinel})


def test_focus_private_runtime_root_is_removed_after_success(
    tmp_path, monkeypatch
) -> None:
    home_root = tmp_path / "home"
    generated_root = home_root / ".openminion" / "runtime"
    generated_root.mkdir(parents=True)
    monkeypatch.setattr(
        focus_runner,
        "isolate_runtime_roots",
        lambda **_kwargs: generated_root,
    )

    with focus_runner._private_runtime_root() as data_root:
        assert data_root == home_root / ".openminion"
        assert home_root.exists()

    assert not home_root.exists()


def test_focus_private_runtime_root_is_removed_after_failure(
    tmp_path, monkeypatch
) -> None:
    home_root = tmp_path / "home"
    generated_root = home_root / ".openminion" / "runtime"
    generated_root.mkdir(parents=True)
    monkeypatch.setattr(
        focus_runner,
        "isolate_runtime_roots",
        lambda **_kwargs: generated_root,
    )

    with pytest.raises(RuntimeError, match="scenario failed"):
        with focus_runner._private_runtime_root():
            raise RuntimeError("scenario failed")

    assert not home_root.exists()


@pytest.mark.parametrize(
    ("runner", "home_path"),
    (
        (configured_runner, lambda runtime: runtime.parents[1]),
        (debug_runner, lambda runtime: runtime.parent),
        (autonomous_runner, lambda runtime: runtime.parents[1]),
        (public_read_runner, lambda runtime: runtime.parents[1]),
    ),
)
def test_blockchain_runner_runtime_root_is_removed_after_inner_failure(
    monkeypatch, runner, home_path
) -> None:
    sentinels = {
        "OPENMINION_HOME": "/caller/home",
        "OPENMINION_DATA_ROOT": "/caller/data",
        "OPENMINION_GENERATED_ROOT": "/caller/generated",
    }
    for name, value in sentinels.items():
        monkeypatch.setenv(name, value)
    home_root = None

    with pytest.raises(subprocess.TimeoutExpired):
        with runner._private_runtime_root() as runtime_root:
            home_root = home_path(runtime_root)
            assert home_root.exists()
            raise subprocess.TimeoutExpired("anvil", 5)

    assert home_root is not None
    assert not home_root.exists()
    assert {name: os.environ.get(name) for name in sentinels} == sentinels


def test_focus_failure_retains_redacted_evidence_and_cleans_runtime(
    tmp_path, monkeypatch
) -> None:
    sentinel = "fake-sentinel-provider-key"
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    home_root = tmp_path / "home"
    generated_root = home_root / ".openminion" / "runtime"
    generated_root.mkdir(parents=True)
    scans: list[tuple[object, object]] = []
    scan = focus_runner._assert_retained_evidence_safe

    def record_scan(root, secrets) -> None:
        scans.append((root, secrets))
        scan(root, secrets)

    def fail_probe(**_kwargs):
        raise RuntimeError(sentinel)

    monkeypatch.setenv(focus_runner.OPT_IN, "1")
    monkeypatch.setattr(
        focus_runner,
        "_required_config",
        lambda: (
            tmp_path / "source.json",
            {"runtime": {"env": {"MINIMAX_API_KEY": sentinel}}},
        ),
    )
    monkeypatch.setattr(focus_runner, "_required", lambda _name: "minimax")
    monkeypatch.setattr(focus_runner, "_source_commit", lambda: "a" * 40)
    monkeypatch.setattr(focus_runner, "_evidence_root", lambda: evidence_root)
    monkeypatch.setattr(
        focus_runner,
        "isolate_runtime_roots",
        lambda **_kwargs: generated_root,
    )
    monkeypatch.setattr(
        focus_runner,
        "_stage_c_inputs",
        lambda _path, _agent: {"max_duration_seconds": 30},
    )
    monkeypatch.setattr(focus_runner, "FocusProbe", fail_probe)
    monkeypatch.setattr(focus_runner, "_assert_retained_evidence_safe", record_scan)

    with pytest.raises(RuntimeError, match=sentinel):
        focus_runner.main()

    path = evidence_root / "evidence.json"
    retained = json.loads(path.read_text())
    assert retained["terminal_result"] == "failed"
    assert retained["failure"] == {
        "error_type": "RuntimeError",
        "message": "Focus scenario failed; details were not retained.",
    }
    assert "requested_tools" not in retained
    assert sentinel not in path.read_text()
    assert scans == [(evidence_root, {sentinel.encode()})]
    assert not home_root.exists()


def test_focus_failure_evidence_keeps_observed_runtime_facts() -> None:
    evidence = focus_runner._failure_evidence(
        source_commit="a" * 40,
        agent_id="minimax",
        approved={"max_duration_seconds": 30},
        observed={
            "persisted_event_count": 4,
            "persisted_event_types": ["tool.call.requested"],
            "requested_tools": ["shell.exec"],
            "completed_tools": [],
            "permission_mode": "readonly",
            "allow_unsandboxed_exec": False,
            "approval_prompt_count": 0,
            "broadcast_count": 0,
            "elapsed_seconds": 2.0,
        },
        error=ValueError("sensitive detail"),
    )

    assert evidence["terminal_result"] == "failed"
    assert evidence["requested_tools"] == ["shell.exec"]
    assert evidence["persisted_event_count"] == 4
    assert evidence["approval_prompt_count"] == 0
    assert evidence["broadcast_count"] == 0
    assert "sensitive detail" not in json.dumps(evidence)


@pytest.mark.parametrize("attribute", ("SIGALRM", "setitimer"))
def test_public_read_deadline_fails_closed_when_unavailable(
    monkeypatch, attribute: str
) -> None:
    monkeypatch.delattr(public_read_runner.signal, attribute)

    with pytest.raises(RuntimeError, match="deadline is unavailable"):
        with public_read_runner._deadline(1):
            pytest.fail("deadline context must not start")


@pytest.mark.parametrize("attribute", ("SIGALRM", "setitimer"))
def test_focus_deadline_fails_closed_when_unavailable(
    monkeypatch, attribute: str
) -> None:
    monkeypatch.delattr(focus_runner.signal, attribute)

    with pytest.raises(RuntimeError, match="deadline is unavailable"):
        with focus_runner._deadline(1):
            pytest.fail("deadline context must not start")


def test_testnet_validator_joins_full_lifecycle() -> None:
    resolution_digest = "sha256:" + "1" * 64
    preparation_digest = "sha256:" + "2" * 64
    status = {
        "ok": True,
        "data": {
            "state": "succeeded",
            "preparation_digest": preparation_digest,
            "postcondition_results": [{"matched": True}],
        },
    }
    payload = {
        "protocol": "production_policy_runtime_protocol",
        "source_commit": "a" * 40,
        "confirmation_depth": 1,
        "resolution": {"ok": True, "data": {"resolution_digest": resolution_digest}},
        "preparation": {
            "resolution_digest": resolution_digest,
            "preparation_digest": preparation_digest,
        },
        "send": {
            "ok": True,
            "data": {
                "preparation_digest": preparation_digest,
                "broadcast_attempts": 1,
            },
        },
        "operation_status": deepcopy(status),
        "restart_status": deepcopy(status),
        "audit_events": [{"preparation_digest": preparation_digest}],
        "policy_approval": {
            "action": "allow_once",
            "grant_id": "grant-1",
            "preview": {"preparation_digest": preparation_digest},
        },
        "execution_owners": {
            "command_executor": "RunnerCommandExecutor",
            "tool_adapter": "ToolAdapter",
            "policy_adapter": "PolicyCtlBrainAdapter",
            "send_scope": "POWER_USER",
        },
    }
    testnet_runner.validate_evidence(payload)
    payload["restart_status"]["data"]["state"] = "pending"
    with pytest.raises(ValueError, match="restart_status is not succeeded"):
        testnet_runner.validate_evidence(payload)


@pytest.mark.parametrize(
    ("runner", "opt_in", "config_env"),
    (
        (focus_runner, focus_runner.OPT_IN, focus_runner.CONFIG_ENV),
        (public_read_runner, public_read_runner.OPT_IN, public_read_runner.CONFIG_ENV),
        (testnet_runner, testnet_runner.OPT_IN, testnet_runner.CONFIG_ENV),
    ),
)
def test_opt_in_runners_fail_clearly_without_required_config(
    monkeypatch, runner, opt_in: str, config_env: str
) -> None:
    monkeypatch.setenv(opt_in, "1")
    monkeypatch.delenv(config_env, raising=False)
    with pytest.raises(
        RuntimeError, match=rf"^{config_env} is required when {opt_in}=1$"
    ):
        runner._required_config()


def test_focus_runner_builds_private_readonly_blockchain_config(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            {
                "runtime": {"env": {"MINIMAX_API_KEY": "sentinel-provider-key"}},
                "agents": {"minimax": {"provider": "openai"}},
            }
        )
    )
    monkeypatch.setenv(focus_runner.OPT_IN, "1")
    monkeypatch.setenv(focus_runner.CONFIG_ENV, str(source))

    resolved, payload = focus_runner._required_config()
    private = focus_runner._write_private_config(payload, tmp_path / "runtime")
    private_payload = json.loads(private.read_text())

    assert resolved == source
    assert private.stat().st_mode & 0o777 == 0o600
    assert private_payload["runtime"]["tools"]["blockchain"] == {"enabled": True}
    assert private_payload["runtime"]["env"]["MINIMAX_API_KEY"] == (
        "sentinel-provider-key"
    )
