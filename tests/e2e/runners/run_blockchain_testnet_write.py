from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.helpers.live_e2e_profiles import resolve_live_framework_root  # noqa: E402
from tests.helpers.runtime_roots import (  # noqa: E402
    configure_runtime_roots,
    isolate_runtime_roots,
)
from tests.e2e.runners.run_blockchain_autonomous_local import (  # noqa: E402
    ProductionBlockchainExecution,
    build_blockchain_policy,
)

FRAMEWORK_ROOT = resolve_live_framework_root(ROOT)
RUNTIME_ROOT = isolate_runtime_roots(prefix="openminion-abo-testnet-write-")
EVIDENCE_ROOT = FRAMEWORK_ROOT / "workspace-tmp" / "abo-e2e" / "testnet-write"
OPT_IN = "OPENMINION_BLOCKCHAIN_TESTNET_WRITE_E2E"
CONFIG_ENV = "OPENMINION_BLOCKCHAIN_TESTNET_WRITE_CONFIG"
TIMEOUT_ENV = "OPENMINION_BLOCKCHAIN_TESTNET_STATUS_TIMEOUT_SECONDS"

from openminion.base.config.env import EnvironmentConfig  # noqa: E402
from openminion.base.config.runtime.tools import (  # noqa: E402
    BlockchainToolRuntimeConfig,
    ToolRuntimeConfig,
)
from openminion.modules.brain.adapters.tool.runtime import ToolAdapter  # noqa: E402
from openminion.modules.brain.schemas import BudgetCounters, WorkingState  # noqa: E402
from openminion.modules.brain.schemas.commands import ToolCommand  # noqa: E402
from openminion.modules.policy.models import PolicyConfig, RiskSpec  # noqa: E402
from openminion.modules.policy.runtime.service import PolicyCtl  # noqa: E402
from openminion.modules.tool.registry import ToolRegistry  # noqa: E402
from openminion.tools.blockchain.public_https import (  # noqa: E402
    request_public_https,
)
from openminion.tools.blockchain import runtime as blockchain_runtime  # noqa: E402
from openminion.tools.blockchain import plugin as blockchain_plugin  # noqa: E402


def _required_config() -> tuple[Path, dict, str]:
    raw = str(os.getenv(CONFIG_ENV, "") or "").strip()
    if not raw:
        raise RuntimeError(f"{CONFIG_ENV} is required when {OPT_IN}=1")
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"{CONFIG_ENV} does not name a readable file: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{CONFIG_ENV} must contain an object")
    if payload.get("network_class") != "disposable_testnet":
        raise RuntimeError(f"{CONFIG_ENV} network_class must be disposable_testnet")
    for field in ("resolve_request", "prepare_request"):
        if not isinstance(payload.get(field), dict):
            raise RuntimeError(f"{CONFIG_ENV} must contain a {field} object")
    signer_secret_env = str(payload.get("signer_secret_env", "") or "").strip()
    if not signer_secret_env:
        raise RuntimeError(f"{CONFIG_ENV} must name signer_secret_env")
    if not str(os.getenv(signer_secret_env, "") or "").strip():
        raise RuntimeError(
            f"{signer_secret_env} is required by {CONFIG_ENV} when {OPT_IN}=1"
        )
    return path, payload, signer_secret_env


def _terminal_status(
    preparation_digest: str,
    execution: ProductionBlockchainExecution,
    *,
    session_id: str,
    timeout_seconds: float,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    attempt = 0
    while True:
        attempt += 1
        status = execution.execute_raw(
            name="blockchain.inspect",
            arguments={
                "action": "operation_status",
                "preparation_digest": preparation_digest,
            },
            session_id=session_id,
            trace_id="abo-disposable-testnet-status",
            invocation_id=f"abo-disposable-testnet-status-{attempt}",
        )["outputs"]
        if status.get("ok") is not True:
            raise RuntimeError(f"testnet operation status failed: {status}")
        if status.get("data", {}).get("state") in {
            "succeeded",
            "reverted",
            "postcondition_failed",
            "reorged",
        }:
            return status
        if time.monotonic() >= deadline:
            raise RuntimeError(f"testnet operation did not become terminal: {status}")
        time.sleep(2)


def validate_evidence(evidence: object) -> None:
    if not isinstance(evidence, dict):
        raise ValueError("testnet evidence must be an object")
    if evidence.get("protocol") != "production_policy_runtime_protocol":
        raise ValueError("testnet protocol is missing")
    if not evidence.get("source_commit") or evidence.get("confirmation_depth") != 1:
        raise ValueError("testnet provenance or confirmation depth is missing")
    resolution = evidence.get("resolution", {})
    preparation = evidence.get("preparation", {})
    digest = resolution.get("data", {}).get("resolution_digest")
    preparation_digest = preparation.get("preparation_digest")
    if not digest or preparation.get("resolution_digest") != digest:
        raise ValueError("testnet preparation does not join to resolution")
    send = evidence.get("send", {})
    if (
        send.get("ok") is not True
        or send.get("data", {}).get("preparation_digest") != preparation_digest
    ):
        raise ValueError("testnet send does not join to preparation")
    if send.get("data", {}).get("broadcast_attempts") != 1:
        raise ValueError("testnet send must prove exactly one broadcast attempt")
    for key in ("operation_status", "restart_status"):
        status = evidence.get(key, {})
        if (
            status.get("ok") is not True
            or status.get("data", {}).get("state") != "succeeded"
        ):
            raise ValueError(f"{key} is not succeeded")
        if status.get("data", {}).get("preparation_digest") != preparation_digest:
            raise ValueError(f"{key} does not join to preparation")
        postconditions = status.get("data", {}).get("postcondition_results", ())
        if not postconditions or any(
            item.get("matched") is not True for item in postconditions
        ):
            raise ValueError(f"{key} lacks matched postcondition evidence")
    audit_events = evidence.get("audit_events")
    if not isinstance(audit_events, list) or len(audit_events) != 1:
        raise ValueError("testnet exact mutation audit evidence is missing")
    approval = evidence.get("policy_approval", {})
    if approval.get("action") != "allow_once" or not approval.get("grant_id"):
        raise ValueError("testnet one-time policy approval evidence is missing")
    if approval.get("preview", {}).get("preparation_digest") != preparation_digest:
        raise ValueError("testnet approval does not bind the exact preparation")
    if evidence.get("execution_owners") != {
        "command_executor": "RunnerCommandExecutor",
        "tool_adapter": "ToolAdapter",
        "policy_adapter": "PolicyCtlBrainAdapter",
        "send_scope": "POWER_USER",
    }:
        raise ValueError("testnet production execution owners are missing")


def _metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "runtime_tools": {
            "blockchain": {
                "enabled": True,
                "writes_enabled": True,
                "signer_secret_key": "abo-testnet-signer",
                "signer_secret_namespace": "blockchain",
                "max_total_fee_wei": str(
                    payload.get("max_total_fee_wei", "10000000000000000")
                ),
                "confirmation_depth": 1,
            }
        }
    }


def _execution(
    data_root: Path,
    metadata: dict[str, Any],
    signer_secret: str,
) -> ProductionBlockchainExecution:
    registry = ToolRegistry()
    blockchain_plugin.register(registry)
    policy_ctl = PolicyCtl.with_sqlite(
        data_root / "policy.sqlite",
        config=PolicyConfig(mode="enforce"),
    )
    policy_ctl.register_risk(
        "blockchain.send_transaction",
        RiskSpec(
            risk_class="financial",
            side_effects="external_account",
            reversibility="irreversible",
            default_confirm=True,
        ),
    )
    blockchain = metadata["runtime_tools"]["blockchain"]
    adapter = ToolAdapter(
        workspace_root=data_root,
        runtime_config=SimpleNamespace(
            tools=ToolRuntimeConfig(
                blockchain=BlockchainToolRuntimeConfig(**blockchain)
            )
        ),
        runtime_registry=registry,
        policy=build_blockchain_policy(data_root, metadata),
        policy_ctl=policy_ctl,
        secret_service=SimpleNamespace(
            get_secret_sync=lambda _key, *, namespace: signer_secret,
            close_sync=lambda: None,
        ),
        agent_id="abo-testnet-agent",
    )
    return ProductionBlockchainExecution(
        adapter=adapter,
        policy_ctl=policy_ctl,
        transport=request_public_https,
        approval_action="allow_once",
    )


def _run_lifecycle(
    execution: ProductionBlockchainExecution,
    payload: dict[str, Any],
    *,
    session_id: str,
) -> tuple[dict, dict, dict, dict, dict | None]:
    trace_id = "abo-disposable-testnet-write"
    resolution = execution.execute_raw(
        name="blockchain.resolve_contract",
        arguments=payload["resolve_request"],
        session_id=session_id,
        trace_id=trace_id,
        invocation_id="abo-disposable-testnet-resolve",
    )["outputs"]
    if resolution.get("ok") is not True:
        raise RuntimeError(f"testnet contract resolution failed: {resolution}")
    preparation = execution.execute_raw(
        name="blockchain.prepare_transaction",
        arguments={
            **payload["prepare_request"],
            "kind": "resolved_contract_call",
            "resolution_digest": resolution["data"]["resolution_digest"],
        },
        session_id=session_id,
        trace_id=trace_id,
        invocation_id="abo-disposable-testnet-prepare",
    )["outputs"]
    if preparation.get("ok") is not True:
        raise RuntimeError(f"testnet transaction preparation failed: {preparation}")
    state = WorkingState(
        session_id=session_id,
        agent_id="abo-testnet-agent",
        trace_id=trace_id,
        goal="Submit the prepared disposable testnet transaction once.",
        budgets_remaining=BudgetCounters(
            ticks=4,
            tool_calls=4,
            a2a_calls=0,
            tokens=4_000,
            time_ms=30_000,
        ),
    )
    outcome = execution.execute_command(
        state=state,
        command=ToolCommand(
            kind="tool",
            title="blockchain.send_transaction",
            tool_name="blockchain.send_transaction",
            args={"preparation_digest": preparation["preparation_digest"]},
            inputs={},
            idempotency_key="abo-disposable-testnet-send",
        ),
        logger=SimpleNamespace(emit=lambda *_args, **_kwargs: None),
        include_reflect=False,
    )
    sent = dict(outcome.action_result.outputs)
    if sent.get("ok") is not True:
        raise RuntimeError(f"testnet transaction submission failed: {sent}")
    timeout_seconds = float(os.getenv(TIMEOUT_ENV, "120"))
    if not 1 <= timeout_seconds <= 900:
        raise RuntimeError(f"{TIMEOUT_ENV} must be between 1 and 900 seconds")
    status = _terminal_status(
        preparation["preparation_digest"],
        execution,
        session_id=session_id,
        timeout_seconds=timeout_seconds,
    )
    if status.get("data", {}).get("state") != "succeeded":
        raise RuntimeError(f"testnet operation did not succeed: {status}")
    return resolution, preparation, sent, status, execution.approval


def _read_audit_events(data_root: Path, preparation_digest: str) -> list[dict]:
    events: list[dict] = []
    for path in sorted((data_root / "tool-runs").rglob("audit.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("preparation_digest") == preparation_digest:
                events.append(event)
    return events


def _run(config_path: Path, payload: dict[str, Any], signer_secret: str) -> dict:
    session_id = "abo-disposable-testnet-write"
    with tempfile.TemporaryDirectory(prefix="run-", dir=RUNTIME_ROOT.parent) as root:
        data_root = Path(root)
        runtime_data_root = configure_runtime_roots(data_root).parent
        env = EnvironmentConfig(
            values={
                "OPENMINION_HOME": str(data_root),
                "OPENMINION_DATA_ROOT": str(runtime_data_root),
            }
        )
        metadata = _metadata(payload)
        execution = _execution(data_root, metadata, signer_secret)
        try:
            resolution, preparation, sent, status, approval = _run_lifecycle(
                execution, payload, session_id=session_id
            )
        finally:
            execution.close()
        restart_status = blockchain_runtime.inspect_blockchain(
            {
                "action": "operation_status",
                "preparation_digest": preparation["preparation_digest"],
            },
            SimpleNamespace(session_id=session_id, env=env, metadata=metadata),
        )
        if (
            restart_status.get("ok") is not True
            or restart_status.get("data", {}).get("state") != "succeeded"
        ):
            raise RuntimeError(
                f"testnet restart reconstruction failed: {restart_status}"
            )
        evidence = {
            "schema_version": "blockchain-autonomous-testnet-write-evidence-v1",
            "protocol": "production_policy_runtime_protocol",
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "network_class": "disposable_testnet",
            "config_path": str(config_path),
            "resolution": resolution,
            "preparation": preparation,
            "send": sent,
            "operation_status": status,
            "restart_status": restart_status,
            "audit_events": _read_audit_events(
                runtime_data_root, preparation["preparation_digest"]
            ),
            "policy_approval": approval,
            "execution_owners": {
                "command_executor": "RunnerCommandExecutor",
                "tool_adapter": "ToolAdapter",
                "policy_adapter": "PolicyCtlBrainAdapter",
                "send_scope": "POWER_USER",
            },
            "confirmation_depth": 1,
        }
        validate_evidence(evidence)
        return evidence


def main() -> int:
    if os.getenv(OPT_IN) != "1":
        raise RuntimeError(f"{OPT_IN}=1 is required")
    config_path, payload, signer_secret_env = _required_config()
    RUNTIME_ROOT.parent.mkdir(parents=True, exist_ok=True)
    evidence = _run(config_path, payload, os.environ[signer_secret_env])
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_ROOT / "evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"ABO testnet write PASS evidence={EVIDENCE_ROOT / 'evidence.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
