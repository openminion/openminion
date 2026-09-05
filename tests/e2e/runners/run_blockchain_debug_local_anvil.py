from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

from cryptography.fernet import Fernet
from web3 import Web3

ROOT = Path(__file__).resolve().parents[3]


def _framework_root() -> Path:
    return min(
        (
            parent
            for parent in ROOT.parents
            if (parent / "test-configs" / "per-agent-minimax-official.json").exists()
        ),
        key=lambda path: len(path.parts),
    )


FRAMEWORK_ROOT = _framework_root()
sys.path.insert(0, str(ROOT))

from tests.helpers.runtime_roots import isolate_runtime_roots  # noqa: E402

RUNTIME_ROOT = isolate_runtime_roots(prefix="openminion-bdtc-local-")
DATA_ROOT = RUNTIME_ROOT.parent

from openminion.base.config.runtime.tools import (  # noqa: E402
    BlockchainToolRuntimeConfig,
    ToolRuntimeConfig,
)
from openminion.modules.brain.adapters.tool.runtime import ToolAdapter  # noqa: E402
from openminion.modules.policy.models import PolicyConfig, RiskSpec  # noqa: E402
from openminion.modules.policy.runtime.service import PolicyCtl  # noqa: E402
from openminion.modules.secret.service import SecretService  # noqa: E402
from openminion.modules.tool.base import ToolExecutionContext  # noqa: E402
from openminion.modules.tool.bootstrap import build_runtime_bootstrap  # noqa: E402
from openminion.modules.tool.contracts import ProviderToolCall  # noqa: E402
from openminion.modules.tool.executor import execute_single_call  # noqa: E402
from openminion.modules.tool.runtime.policy import DEFAULT_POLICY, Policy  # noqa: E402
from openminion.tools.blockchain.confirmation import (  # noqa: E402
    build_blockchain_send_confirmation_preview,
    preview_to_dict,
)
from openminion.tools.blockchain.runtime import (  # noqa: E402
    inspect_blockchain,
    prepare_transaction,
)

EVIDENCE_ROOT = FRAMEWORK_ROOT / "workspace-tmp" / "bdtc-e2e" / "local"
FIXTURE = ROOT / "tests" / "e2e" / "fixtures" / "blockchain" / "reference_swap.json"
RPC_URL = "http://127.0.0.1:18549"
CHAIN_ID = 31337
PRIVATE_KEY = "0x" + "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
SENDER = Web3.to_checksum_address("0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266")
RECIPIENT = Web3.to_checksum_address("0x" + "44" * 20)


class _Telemetry:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit_canonical_event(
        self,
        session_id: str,
        turn_id: str,
        event_type: str,
        payload: dict,
        **kwargs,
    ) -> None:
        self.events.append(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "event_type": event_type,
                "payload": payload,
                "status": kwargs.get("status"),
            }
        )

    def emit_module_counter(self, *_args, **_kwargs) -> None:
        return None

    def emit_module_operation(self, *_args, **_kwargs) -> None:
        return None


def _clean_source_commit() -> str:
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        text=True,
    )
    if status:
        raise RuntimeError("BDTC evidence requires a clean OpenMinion checkout")
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


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
            signer_secret_key="bdtc-local-signer",
            signer_secret_namespace="blockchain",
            writes_enabled=True,
            receipt_timeout_seconds=1,
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
    raw["audit"] = {"write_mode": "jsonl_only"}
    raw["context_metadata"] = {
        "runtime_tools": {
            "blockchain": {
                "enabled": True,
                "rpc_url": RPC_URL,
                "chain_id": CHAIN_ID,
                "signer_secret_key": "bdtc-local-signer",
                "signer_secret_namespace": "blockchain",
                "writes_enabled": True,
                "max_total_fee_wei": "10000000000000000",
                "receipt_timeout_seconds": 1,
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
    preview = build_blockchain_send_confirmation_preview(args)
    decision = ctl.check(
        {
            "tool": "blockchain",
            "method": "send_transaction",
            "args": args,
            "invocation_id": invocation_id,
        },
        {
            "subject_id": "local",
            "session_id": "bdtc-local",
            "trace_id": invocation_id,
            "mode_name": "act",
        },
        confirmation_preview=preview,
    )
    assert decision.approval_id
    grant_id = ctl.resolve_confirmation(decision.approval_id, action)
    return decision, grant_id, preview


def _debug_call(
    bootstrap,
    telemetry: _Telemetry,
    workspace: Path,
    arguments: dict,
    call_id: str,
):
    context = ToolExecutionContext(
        channel="test",
        target="local",
        session_id="bdtc-local",
        metadata={
            "session_id": "bdtc-local",
            "trace_id": call_id,
            "tool_call_origin": "model",
            **_policy(workspace).raw["context_metadata"],
        },
        telemetryctl=telemetry,
        tool_registry=bootstrap.registry,
    )
    return execute_single_call(
        bootstrap.registry,
        call=ProviderToolCall(
            name="blockchain.debug",
            arguments=arguments,
            id=call_id,
        ),
        context=context,
        available_tool_names=tuple(bootstrap.registry.list()),
        runtime_binding_policies=bootstrap.policy_manager,
    )


def main() -> int:
    source_commit = _clean_source_commit()
    anvil = shutil.which("anvil")
    if not anvil:
        raise RuntimeError("anvil is required")
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [anvil, "--port", "18549", "--chain-id", str(CHAIN_ID), "--silent"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    policy_ctl = None
    secret = None
    try:
        web3 = Web3(Web3.HTTPProvider(RPC_URL))
        _wait_rpc(web3)
        artifact = json.loads(FIXTURE.read_text())
        factory = web3.eth.contract(
            abi=artifact["abi"], bytecode=artifact["creation_bytecode"]
        )
        deploy_hash = factory.constructor().transact({"from": SENDER})
        deploy_receipt = web3.eth.wait_for_transaction_receipt(deploy_hash)
        contract = web3.eth.contract(
            address=deploy_receipt["contractAddress"], abi=artifact["abi"]
        )

        secret = SecretService(
            str(DATA_ROOT / "secrets.db"), Fernet.generate_key().decode()
        )
        asyncio.run(
            secret.set_secret("bdtc-local-signer", PRIVATE_KEY, namespace="blockchain")
        )
        policy_ctl = _policy_ctl(DATA_ROOT / "policy.db")
        bootstrap = build_runtime_bootstrap(
            config=_runtime_config(),
            workspace_root=DATA_ROOT,
            run_root=DATA_ROOT / "bootstrap",
            strict=True,
        )
        adapter = ToolAdapter(
            workspace_root=DATA_ROOT,
            runtime_config=_runtime_config().runtime,
            runtime_registry=bootstrap.registry,
            policy=_policy(DATA_ROOT),
            policy_ctl=policy_ctl,
            secret_service=secret,
            agent_id="bdtc-local",
        )
        runtime_context = SimpleNamespace(
            policy=_policy(DATA_ROOT), secret_service=secret
        )
        telemetry = _Telemetry()

        chain = inspect_blockchain({"action": "chain_summary"}, runtime_context)
        account = inspect_blockchain(
            {"action": "native_balance", "address": SENDER}, runtime_context
        )
        quote_abi = next(
            item for item in artifact["abi"] if item.get("name") == "quote"
        )
        quote = inspect_blockchain(
            {
                "action": "contract_read",
                "contract_address": contract.address,
                "function_abi": {
                    key: value for key, value in quote_abi.items() if key != "inputs"
                }
                | {
                    "inputs": [
                        {
                            key: value
                            for key, value in quote_abi["inputs"][0].items()
                            if key != "internalType"
                        }
                    ],
                    "outputs": [
                        {
                            key: value
                            for key, value in quote_abi["outputs"][0].items()
                            if key != "internalType"
                        }
                    ],
                },
                "function_args": [7],
            },
            runtime_context,
        )
        closed_swap_abi = {
            "type": "function",
            "name": "swap",
            "inputs": [
                {
                    "name": "request",
                    "type": "tuple",
                    "components": [
                        {"name": "recipient", "type": "address"},
                        {"name": "amountIn", "type": "uint256"},
                        {"name": "minAmountOut", "type": "uint256"},
                    ],
                }
            ],
            "outputs": [{"name": "amountOut", "type": "uint256"}],
            "stateMutability": "nonpayable",
        }
        error_abi = {
            "type": "error",
            "name": "MinimumOutput",
            "inputs": [
                {"name": "quoted", "type": "uint256"},
                {"name": "minimum", "type": "uint256"},
            ],
        }
        reverting_data = contract.functions.swap(
            (RECIPIENT, 7, 99)
        )._encode_transaction_data()
        revert_args = {
            "action": "simulate_call",
            "from_address": SENDER,
            "to_address": contract.address,
            "data": reverting_data,
            "error_abis": [error_abi],
        }
        revert_call = _debug_call(
            bootstrap,
            telemetry,
            DATA_ROOT,
            revert_args,
            "debug-revert",
        )
        assert revert_call.data["error_code"] == "SIMULATION_REVERTED"
        assert not revert_call.ok
        assert revert_call.error == "Transaction simulation reverted."

        pre_state = str(contract.functions.outputOf(RECIPIENT).call())
        prepared = prepare_transaction(
            {
                "kind": "contract_call",
                "contract_address": contract.address,
                "function_abi": closed_swap_abi,
                "function_args": [[RECIPIENT, 7, 14]],
            },
            runtime_context,
        )
        send_args = {
            "transaction": prepared["transaction"],
            "call_context": prepared["call_context"],
            "preparation_digest": prepared["preparation_digest"],
        }
        denied_decision, denied_grant, preview = _approval(
            policy_ctl, send_args, "denied", "deny"
        )
        assert denied_grant is None
        denied = adapter.execute(
            command={
                "tool_name": "blockchain.send_transaction",
                "args": send_args,
                "idempotency_key": "denied",
            },
            session_id="bdtc-local",
            trace_id="denied",
        )
        assert denied["error"]["code"] == "POLICY_DENIED"
        assert str(contract.functions.outputOf(RECIPIENT).call()) == pre_state

        prepared_again = prepare_transaction(
            {
                "kind": "contract_call",
                "contract_address": contract.address,
                "function_abi": closed_swap_abi,
                "function_args": [[RECIPIENT, 7, 14]],
            },
            runtime_context,
        )
        send_args = {
            "transaction": prepared_again["transaction"],
            "call_context": prepared_again["call_context"],
            "preparation_digest": prepared_again["preparation_digest"],
        }
        allowed_decision, grant_id, allowed_preview = _approval(
            policy_ctl, send_args, "allowed", "allow_once"
        )
        assert grant_id
        web3.provider.make_request("evm_setAutomine", [False])
        allowed = adapter.execute(
            command={
                "tool_name": "blockchain.send_transaction",
                "args": send_args,
                "idempotency_key": "allowed",
            },
            session_id="bdtc-local",
            trace_id="allowed",
        )
        assert allowed["error"]["code"] == "RECEIPT_PENDING", allowed
        transaction_hash = allowed["outputs"]["data"]["transaction_hash"]
        assert allowed["outputs"]["data"]["broadcast_attempts"] == 1
        web3.provider.make_request("evm_mine", [])
        web3.provider.make_request("evm_setAutomine", [True])
        web3.eth.wait_for_transaction_receipt(transaction_hash)
        receipt = inspect_blockchain(
            {"action": "receipt", "transaction_hash": transaction_hash},
            runtime_context,
        )
        event_abi = {
            "type": "event",
            "name": "Swap",
            "inputs": [
                {"name": "sender", "type": "address", "indexed": True},
                {"name": "recipient", "type": "address", "indexed": True},
                {"name": "amountIn", "type": "uint256", "indexed": False},
                {"name": "amountOut", "type": "uint256", "indexed": False},
            ],
            "anonymous": False,
        }
        events_call = _debug_call(
            bootstrap,
            telemetry,
            DATA_ROOT,
            {
                "action": "transaction_events",
                "transaction_hash": transaction_hash,
                "event_abi": event_abi,
                "contract_address": contract.address,
            },
            "debug-events",
        )
        assert events_call.ok, events_call
        final_state = str(contract.functions.outputOf(RECIPIENT).call())
        assert final_state == "14"

        audits = [
            json.loads(line)
            for path in (DATA_ROOT / "tool-runs").rglob("audit.jsonl")
            for line in path.read_text().splitlines()
            if line.strip()
        ]
        transaction_audits = [
            item
            for item in audits
            if item.get("event_type") == "tool.blockchain.transaction"
        ]
        assert len(transaction_audits) == 1
        audit = transaction_audits[0]
        evidence = {
            "schema_version": "bdtc-e2e-v1",
            "source_commit": source_commit,
            "profile": "local",
            "scenario_id": str(uuid4()),
            "debug_revert": {
                "tool_call_id": "debug-revert",
                "tool": "blockchain.debug",
                "action": "simulate_call",
                "error_code": "SIMULATION_REVERTED",
                "revert_kind": revert_call.data["details"]["revert"]["kind"],
                "provider_text_exposed": False,
            },
            "prepared": {
                "chain_id": str(prepared_again["transaction"]["chain_id"]),
                "contract_address": contract.address,
                "function_signature": prepared_again["call_context"][
                    "function_signature"
                ],
                "function_args": prepared_again["call_context"]["function_args"],
                "preparation_digest": prepared_again["preparation_digest"],
                "pre_send_state": pre_state,
            },
            "approval_preview": preview_to_dict(allowed_preview),
            "denied": {
                "approval_id": denied_decision.approval_id,
                "broadcast_attempts": 0,
                "post_denial_state": pre_state,
            },
            "authorized": {
                "approval_id": allowed_decision.approval_id,
                "consumed_grant_id": grant_id,
                "invocation_hash": allowed_decision.invocation_hash,
                "broadcast_attempts": 1,
                "transaction_hash": transaction_hash,
            },
            "verified": {
                "receipt_status": receipt["data"]["status"],
                "event_signature": events_call.data["event_signature"],
                "event_arguments": events_call.data["events"][0]["arguments"],
                "event_tool_call_id": "debug-events",
                "final_state": final_state,
                "transaction_hash": transaction_hash,
            },
            "telemetry": telemetry.events,
            "audit": {
                "prepare_transaction_event_count": 0,
                "denied_send_transaction_event_count": 0,
                "authorized_send_transaction_event_count": 1,
                "authorized_event": audit,
            },
            "transcript_path": None,
            "chain_summary": chain,
            "account": account,
            "quote": quote,
        }
        (EVIDENCE_ROOT / "evidence.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n"
        )
        return 0
    finally:
        if policy_ctl is not None:
            policy_ctl.close()
        if secret is not None:
            secret.close_sync()
        process.terminate()
        process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
