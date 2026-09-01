from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

from cryptography.fernet import Fernet
from web3 import Web3

ROOT = Path(__file__).resolve().parents[3]
FRAMEWORK_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from tests.helpers.runtime_roots import isolate_runtime_roots  # noqa: E402

RUNTIME_GENERATED_ROOT = isolate_runtime_roots(prefix="openminion-bttl-local-")

from openminion.base.config.runtime.tools import (  # noqa: E402
    BlockchainToolRuntimeConfig,
    ToolRuntimeConfig,
)
from openminion.modules.brain.adapters.tool.runtime import ToolAdapter  # noqa: E402
from openminion.modules.policy.models import PolicyConfig, RiskSpec  # noqa: E402
from openminion.modules.policy.runtime.service import PolicyCtl  # noqa: E402
from openminion.modules.secret.service import SecretService  # noqa: E402
from openminion.modules.tool.bootstrap import build_runtime_bootstrap  # noqa: E402
from openminion.modules.tool.runtime.policy import (  # noqa: E402
    DEFAULT_POLICY,
    Policy,
)
from openminion.tools.blockchain.runtime import (  # noqa: E402
    inspect_blockchain,
    prepare_transaction,
)

ARTIFACT_ROOT = FRAMEWORK_ROOT / "workspace-tmp" / "bttl-e2e" / "local"
RPC_URL = "http://127.0.0.1:18547"
CHAIN_ID = 31337
PRIVATE_KEY = "0x" + "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
SENDER = Web3.to_checksum_address("0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266")
RECIPIENT = Web3.to_checksum_address("0x" + "22" * 20)


def _wait_rpc(web3: Web3) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if web3.is_connected():
            return
        time.sleep(0.1)
    raise RuntimeError("Anvil did not become ready")


def _runtime_config() -> SimpleNamespace:
    tools = ToolRuntimeConfig(
        blockchain=BlockchainToolRuntimeConfig(
            enabled=True,
            rpc_url=RPC_URL,
            chain_id=CHAIN_ID,
            signer_secret_key="local-anvil-signer",
            signer_secret_namespace="blockchain",
            writes_enabled=True,
            receipt_timeout_seconds=10,
        )
    )
    return SimpleNamespace(
        runtime=SimpleNamespace(tools=tools),
        mcp_servers=None,
        tool_selection=None,
    )


def _policy(workspace: Path) -> Policy:
    raw = json.loads(json.dumps(DEFAULT_POLICY))
    raw["scope"] = "POWER_USER"
    raw["workspace_root"] = str(workspace)
    raw["tools"]["allow_prefix"].append("blockchain.")
    raw["audit"] = {"write_mode": "jsonl_only"}
    raw["context_metadata"] = {
        "runtime_tools": {
            "blockchain": {
                "enabled": True,
                "rpc_url": RPC_URL,
                "chain_id": CHAIN_ID,
                "signer_secret_key": "local-anvil-signer",
                "signer_secret_namespace": "blockchain",
                "writes_enabled": True,
                "max_total_fee_wei": "10000000000000000",
                "receipt_timeout_seconds": 10,
            }
        }
    }
    return Policy(raw=raw)


def _policy_ctl(path: Path) -> PolicyCtl:
    ctl = PolicyCtl.with_sqlite(path, config=PolicyConfig(mode="enforce"))
    ctl.register_risk(
        "blockchain.send_transaction",
        RiskSpec(
            risk_class="financial",
            side_effects="external_account",
            reversibility="irreversible",
            default_confirm=True,
        ),
    )
    return ctl


def _approval(ctl: PolicyCtl, args: dict, invocation_id: str, action: str):
    decision = ctl.check(
        {
            "tool": "blockchain",
            "method": "send_transaction",
            "args": args,
            "invocation_id": invocation_id,
        },
        {
            "subject_id": "local",
            "session_id": "bttl-local",
            "trace_id": invocation_id,
            "mode_name": "act",
        },
    )
    assert decision.approval_id
    grant_id = ctl.resolve_confirmation(decision.approval_id, action)
    return decision, grant_id


def main() -> int:
    anvil = shutil.which("anvil")
    if not anvil:
        raise RuntimeError("anvil is required")
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [anvil, "--port", "18547", "--chain-id", str(CHAIN_ID), "--silent"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        web3 = Web3(Web3.HTTPProvider(RPC_URL))
        _wait_rpc(web3)
        secret = SecretService(
            str(ARTIFACT_ROOT / "secrets.db"),
            Fernet.generate_key().decode(),
        )
        asyncio.run(
            secret.set_secret(
                "local-anvil-signer",
                PRIVATE_KEY,
                namespace="blockchain",
            )
        )
        policy_ctl = _policy_ctl(ARTIFACT_ROOT / "policy.db")
        bootstrap = build_runtime_bootstrap(
            config=_runtime_config(),
            workspace_root=ARTIFACT_ROOT,
            run_root=ARTIFACT_ROOT / "bootstrap",
            strict=True,
        )
        adapter = ToolAdapter(
            workspace_root=ARTIFACT_ROOT,
            runtime_config=_runtime_config().runtime,
            runtime_registry=bootstrap.registry,
            policy=_policy(ARTIFACT_ROOT),
            policy_ctl=policy_ctl,
            secret_service=secret,
            agent_id="bttl-local",
        )
        context = SimpleNamespace(
            policy=_policy(ARTIFACT_ROOT),
            secret_service=secret,
        )
        inspect = inspect_blockchain({"action": "chain_summary"}, context)
        before_balance = web3.eth.get_balance(RECIPIENT)
        prepared = prepare_transaction(
            {
                "kind": "native_transfer",
                "to_address": RECIPIENT,
                "value_wei": "1",
            },
            context,
        )
        send_args = {
            "transaction": prepared["transaction"],
            "call_context": prepared["call_context"],
            "preparation_digest": prepared["preparation_digest"],
        }

        denied_decision, denied_grant = _approval(
            policy_ctl, send_args, "denied-invocation", "deny"
        )
        assert denied_grant is None
        denied = adapter.execute(
            command={
                "tool_name": "blockchain.send_transaction",
                "args": send_args,
                "idempotency_key": "denied-invocation",
            },
            session_id="bttl-local",
            trace_id="denied-invocation",
        )
        assert denied["error"]["code"] == "POLICY_DENIED"
        assert web3.eth.get_balance(RECIPIENT) == before_balance

        allowed_decision, grant_id = _approval(
            policy_ctl, send_args, "allowed-invocation", "allow_once"
        )
        assert grant_id
        allowed = adapter.execute(
            command={
                "tool_name": "blockchain.send_transaction",
                "args": send_args,
                "idempotency_key": "allowed-invocation",
            },
            session_id="bttl-local",
            trace_id="allowed-invocation",
        )
        assert allowed["status"] == "success", allowed
        result = allowed["outputs"]
        transaction_hash = result["data"]["transaction_hash"]
        receipt = web3.eth.get_transaction_receipt(transaction_hash)
        after_balance = web3.eth.get_balance(RECIPIENT)
        assert after_balance == before_balance + 1

        stale_decision, stale_grant_id = _approval(
            policy_ctl, send_args, "stale-invocation", "allow_once"
        )
        assert stale_grant_id
        block_before_stale = web3.eth.block_number
        stale = adapter.execute(
            command={
                "tool_name": "blockchain.send_transaction",
                "args": send_args,
                "idempotency_key": "stale-invocation",
            },
            session_id="bttl-local",
            trace_id="stale-invocation",
        )
        stale_result = stale["outputs"]
        assert stale_result["error"]["code"] == "STALE_PREPARATION"
        assert stale_result["data"]["broadcast_attempts"] == 0
        assert web3.eth.block_number == block_before_stale

        audit_files = sorted(
            (RUNTIME_GENERATED_ROOT.parent / "tool-runs").rglob("audit.jsonl")
        )
        audit = json.loads(audit_files[-1].read_text().splitlines()[-1])
        authorization = {
            "invocation_hash": audit["invocation_hash"],
            "approval_id": audit["approval_id"],
            "grant_id": audit["consumed_grant_id"],
            "duration_type": audit["duration_type"],
        }
        evidence = {
            "provider_request": {
                "tool_name": "blockchain.send_transaction",
                "arguments": send_args,
            },
            "policy_decision": allowed_decision.to_dict(),
            "execution_authorization": authorization,
            "tool_result": result,
            "transaction_audit": audit,
            "chain_state": {
                "chain_id": web3.eth.chain_id,
                "receipt_status": int(receipt["status"]),
                "recipient_balance_before": str(before_balance),
                "recipient_balance_after": str(after_balance),
            },
            "denied_send": {
                "policy_decision": denied_decision.to_dict(),
                "tool_result": denied,
                "execution_authorization": None,
                "transaction_audit": None,
                "chain_state_unchanged": True,
            },
            "stale_send": {
                "policy_decision": stale_decision.to_dict(),
                "tool_result": stale,
                "chain_state_unchanged": True,
            },
            "inspect": inspect,
            "prepared": prepared,
        }
        output = ARTIFACT_ROOT / "evidence.json"
        output.write_text(json.dumps(evidence, indent=2, sort_keys=True))
        print(f"BTTL local Anvil PASS evidence={output}")
        adapter.close()
        policy_ctl.close()
        return 0
    finally:
        process.terminate()
        process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
