from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

from cryptography.fernet import Fernet
from web3 import Web3

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.helpers.live_e2e_profiles import resolve_live_framework_root  # noqa: E402
from tests.helpers.runtime_roots import (  # noqa: E402
    RUNTIME_ROOT_ENV_VARS,
    isolate_runtime_roots,
)

FRAMEWORK_ROOT = resolve_live_framework_root(ROOT)


@contextmanager
def _private_runtime_root() -> Iterator[Path]:
    previous = {name: os.environ.get(name) for name in RUNTIME_ROOT_ENV_VARS}
    generated_root = isolate_runtime_roots(prefix="openminion-bttl-local-")
    try:
        yield generated_root
    finally:
        try:
            shutil.rmtree(generated_root.parents[1])
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


with _private_runtime_root():
    from openminion.base.config.runtime.tools import (  # noqa: E402
        BlockchainToolRuntimeConfig,
        ToolRuntimeConfig,
    )
    from openminion.base.config.env import resolve_environment_config  # noqa: E402
    from openminion.modules.brain.adapters.tool.runtime import ToolAdapter  # noqa: E402
    from openminion.modules.policy.models import PolicyConfig, RiskSpec  # noqa: E402
    from openminion.modules.policy.runtime.service import PolicyCtl  # noqa: E402
    from openminion.modules.secret.service import SecretService  # noqa: E402
    from openminion.modules.tool.bootstrap import build_runtime_bootstrap  # noqa: E402
    from openminion.modules.tool.runtime.policy import (  # noqa: E402
        DEFAULT_POLICY,
        Policy,
    )
    from openminion.tools.blockchain.confirmation import (  # noqa: E402
        build_blockchain_send_confirmation_preview,
    )
    from openminion.tools.blockchain.runtime import (  # noqa: E402
        inspect_blockchain,
        prepare_transaction,
    )

ARTIFACT_ROOT = FRAMEWORK_ROOT / "workspace-tmp" / "bttl-e2e" / "local"
RPC_URL = ""
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


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


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
        confirmation_preview=build_blockchain_send_confirmation_preview(args),
    )
    assert decision.approval_id
    grant_id = ctl.resolve_confirmation(decision.approval_id, action)
    return decision, grant_id


def _run(runtime_generated_root: Path) -> int:
    global RPC_URL
    anvil = shutil.which("anvil")
    if not anvil:
        raise RuntimeError("anvil is required")
    if ARTIFACT_ROOT.exists():
        shutil.rmtree(ARTIFACT_ROOT)
    ARTIFACT_ROOT.mkdir(parents=True)
    port = _free_port()
    RPC_URL = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [anvil, "--port", str(port), "--chain-id", str(CHAIN_ID), "--silent"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    adapter = None
    policy_ctl = None
    secret = None
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
            session_id="bttl-local",
            env=resolve_environment_config(),
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
        send_reference = {"preparation_digest": prepared["preparation_digest"]}
        send_args = {
            "transaction": prepared["transaction"],
            "call_context": prepared["call_context"],
            "preparation_digest": prepared["preparation_digest"],
        }

        denied_decision, denied_grant = _approval(
            policy_ctl, send_args, "denied-invocation", "deny"
        )
        assert denied_grant is None
        retry_decision, retry_grant = _approval(
            policy_ctl, send_args, "denied-retry", "deny"
        )
        assert retry_grant is None
        assert retry_decision.approval_id != denied_decision.approval_id
        denied = {
            "status": "needs_user",
            "error": {
                "code": "CONFIRM_REQUIRED",
                "message": retry_decision.reason,
                "details": {"approval_id": retry_decision.approval_id},
            },
        }
        assert web3.eth.get_balance(RECIPIENT) == before_balance

        allowed_decision, grant_id = _approval(
            policy_ctl, send_args, "allowed-invocation", "allow_once"
        )
        assert grant_id
        allowed = adapter.execute(
            command={
                "tool_name": "blockchain.send_transaction",
                "args": send_reference,
                "idempotency_key": "allowed-invocation",
                "inputs": {
                    "confirmation_grant_id": allowed_decision.approval_id,
                    "confirmation_source": "policy_replay",
                },
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
                "args": send_reference,
                "idempotency_key": "stale-invocation",
                "inputs": {
                    "confirmation_grant_id": stale_decision.approval_id,
                    "confirmation_source": "policy_replay",
                },
            },
            session_id="bttl-local",
            trace_id="stale-invocation",
        )
        stale_result = stale["outputs"]
        assert stale_result["error"]["code"] == "STALE_PREPARATION"
        assert stale_result["data"]["broadcast_attempts"] == 0
        assert web3.eth.block_number == block_before_stale

        audit_files = sorted(
            (runtime_generated_root.parent / "tool-runs").rglob("audit.jsonl")
        )
        audits = [
            json.loads(line)
            for audit_file in audit_files
            for line in audit_file.read_text().splitlines()
            if line
        ]
        allowed_audits = [
            record for record in audits if record["invocation_id"] == "allowed-invocation"
        ]
        stale_audits = [
            record for record in audits if record["invocation_id"] == "stale-invocation"
        ]
        assert len(allowed_audits) == 1
        assert len(stale_audits) == 1
        audit = allowed_audits[0]
        stale_audit = stale_audits[0]
        assert audit["approval_id"] == allowed_decision.approval_id
        assert audit["state"] == "succeeded"
        assert audit["broadcast_attempts"] == 1
        assert audit["transaction_hash"] == transaction_hash
        assert stale_audit["approval_id"] == stale_decision.approval_id
        assert stale_audit["state"] == "stale"
        assert stale_audit["broadcast_attempts"] == 0
        assert stale_audit["transaction_hash"] == ""
        authorization = {
            "invocation_hash": audit["invocation_hash"],
            "approval_id": audit["approval_id"],
            "grant_id": audit["consumed_grant_id"],
            "duration_type": audit["duration_type"],
        }
        evidence = {
            "schema_version": "bttl-e2e-v2",
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "scenario_id": "configured-local-anvil",
            "protocol": "production_tool_adapter",
            "terminal_result": "completed",
            "provider_request": {
                "tool_name": "blockchain.send_transaction",
                "arguments": send_reference,
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
                "transaction_audit": stale_audit,
                "chain_state_unchanged": True,
            },
            "inspect": inspect,
            "prepared": prepared,
        }
        output = ARTIFACT_ROOT / "evidence.json"
        output.write_text(json.dumps(evidence, indent=2, sort_keys=True))
        print(f"BTTL local Anvil PASS evidence={output}")
        return 0
    finally:
        try:
            if adapter is not None:
                adapter.close()
            if policy_ctl is not None:
                policy_ctl.close()
            if secret is not None:
                secret.close_sync()
        finally:
            process.terminate()
            process.wait(timeout=5)


def main() -> int:
    with _private_runtime_root() as runtime_generated_root:
        return _run(runtime_generated_root)


if __name__ == "__main__":
    raise SystemExit(main())
