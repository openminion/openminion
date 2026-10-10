import json
import os
from pathlib import Path

import pytest
from tests.helpers.live_e2e_profiles import resolve_live_framework_root

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
_EVIDENCE_ROOT = resolve_live_framework_root(ROOT) / "workspace-tmp" / "bttl-e2e"


def _load(profile: str) -> dict:
    if os.getenv("OPENMINION_BTTL_EVIDENCE_E2E") != "1":
        pytest.skip("BTTL evidence validation requires explicit opt-in")
    path = _EVIDENCE_ROOT / profile / "evidence.json"
    assert path.is_file(), f"missing BTTL evidence: {path}"
    evidence = json.loads(path.read_text(encoding="utf-8"))
    assert evidence["schema_version"] == "bttl-e2e-v2"
    assert (
        evidence["scenario_id"]
        == f"configured-{profile.replace('focus', 'focus-minimax').replace('local', 'local-anvil')}"
    )
    assert evidence["terminal_result"] == "completed"
    assert evidence["source_commit"]
    return evidence


def test_local_blockchain_evidence_has_six_typed_records() -> None:
    evidence = _load("local")
    assert {
        "provider_request",
        "policy_decision",
        "execution_authorization",
        "tool_result",
        "transaction_audit",
        "chain_state",
    } <= evidence.keys()
    assert (
        evidence["policy_decision"]["invocation_hash"]
        == evidence["execution_authorization"]["invocation_hash"]
    )
    assert (
        evidence["transaction_audit"]["approval_id"]
        == evidence["execution_authorization"]["approval_id"]
    )
    assert evidence["chain_state"]["receipt_status"] == 1
    denied = evidence["denied_send"]
    assert denied["execution_authorization"] is None
    assert denied["transaction_audit"] is None
    assert denied["chain_state_unchanged"] is True
    stale = evidence["stale_send"]
    assert stale["policy_decision"]["decision"] == "REQUIRE_CONFIRM"
    assert stale["tool_result"]["outputs"]["error"]["code"] == ("STALE_PREPARATION")
    assert stale["tool_result"]["outputs"]["data"]["broadcast_attempts"] == 0
    assert stale["chain_state_unchanged"] is True


def test_focus_blockchain_evidence_binds_policy_audit_and_chain_state() -> None:
    evidence = _load("focus")
    assert evidence["provider_request"]["trace_files"]
    authorization = evidence["execution_authorization"]
    assert (
        evidence["policy_decision"]["invocation_hash"]
        == authorization["invocation_hash"]
    )
    assert evidence["transaction_audit"]["approval_id"] == authorization["approval_id"]
    assert (
        evidence["transaction_audit"]["consumed_grant_id"] == authorization["grant_id"]
    )
    assert evidence["chain_state"]["recipient_balance_after"] == str(
        int(evidence["chain_state"]["recipient_balance_before"]) + 1
    )
    denied = evidence["denied_send"]
    assert denied["policy_decision"]["decision"] == "require_confirm"
    assert denied["pending_confirmation"]["state"] == "denied"
    assert denied["execution_authorization"] is None
    assert denied["transaction_audit"] is None
    assert denied["chain_state_unchanged"] is True
    encoded = json.dumps(evidence, sort_keys=True)
    assert (
        "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
        not in encoded
    )
    assert "focus-anvil-signer" not in encoded
