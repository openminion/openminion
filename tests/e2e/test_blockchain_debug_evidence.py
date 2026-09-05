from __future__ import annotations

from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import subprocess

import pytest

from tests.e2e.runners.run_blockchain_debug_focus_minimax import (
    EXPECTED_FINAL_INVOCATIONS as EXPECTED_FOCUS_FINAL_INVOCATIONS,
    EXPECTED_INVOCATIONS as EXPECTED_FOCUS_INVOCATIONS,
    EXPECTED_PREFIX_INVOCATIONS as EXPECTED_FOCUS_PREFIX_INVOCATIONS,
    EXPECTED_TURN_GROUPS as EXPECTED_FOCUS_TURN_GROUPS,
    _required_invocations,
    _turn_scope_groups,
)

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
FRAMEWORK_ROOT = min(
    (
        parent
        for parent in ROOT.parents
        if (parent / "test-configs" / "per-agent-minimax-official.json").exists()
    ),
    key=lambda path: len(path.parts),
)
EVIDENCE_ROOT = FRAMEWORK_ROOT / "workspace-tmp" / "bdtc-e2e"


def _load(profile: str) -> dict:
    path = EVIDENCE_ROOT / profile / "evidence.json"
    assert path.is_file(), f"missing BDTC evidence: {path}"
    return json.loads(path.read_text())


def _clean_commit() -> str:
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        text=True,
    )
    assert not status, "BDTC evidence validation requires a clean checkout"
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def _event_fields(event: dict) -> tuple[str, str, str]:
    payload = event.get("payload", event)
    return (
        event["event_type"],
        payload["tool_call_id"],
        payload["tool_name"],
    )


def _assert_focus_invocation_sequence(evidence: dict) -> None:
    sequence = evidence["invocation_sequence"]
    required = _required_invocations(sequence)
    observed = [(item["tool"], item["operation"]) for item in required]
    assert observed[:6] == EXPECTED_FOCUS_PREFIX_INVOCATIONS
    assert observed[6:] == EXPECTED_FOCUS_FINAL_INVOCATIONS
    call_ids = [item["tool_call_id"] for item in sequence]
    assert all(call_ids)
    assert len(set(call_ids)) == len(call_ids)
    turn_scope_ids = [item["turn_scope_id"] for item in sequence]
    assert all(turn_scope_ids)
    assert _turn_scope_groups(required) == EXPECTED_FOCUS_TURN_GROUPS


def _assert_evidence(evidence: dict, *, profile: str, commit: str) -> None:
    recipient = "0x" + ("44" if profile == "local" else "55") * 20
    expected_args = [[recipient, "7", "14"]]
    assert evidence["schema_version"] == "bdtc-e2e-v1"
    assert evidence["source_commit"] == commit
    assert evidence["profile"] == profile
    assert evidence["scenario_id"]

    encoded = json.dumps(evidence, sort_keys=True).lower()
    assert "private provider text" not in encoded
    assert "private_key" not in encoded
    assert "signer_secret" not in encoded
    assert "ac0974bec39a17e36" not in encoded

    debug = evidence["debug_revert"]
    assert debug == {
        "tool_call_id": debug["tool_call_id"],
        "tool": "blockchain.debug",
        "action": "simulate_call",
        "error_code": "SIMULATION_REVERTED",
        "revert_kind": debug["revert_kind"],
        "provider_text_exposed": False,
    }
    assert debug["revert_kind"] in {
        "standard_error",
        "panic",
        "custom_error",
        "unknown",
        "data_unavailable",
    }

    prepared = evidence["prepared"]
    preview = evidence["approval_preview"]
    assert prepared["chain_id"] == "31337"
    assert prepared["function_signature"] == "swap((address,uint256,uint256))"
    assert [[str(item) for item in prepared["function_args"][0]]] == expected_args
    assert prepared["pre_send_state"] == "0"
    assert preview["schema_version"] == "blockchain-send-preview-v1"
    assert preview["chain_id"] == prepared["chain_id"]
    assert preview["to_address"] == prepared["contract_address"]
    assert preview["preparation_digest"] == prepared["preparation_digest"]
    assert preview["call"] == {
        "function_signature": prepared["function_signature"],
        "function_args": expected_args,
    }
    assert preview["opaque_calldata"] is False
    assert preview["calldata_hex"] is None

    denied = evidence["denied"]
    authorized = evidence["authorized"]
    verified = evidence["verified"]
    assert denied["approval_id"]
    assert denied["broadcast_attempts"] == 0
    assert denied["post_denial_state"] == prepared["pre_send_state"]
    assert authorized["approval_id"] != denied["approval_id"]
    assert authorized["consumed_grant_id"]
    assert authorized["invocation_hash"]
    assert authorized["broadcast_attempts"] == 1
    assert authorized["transaction_hash"] == verified["transaction_hash"]
    assert verified["receipt_status"] == 1
    assert verified["event_signature"] == "Swap(address,address,uint256,uint256)"
    assert verified["final_state"] == "14"
    assert [item["value"] for item in verified["event_arguments"]] == [
        "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        recipient,
        "7",
        "14",
    ]

    events_by_call: dict[str, list[str]] = defaultdict(list)
    for event in evidence["telemetry"]:
        event_type, call_id, tool_name = _event_fields(event)
        assert tool_name == "blockchain.debug"
        events_by_call[call_id].append(event_type)
    assert debug["tool_call_id"] in events_by_call
    assert verified["event_tool_call_id"] in events_by_call
    assert Counter(events_by_call[debug["tool_call_id"]]) == Counter(
        ["tool.execution.started", "tool.execution.failed"]
    )
    assert Counter(events_by_call[verified["event_tool_call_id"]]) == Counter(
        ["tool.execution.started", "tool.execution.completed"]
    )
    for event_types in events_by_call.values():
        assert len(event_types) == 2
        assert event_types.count("tool.execution.started") == 1
        assert (
            sum(
                event_types.count(terminal)
                for terminal in (
                    "tool.execution.completed",
                    "tool.execution.failed",
                )
            )
            == 1
        )

    audit = evidence["audit"]
    assert audit["prepare_transaction_event_count"] == 0
    assert audit["denied_send_transaction_event_count"] == 0
    assert audit["authorized_send_transaction_event_count"] == 1
    event = audit["authorized_event"]
    assert event["preparation_digest"] == prepared["preparation_digest"]
    assert event["approval_id"] == authorized["approval_id"]
    assert event["consumed_grant_id"] == authorized["consumed_grant_id"]
    assert event["invocation_hash"] == authorized["invocation_hash"]
    assert event["transaction_hash"] == authorized["transaction_hash"]
    assert event["broadcast_attempts"] == 1

    if profile == "local":
        assert evidence["transcript_path"] is None
    else:
        _assert_focus_invocation_sequence(evidence)
        sequence = evidence["invocation_sequence"]
        assert sequence[0]["tool_call_id"] == debug["tool_call_id"]
        event_call = next(
            item
            for item in sequence
            if item["tool"] == "blockchain.debug"
            and item["operation"] == "transaction_events"
        )
        assert event_call["tool_call_id"] == verified["event_tool_call_id"]
        transcript = FRAMEWORK_ROOT / evidence["transcript_path"]
        transcript.relative_to(EVIDENCE_ROOT)
        assert transcript.is_file()


@pytest.mark.e2e
def test_blockchain_debug_local_and_focus_evidence() -> None:
    if os.getenv("OPENMINION_BDTC_EVIDENCE_E2E") != "1":
        pytest.skip("BDTC evidence validation requires explicit opt-in")
    commit = _clean_commit()
    _assert_evidence(_load("local"), profile="local", commit=commit)
    _assert_evidence(_load("focus"), profile="focus-minimax", commit=commit)


def test_focus_invocation_sequence_requires_exact_calls_and_turn_scopes() -> None:
    sequence = [
        {
            "tool": tool,
            "operation": operation,
            "tool_call_id": f"call-{index}",
            "turn_scope_id": f"turn-{index if index < 7 else 7}",
        }
        for index, (tool, operation) in enumerate(EXPECTED_FOCUS_INVOCATIONS, start=1)
    ]
    _assert_focus_invocation_sequence({"invocation_sequence": sequence})

    final_calls_reordered = [*sequence[:6], sequence[8], sequence[6], sequence[7]]
    _assert_focus_invocation_sequence({"invocation_sequence": final_calls_reordered})

    with pytest.raises(AssertionError):
        _assert_focus_invocation_sequence(
            {"invocation_sequence": sequence + [dict(sequence[-1])]}
        )
    with pytest.raises(AssertionError):
        _assert_focus_invocation_sequence(
            {"invocation_sequence": [sequence[1], sequence[0], *sequence[2:]]}
        )
    collapsed = [dict(item) for item in sequence]
    collapsed[1]["turn_scope_id"] = collapsed[0]["turn_scope_id"]
    with pytest.raises(AssertionError):
        _assert_focus_invocation_sequence({"invocation_sequence": collapsed})
