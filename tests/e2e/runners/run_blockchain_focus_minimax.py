from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time

from cryptography.fernet import Fernet
from web3 import Web3

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.helpers.live_e2e_profiles import resolve_live_framework_root  # noqa: E402
from tests.helpers.runtime_roots import isolate_runtime_roots  # noqa: E402

FRAMEWORK_ROOT = resolve_live_framework_root(ROOT)
RUNTIME_GENERATED_ROOT = isolate_runtime_roots(prefix="openminion-bttl-focus-")

from openminion.modules.secret.service import SecretService  # noqa: E402
from tests.e2e.cli.focus.harness import FocusProbe  # noqa: E402
from tests.e2e.cli.focus.harness.scenarios import FocusScenario  # noqa: E402

ARTIFACT_ROOT = FRAMEWORK_ROOT / "workspace-tmp" / "bttl-e2e" / "focus"
CONFIG_SOURCE = FRAMEWORK_ROOT / "test-configs" / "per-agent-minimax-official.json"
RPC_URL = ""
CHAIN_ID = 31337
PRIVATE_KEY = "0x" + "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
RECIPIENT = Web3.to_checksum_address("0x" + "33" * 20)


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


def _write_config(data_root: Path) -> tuple[Path, set[bytes]]:
    payload = json.loads(CONFIG_SOURCE.read_text(encoding="utf-8"))
    secret_values = {
        str(value).encode()
        for key, value in payload.get("runtime", {}).get("env", {}).items()
        if any(marker in str(key).lower() for marker in ("key", "token", "secret"))
        and len(str(value)) >= 8
    }
    payload["action_policy"] = {
        "mode": "ask",
        "default_action": "require_confirm",
        "allow_read_only_without_prompt": True,
        "rules": [],
    }
    runtime = payload.setdefault("runtime", {})
    runtime.setdefault("tools", {})["blockchain"] = {
        "enabled": True,
        "rpc_url": RPC_URL,
        "chain_id": CHAIN_ID,
        "signer_secret_key": "focus-anvil-signer",
        "signer_secret_namespace": "blockchain",
        "writes_enabled": True,
        "max_total_fee_wei": "10000000000000000",
        "receipt_timeout_seconds": 10,
    }
    path = data_root / "config.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    path.chmod(0o600)
    return path, secret_values


def _assert_no_retained_credentials(secrets: set[bytes]) -> None:
    for path in ARTIFACT_ROOT.rglob("*"):
        if path.is_file() and any(secret in path.read_bytes() for secret in secrets):
            raise RuntimeError("retained BTTL evidence contains provider credentials")


def _json_rows(database: Path, table: str) -> list[dict]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
    finally:
        connection.close()


def _session_status(data_root: Path, scenario_id: str) -> str:
    session_id = (
        f"bttl-focus-minimax-{scenario_id}::conv:focus-bttl-focus-minimax-{scenario_id}"
    )
    rows = _json_rows(data_root / "state" / "brain" / "sessions.db", "sessions")
    return str(
        next(row for row in rows if row.get("session_id") == session_id).get("status")
        or ""
    )


def _session_tool_results(data_root: Path) -> list[dict]:
    results: list[dict] = []
    for database in data_root.rglob("*.db"):
        try:
            rows = _json_rows(database, "messages")
        except sqlite3.Error:
            continue
        for row in rows:
            try:
                metadata = json.loads(row.get("metadata_json") or "{}")
                tool_results = json.loads(metadata.get("tool_results") or "[]")
            except (json.JSONDecodeError, TypeError):
                continue
            for result in tool_results:
                if str(result.get("tool_name", "")).startswith("blockchain."):
                    results.append({**result, "session_id": row.get("session_id")})
    return results


def _telemetry_tool_results(data_root: Path) -> list[dict]:
    database = data_root / "telemetry" / "telemetry.db"
    if not database.exists():
        return []
    results: list[dict] = []
    for row in _json_rows(database, "events"):
        if row.get("event_type") != "tool.completed":
            continue
        try:
            data = json.loads(row.get("data") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        tool_name = str(data.get("tool_name", ""))
        if not tool_name.startswith("blockchain."):
            continue
        summary = data.get("summary")
        try:
            output = json.loads(summary) if isinstance(summary, str) else summary
        except json.JSONDecodeError:
            output = summary
        results.append(
            {
                "tool_name": tool_name,
                "status": data.get("status"),
                "data": output,
                "session_id": row.get("session_id"),
            }
        )
    return results


def main() -> int:
    global RPC_URL
    if os.getenv("OPENMINION_LIVE_CLI_FOCUS_E2E") != "1":
        raise RuntimeError("OPENMINION_LIVE_CLI_FOCUS_E2E=1 is required")
    anvil = shutil.which("anvil")
    if not anvil:
        raise RuntimeError("anvil is required")
    if ARTIFACT_ROOT.exists():
        shutil.rmtree(ARTIFACT_ROOT)
    ARTIFACT_ROOT.mkdir(parents=True)
    data_root = RUNTIME_GENERATED_ROOT.parent
    data_root.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    RPC_URL = f"http://127.0.0.1:{port}"
    master_key = Fernet.generate_key().decode()
    config_path, config_secrets = _write_config(data_root)
    secret = SecretService(
        str(data_root / "secret" / "secrets.db"),
        master_key,
    )
    asyncio.run(
        secret.set_secret(
            "focus-anvil-signer",
            PRIVATE_KEY,
            namespace="blockchain",
        )
    )
    secret.close_sync()
    os.environ["OPENMINION_SECRET_KEY"] = master_key
    os.environ["OPENMINION_TRACE_REQUESTS"] = "1"
    process = subprocess.Popen(
        [anvil, "--port", str(port), "--chain-id", str(CHAIN_ID), "--silent"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        web3 = Web3(Web3.HTTPProvider(RPC_URL))
        _wait_rpc(web3)
        balance_before = web3.eth.get_balance(RECIPIENT)
        balance_after_deny: int | None = None
        scenarios = (
            FocusScenario(
                scenario_id="inspect",
                prompt=(
                    "What is the latest block and chain ID on the configured "
                    "Ethereum blockchain? Use the blockchain inspection tool."
                ),
                expected_markers=(),
                timeout=360,
            ),
            FocusScenario(
                scenario_id="prepare",
                prompt=(
                    f"Prepare and simulate, but do not send, 1 wei to {RECIPIENT} "
                    "on the configured EVM blockchain."
                ),
                expected_markers=(),
                timeout=360,
            ),
            FocusScenario(
                scenario_id="deny",
                prompt=(
                    f"Prepare a fresh 1 wei transfer to {RECIPIENT}, then send it "
                    "with the canonical blockchain tools and wait for approval. "
                    "Use the preparation digest returned by the prepare step."
                ),
                expected_markers=(),
                requires_approval=True,
                approval_reply="no",
                timeout=360,
            ),
            FocusScenario(
                scenario_id="approve",
                prompt=(
                    f"Prepare a fresh 1 wei transfer to {RECIPIENT}, then send it "
                    "with the canonical blockchain tools and wait for approval. "
                    "Use the preparation digest returned by the prepare step."
                ),
                expected_markers=(),
                requires_approval=True,
                approval_reply="yes",
                timeout=480,
            ),
        )
        transcripts: dict[str, str] = {}
        for scenario in scenarios:
            probe = FocusProbe(
                python_bin=Path(sys.executable),
                openminion_root=ROOT,
                framework_root=FRAMEWORK_ROOT,
                data_root=data_root,
                config_path=config_path,
                agent_id="minimax-m2-7",
                workdir=FRAMEWORK_ROOT,
                session_id=f"bttl-focus-minimax-{scenario.scenario_id}",
                include_project_context=False,
            )
            with probe.session(rows=48, cols=160) as session:
                probe.wait_ready(session)
                transcript = probe.run_turn(session, scenario)
                transcripts[scenario.scenario_id] = transcript
                (ARTIFACT_ROOT / f"{scenario.scenario_id}.txt").write_text(
                    transcript,
                    encoding="utf-8",
                )
                if scenario.scenario_id == "deny":
                    balance_after_deny = web3.eth.get_balance(RECIPIENT)
                    assert balance_after_deny == balance_before

        balance_after = web3.eth.get_balance(RECIPIENT)
        assert balance_after == balance_before + 1
        assert _session_status(data_root, "inspect") == "done"
        assert _session_status(data_root, "prepare") == "done"
        assert _session_status(data_root, "deny") == "stopped"
        assert _session_status(data_root, "approve") == "done"
        assert "could not safely determine the next step" not in transcripts["approve"]
        policy_rows = _json_rows(data_root / "policy" / "policy.db", "policy_decisions")
        blockchain_policy_rows = [
            row for row in policy_rows if row.get("tool") == "blockchain"
        ]
        send_policy_rows = [
            row
            for row in blockchain_policy_rows
            if row.get("method") == "send_transaction"
        ]
        pending_rows = _json_rows(
            data_root / "policy" / "policy.db",
            "policy_pending_confirmations",
        )
        denied_confirmation = next(
            row for row in pending_rows if "-deny::" in str(row.get("session_id"))
        )
        allowed_confirmation = next(
            row for row in pending_rows if "-approve::" in str(row.get("session_id"))
        )
        audit_files = sorted((data_root / "tool-runs").rglob("audit.jsonl"))
        audits = [
            json.loads(line)
            for path in audit_files
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        transaction_audits = [
            item
            for item in audits
            if item.get("event_type") == "tool.blockchain.transaction"
        ]
        tool_results = [
            *_session_tool_results(data_root),
            *_telemetry_tool_results(data_root),
        ]
        assert blockchain_policy_rows
        assert len(transaction_audits) == 1
        assert denied_confirmation["state"] == "denied"
        assert denied_confirmation["resolution_action"] == "deny"
        assert allowed_confirmation["state"] == "allowed"
        assert allowed_confirmation["resolution_action"] == "allow_once"
        assert any(
            item.get("tool_name") == "blockchain.inspect" for item in tool_results
        )
        assert any(
            item.get("tool_name") == "blockchain.prepare_transaction"
            for item in tool_results
        )
        approved_tool_result = next(
            item
            for item in reversed(tool_results)
            if item.get("tool_name") == "blockchain.send_transaction"
            and "-approve" in str(item.get("session_id"))
        )
        approved_policy_decision = next(
            row
            for row in reversed(send_policy_rows)
            if "-approve::" in str(row.get("session_id"))
            and row.get("decision") == "allow"
        )
        denied_policy_decision = next(
            row for row in send_policy_rows if "-deny::" in str(row.get("session_id"))
        )
        trace_root = ARTIFACT_ROOT / "traces"
        shutil.copytree(data_root / "traces", trace_root, dirs_exist_ok=True)
        evidence = {
            "schema_version": "bttl-e2e-v2",
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "scenario_id": "configured-focus-minimax",
            "protocol": "general_loop_focus_cli",
            "terminal_result": "completed",
            "provider_request": {
                "trace_files": [
                    str(path.relative_to(ARTIFACT_ROOT))
                    for path in trace_root.rglob("*-http*.json")
                ]
            },
            "policy_decision": approved_policy_decision,
            "execution_authorization": {
                "invocation_hash": transaction_audits[-1]["invocation_hash"],
                "approval_id": transaction_audits[-1]["approval_id"],
                "grant_id": transaction_audits[-1]["consumed_grant_id"],
                "duration_type": transaction_audits[-1]["duration_type"],
            },
            "tool_result": approved_tool_result,
            "transaction_audit": transaction_audits[-1],
            "chain_state": {
                "chain_id": web3.eth.chain_id,
                "recipient_balance_before": str(balance_before),
                "recipient_balance_after": str(balance_after),
            },
            "denied_send": {
                "policy_decision": denied_policy_decision,
                "pending_confirmation": denied_confirmation,
                "execution_authorization": None,
                "transaction_audit": None,
                "chain_state_unchanged": balance_after_deny == balance_before,
            },
        }
        output = ARTIFACT_ROOT / "evidence.json"
        output.write_text(json.dumps(evidence, indent=2, sort_keys=True))
        _assert_no_retained_credentials(config_secrets)
        print(f"BTTL Focus MiniMax PASS evidence={output}")
        return 0
    finally:
        process.terminate()
        process.wait(timeout=5)
        shutil.rmtree(data_root.parent, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
