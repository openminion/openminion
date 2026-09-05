from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
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

RUNTIME_ROOT = isolate_runtime_roots(prefix="openminion-bdtc-focus-")

from openminion.modules.secret.service import SecretService  # noqa: E402
from tests.e2e.cli.focus.harness import FocusProbe  # noqa: E402
from tests.e2e.cli.focus.harness.probe import (  # noqa: E402
    inline_approval_menu,
    sidecar_consent_prompt_visible,
)
from tests.e2e.cli.focus.harness.scenarios import FocusScenario  # noqa: E402

EVIDENCE_ROOT = FRAMEWORK_ROOT / "workspace-tmp" / "bdtc-e2e" / "focus"
CONFIG_SOURCE = FRAMEWORK_ROOT / "test-configs" / "per-agent-minimax-official.json"
FIXTURE = ROOT / "tests" / "e2e" / "fixtures" / "blockchain" / "reference_swap.json"
RPC_URL = "http://127.0.0.1:18550"
CHAIN_ID = 31337
PRIVATE_KEY = "0x" + "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
SENDER = Web3.to_checksum_address("0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266")
RECIPIENT = Web3.to_checksum_address("0x" + "55" * 20)
EXPECTED_PREFIX_INVOCATIONS = [
    ("blockchain.debug", "simulate_call"),
    ("blockchain.inspect", "contract_read"),
    ("blockchain.prepare_transaction", "contract_call"),
    ("blockchain.send_transaction", "send_transaction"),
    ("blockchain.prepare_transaction", "contract_call"),
    ("blockchain.send_transaction", "send_transaction"),
]
EXPECTED_FINAL_INVOCATIONS = [
    ("blockchain.inspect", "receipt"),
    ("blockchain.debug", "transaction_events"),
    ("blockchain.inspect", "contract_read"),
]
EXPECTED_INVOCATIONS = [*EXPECTED_PREFIX_INVOCATIONS, *EXPECTED_FINAL_INVOCATIONS]
EXPECTED_TURN_GROUPS = [0, 1, 2, 3, 4, 5, 6, 6, 6]


def _turn_scope_groups(invocations: list[dict]) -> list[int]:
    groups: dict[str, int] = {}
    return [
        groups.setdefault(item["turn_scope_id"], len(groups)) for item in invocations
    ]


def _required_invocations(invocations: list[dict]) -> list[dict]:
    prefix: list[dict] = []
    expected = iter(EXPECTED_PREFIX_INVOCATIONS)
    current = next(expected, None)
    prefix_end = 0
    for index, invocation in enumerate(invocations):
        if current is None:
            break
        observed = (invocation["tool"], invocation["operation"])
        if observed != current:
            continue
        prefix.append(invocation)
        prefix_end = index + 1
        current = next(expected, None)
    assert current is None, "missing required blockchain invocation sequence"

    final: dict[tuple[str, str], dict] = {}
    expected_final = set(EXPECTED_FINAL_INVOCATIONS)
    for invocation in invocations[prefix_end:]:
        observed = (invocation["tool"], invocation["operation"])
        if observed not in expected_final:
            continue
        assert observed not in final, "duplicate required blockchain invocation"
        final[observed] = invocation
    assert final.keys() == expected_final, "missing required final blockchain calls"
    return [*prefix, *(final[item] for item in EXPECTED_FINAL_INVOCATIONS)]


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


def _write_config(data_root: Path) -> Path:
    payload = json.loads(CONFIG_SOURCE.read_text())
    payload["action_policy"] = {
        "mode": "ask",
        "default_action": "require_confirm",
        "allow_read_only_without_prompt": True,
        "rules": [],
    }
    payload.setdefault("runtime", {}).setdefault("tools", {})["blockchain"] = {
        "enabled": True,
        "rpc_url": RPC_URL,
        "chain_id": CHAIN_ID,
        "signer_secret_key": "bdtc-focus-signer",
        "signer_secret_namespace": "blockchain",
        "writes_enabled": True,
        "max_total_fee_wei": "10000000000000000",
        "receipt_timeout_seconds": 10,
    }
    path = data_root / "config.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def _rows(database: Path, table: str) -> list[dict]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
    finally:
        connection.close()


def _tool_results(data_root: Path) -> list[dict]:
    results: list[dict] = []
    for database in data_root.rglob("*.db"):
        try:
            rows = _rows(database, "messages")
        except sqlite3.Error:
            continue
        for row in rows:
            try:
                metadata = json.loads(row.get("metadata_json") or "{}")
                tool_results = json.loads(metadata.get("tool_results") or "[]")
            except (json.JSONDecodeError, TypeError):
                continue
            results.extend(
                result
                for result in tool_results
                if str(result.get("tool_name", "")).startswith("blockchain.")
            )
    return results


def _telemetry(data_root: Path) -> list[dict]:
    database = data_root / "telemetry" / "telemetry.db"
    if not database.exists():
        return []
    events: list[dict] = []
    for row in _rows(database, "events"):
        if row.get("event_type") not in {
            "tool.execution.started",
            "tool.execution.completed",
            "tool.execution.failed",
        }:
            continue
        data = json.loads(row.get("data") or "{}")
        if data.get("tool_name") == "blockchain.debug":
            events.append(
                {
                    "event_type": row["event_type"],
                    "turn_id": row["turn_id"],
                    "tool_call_id": data.get("tool_call_id"),
                    "tool_name": data.get("tool_name"),
                }
            )
    return events


def _requested_invocations(data_root: Path) -> list[dict]:
    database = data_root / "telemetry" / "telemetry.db"
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT rowid, * FROM events "
            "WHERE event_type = 'tool.call.requested' ORDER BY rowid"
        )
        invocations = []
        for row in rows:
            data = json.loads(row["data"] or "{}")
            tool = str(data.get("canonical_name", ""))
            if not tool.startswith("blockchain."):
                continue
            arguments = data.get("sanitized_normalized_arguments")
            arguments = arguments if isinstance(arguments, dict) else {}
            operation = str(
                arguments.get("action")
                or arguments.get("kind")
                or tool.removeprefix("blockchain.")
            )
            invocations.append(
                {
                    "tool": tool,
                    "operation": operation,
                    "tool_call_id": str(data.get("call_id", "")),
                    "turn_scope_id": str(data.get("turn_scope_id", "")),
                }
            )
        return invocations
    finally:
        connection.close()


def _debug_revert(data_root: Path) -> tuple[str, dict]:
    call_id = next(
        item["tool_call_id"]
        for item in _requested_invocations(data_root)
        if (item["tool"], item["operation"]) == ("blockchain.debug", "simulate_call")
    )
    for result in _tool_results(data_root):
        data = result.get("data")
        if (
            result.get("call_id") == call_id
            and isinstance(data, dict)
            and data.get("error_code") == "SIMULATION_REVERTED"
        ):
            return call_id, data["error_details"]["revert"]
    raise AssertionError("missing structured Focus blockchain revert")


def _pending_approval_id(data_root: Path, seen: set[str]) -> str | None:
    database = data_root / "policy" / "policy.db"
    if not database.exists():
        return None
    try:
        rows = _rows(database, "policy_pending_confirmations")
    except sqlite3.Error:
        return None
    return next(
        (
            row["approval_id"]
            for row in reversed(rows)
            if row["state"] == "pending" and row["approval_id"] not in seen
        ),
        None,
    )


def _wait_for_approval(
    probe: FocusProbe,
    session,
    prompt: str,
    timeout: int,
    data_root: Path,
    seen: set[str],
) -> tuple[str, str]:
    offset = len(session.visible_transcript)
    probe._submit_composer_line(session, prompt)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        approval_id = _pending_approval_id(data_root, seen)
        if approval_id is not None:
            return session.visible_transcript[offset:], approval_id
        time.sleep(0.1)
    raise AssertionError(
        f"Focus approval did not appear\n{session.screen_text[-2000:]}"
    )


def _run_turn(probe: FocusProbe, session, scenario: FocusScenario) -> str:
    offset = len(session.visible_transcript)
    probe.run_turn(session, scenario)
    probe._wait_for_composer(session, timeout=scenario.timeout)
    return session.visible_transcript[offset:]


def _reply_to_approval(probe: FocusProbe, session, reply: str, timeout: int) -> str:
    offset = len(session.visible_transcript)
    if inline_approval_menu(session.screen_text) is not None:
        probe._submit_inline_approval(session, reply)
    elif sidecar_consent_prompt_visible(session.screen_text):
        probe._submit_sidecar_consent(session, reply)
    else:
        probe._submit_composer_line(session, reply)
    probe._wait_for_composer(session, timeout=timeout)
    return session.visible_transcript[offset:]


def _closed_abis() -> tuple[dict, dict, dict, dict, dict]:
    quote = {
        "type": "function",
        "name": "quote",
        "inputs": [{"name": "amountIn", "type": "uint256"}],
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "pure",
    }
    swap = {
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
    state = {
        "type": "function",
        "name": "outputOf",
        "inputs": [{"name": "recipient", "type": "address"}],
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "view",
    }
    error = {
        "type": "error",
        "name": "MinimumOutput",
        "inputs": [
            {"name": "quoted", "type": "uint256"},
            {"name": "minimum", "type": "uint256"},
        ],
    }
    event = {
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
    return quote, swap, state, error, event


def _scenario(scenario_id: str, prompt: str, timeout: int = 480) -> FocusScenario:
    return FocusScenario(
        scenario_id=scenario_id,
        prompt=prompt,
        expected_markers=(),
        timeout=timeout,
        include_project_context=False,
    )


def main() -> int:
    if os.getenv("OPENMINION_LIVE_CLI_FOCUS_E2E") != "1":
        raise RuntimeError("OPENMINION_LIVE_CLI_FOCUS_E2E=1 is required")
    source_commit = _clean_source_commit()
    anvil = shutil.which("anvil")
    if not anvil:
        raise RuntimeError("anvil is required")
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    data_root = RUNTIME_ROOT.parent
    data_root.mkdir(parents=True, exist_ok=True)
    master_key = Fernet.generate_key().decode()
    config_path = _write_config(data_root)
    secret = SecretService(str(data_root / "secret" / "secrets.db"), master_key)
    asyncio.run(
        secret.set_secret("bdtc-focus-signer", PRIVATE_KEY, namespace="blockchain")
    )
    secret.close_sync()
    os.environ["OPENMINION_SECRET_KEY"] = master_key
    os.environ["OPENMINION_TRACE_REQUESTS"] = "1"
    process = subprocess.Popen(
        [anvil, "--port", "18550", "--chain-id", str(CHAIN_ID), "--silent"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
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
        quote_abi, swap_abi, state_abi, error_abi, event_abi = _closed_abis()
        reverting_calldata = contract.functions.swap(
            (RECIPIENT, 7, 99)
        )._encode_transaction_data()

        def compact(value: object) -> str:
            return json.dumps(value, separators=(",", ":"))

        prepare_prompt = (
            f"Prepare but do not send a call to contract {contract.address} using "
            f"function ABI {compact(swap_abi)} and tuple arguments "
            f"{compact([[RECIPIENT, 7, 14]])}."
        )
        turns = [
            _scenario(
                "turn-1",
                f"On the configured local EVM chain, simulate the call from {SENDER} "
                f"to {contract.address} with calldata {reverting_calldata}. Use error "
                f"ABI {compact(error_abi)}. Explain the structured revert. Do not send "
                "a transaction.",
            ),
            _scenario(
                "turn-2",
                f"Read the quote from contract {contract.address} using function ABI "
                f"{compact(quote_abi)} and arguments {compact([7])}. Do not prepare or "
                "send a transaction.",
            ),
            _scenario("turn-3", prepare_prompt),
        ]
        probe = FocusProbe(
            python_bin=Path(sys.executable),
            openminion_root=ROOT,
            framework_root=FRAMEWORK_ROOT,
            data_root=data_root,
            config_path=config_path,
            agent_id="minimax-m2-7",
            workdir=FRAMEWORK_ROOT,
            session_id="bdtc-focus-minimax",
            include_project_context=False,
        )
        transcript_parts: list[str] = []
        approval_ids: set[str] = set()
        with probe.session(rows=56, cols=180) as session:
            probe.wait_ready(session)
            for turn in turns:
                transcript_parts.append(_run_turn(probe, session, turn))
            approval_transcript, denied_approval_id = _wait_for_approval(
                probe,
                session,
                "Send the transaction you just prepared.",
                480,
                data_root,
                approval_ids,
            )
            approval_ids.add(denied_approval_id)
            transcript_parts.append(approval_transcript)
            transcript_parts.append(_reply_to_approval(probe, session, "no", 480))
            assert contract.functions.outputOf(RECIPIENT).call() == 0
            transcript_parts.append(
                _run_turn(probe, session, _scenario("turn-6", prepare_prompt))
            )
            approval_transcript, allowed_approval_id = _wait_for_approval(
                probe,
                session,
                "Call the blockchain send-transaction tool now for the newly "
                "prepared transaction. Request my approval before broadcasting it.",
                480,
                data_root,
                approval_ids,
            )
            approval_ids.add(allowed_approval_id)
            transcript_parts.append(approval_transcript)
            transcript_parts.append(_reply_to_approval(probe, session, "yes", 600))
            tool_results = _tool_results(data_root)
            send_result = next(
                item
                for item in reversed(tool_results)
                if item.get("tool_name") == "blockchain.send_transaction"
                and item.get("ok") is True
            )
            send_data = send_result["data"]["data"]
            transaction_hash = send_data["transaction_hash"]
            audits = [
                json.loads(line)
                for path in (data_root / "tool-runs").rglob("audit.jsonl")
                for line in path.read_text().splitlines()
                if line.strip()
            ]
            transaction_audits = [
                item
                for item in audits
                if item.get("event_type") == "tool.blockchain.transaction"
            ]
            assert len(transaction_audits) == 1
            assert transaction_audits[0]["transaction_hash"] == transaction_hash
            transcript_parts.append(
                _run_turn(
                    probe,
                    session,
                    _scenario(
                        "turn-9",
                        "Independently query the configured chain for receipt "
                        f"{transaction_hash} by transaction hash and report its status; "
                        "do not rely on the earlier send result. Also decode its Swap "
                        f"event with event ABI {compact(event_abi)}, and read final state from "
                        f"contract {contract.address} using "
                        f"function ABI {compact(state_abi)} and arguments "
                        f"{compact([RECIPIENT])}.",
                        720,
                    ),
                )
            )
        transcript = "\n".join(transcript_parts)
        transcript_path = EVIDENCE_ROOT / "transcript.txt"
        transcript_path.write_text(transcript)
        assert "private provider text" not in transcript
        assert contract.functions.outputOf(RECIPIENT).call() == 14
        invocation_sequence = _requested_invocations(data_root)
        required_invocations = _required_invocations(invocation_sequence)
        assert all(item["tool_call_id"] for item in invocation_sequence)
        assert len({item["tool_call_id"] for item in invocation_sequence}) == len(
            invocation_sequence
        )
        assert all(item["turn_scope_id"] for item in invocation_sequence)
        assert _turn_scope_groups(required_invocations) == EXPECTED_TURN_GROUPS

        pending_rows = _rows(
            data_root / "policy" / "policy.db", "policy_pending_confirmations"
        )
        denied = next(
            row for row in pending_rows if row["approval_id"] == denied_approval_id
        )
        allowed = next(
            row for row in pending_rows if row["approval_id"] == allowed_approval_id
        )
        assert denied["state"] == "denied"
        assert allowed["state"] == "allowed"
        tool_results = _tool_results(data_root)
        prepared_results = [
            item
            for item in tool_results
            if item.get("tool_name") == "blockchain.prepare_transaction"
        ]
        debug_results = [
            item for item in tool_results if item.get("tool_name") == "blockchain.debug"
        ]
        inspect_results = [
            item
            for item in tool_results
            if item.get("tool_name") == "blockchain.inspect"
        ]
        quote_result = next(
            item["data"]
            for item in inspect_results
            if item.get("data", {}).get("data", {}).get("function_signature")
            == "quote(uint256)"
        )
        receipt_result = next(
            item["data"]
            for item in inspect_results
            if item.get("data", {}).get("action") == "receipt"
            and item.get("data", {}).get("data", {}).get("transaction_hash")
            == transaction_hash
        )
        state_result = next(
            item["data"]
            for item in inspect_results
            if item.get("data", {}).get("data", {}).get("function_signature")
            == "outputOf(address)"
        )
        prepared = prepared_results[-1]["data"]
        revert_call_id, revert_fact = _debug_revert(data_root)
        events_result_item = next(
            item
            for item in debug_results
            if item.get("data", {}).get("action") == "transaction_events"
        )
        events_result = events_result_item["data"]
        assert quote_result["data"]["return_values"] == ["14"]
        assert receipt_result["data"]["status"] == 1
        assert state_result["data"]["return_values"] == ["14"]
        audit = transaction_audits[0]
        evidence = {
            "schema_version": "bdtc-e2e-v1",
            "source_commit": source_commit,
            "profile": "focus-minimax",
            "scenario_id": str(uuid4()),
            "debug_revert": {
                "tool_call_id": revert_call_id,
                "tool": "blockchain.debug",
                "action": "simulate_call",
                "error_code": "SIMULATION_REVERTED",
                "revert_kind": revert_fact["kind"],
                "provider_text_exposed": False,
            },
            "prepared": {
                "chain_id": str(prepared["transaction"]["chain_id"]),
                "contract_address": contract.address,
                "function_signature": prepared["call_context"]["function_signature"],
                "function_args": prepared["call_context"]["function_args"],
                "preparation_digest": prepared["preparation_digest"],
                "pre_send_state": "0",
            },
            "approval_preview": json.loads(allowed["preview_json"]),
            "denied": {
                "approval_id": denied["approval_id"],
                "broadcast_attempts": 0,
                "post_denial_state": "0",
            },
            "authorized": {
                "approval_id": allowed["approval_id"],
                "consumed_grant_id": allowed["grant_id"],
                "invocation_hash": allowed["invocation_hash"],
                "broadcast_attempts": 1,
                "transaction_hash": transaction_hash,
            },
            "verified": {
                "receipt_status": receipt_result["data"]["status"],
                "event_signature": events_result["event_signature"],
                "event_arguments": events_result["events"][0]["arguments"],
                "event_tool_call_id": events_result_item["call_id"],
                "final_state": state_result["data"]["return_values"][0],
                "transaction_hash": transaction_hash,
            },
            "telemetry": _telemetry(data_root),
            "invocation_sequence": invocation_sequence,
            "audit": {
                "prepare_transaction_event_count": 0,
                "denied_send_transaction_event_count": 0,
                "authorized_send_transaction_event_count": 1,
                "authorized_event": audit,
            },
            "transcript_path": str(transcript_path.relative_to(FRAMEWORK_ROOT)),
            "send_result": send_result,
            "quote": quote_result,
        }
        (EVIDENCE_ROOT / "evidence.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n"
        )
        return 0
    finally:
        process.terminate()
        process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
