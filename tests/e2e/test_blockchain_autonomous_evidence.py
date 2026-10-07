from __future__ import annotations

from copy import deepcopy
import re

import pytest

from tests.e2e.runners import (
    run_blockchain_autonomous_focus_minimax as focus_runner,
)
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


def _tool_events(payload: dict) -> tuple[dict[str, tuple[int, dict]], dict[str, tuple[int, dict]]]:
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
        item for item in executed if item.get("tool_name") == "blockchain.resolve_contract"
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
        if not any(call_id in call.get("tool_result_call_ids", ()) for call in model_calls):
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
    if payload.get("resolution", {}).get("data", {}).get("resolution_digest") != resolution_digest:
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
    if payload.get("broadcasts_before_restart") != 1 or payload.get(
        "broadcasts_after_restart"
    ) != 1:
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
    if denied_approval.get("action") != "deny" or denied_approval.get("grant_id") is not None:
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
        "protocol": "general_loop_focus_cli",
        "source_commit": "a" * 40,
        "persisted_event_count": 4,
        "requested_tools": [
            "web.search",
            "blockchain.resolve_contract",
            "blockchain.inspect",
        ],
        "transcript": "Verified contract at a confirmed block.",
    }
    focus_runner.validate_evidence(payload)
    payload["requested_tools"] = ["web.search"]
    with pytest.raises(ValueError, match="resolution, and read"):
        focus_runner.validate_evidence(payload)


def test_public_read_validator_joins_resolution_and_read() -> None:
    digest = "sha256:" + "1" * 64
    payload = {
        "protocol": "direct_runtime_protocol",
        "source_commit": "a" * 40,
        "write_attempts": 0,
        "resolution": {"ok": True, "data": {"resolution_digest": digest}},
        "read": {"ok": True, "data": {"resolution_digest": digest}},
    }
    public_read_runner.validate_evidence(payload)
    payload["read"]["data"]["resolution_digest"] = "sha256:" + "2" * 64
    with pytest.raises(ValueError, match="does not join"):
        public_read_runner.validate_evidence(payload)


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
        "protocol": "direct_runtime_protocol",
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
    with pytest.raises(RuntimeError, match=rf"^{config_env} is required when {opt_in}=1$"):
        runner._required_config()
