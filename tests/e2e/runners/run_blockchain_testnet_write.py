from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.helpers.live_e2e_profiles import resolve_live_framework_root  # noqa: E402
from tests.helpers.runtime_roots import isolate_runtime_roots  # noqa: E402

FRAMEWORK_ROOT = resolve_live_framework_root(ROOT)
RUNTIME_ROOT = isolate_runtime_roots(prefix="openminion-abo-testnet-write-")
EVIDENCE_ROOT = FRAMEWORK_ROOT / "workspace-tmp" / "abo-e2e" / "testnet-write"
OPT_IN = "OPENMINION_BLOCKCHAIN_TESTNET_WRITE_E2E"
CONFIG_ENV = "OPENMINION_BLOCKCHAIN_TESTNET_WRITE_CONFIG"
TIMEOUT_ENV = "OPENMINION_BLOCKCHAIN_TESTNET_STATUS_TIMEOUT_SECONDS"

from openminion.base.config.env import EnvironmentConfig  # noqa: E402
from openminion.tools.blockchain import runtime as blockchain_runtime  # noqa: E402
from openminion.tools.blockchain import resolved_operations  # noqa: E402
from openminion.tools.blockchain.resolution import resolve_contract  # noqa: E402


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
        raise RuntimeError(
            f"{CONFIG_ENV} network_class must be disposable_testnet"
        )
    for field in ("resolve_request", "prepare_request", "policy_authorization"):
        if not isinstance(payload.get(field), dict):
            raise RuntimeError(f"{CONFIG_ENV} must contain a {field} object")
    signer_secret_env = str(payload.get("signer_secret_env", "") or "").strip()
    if not signer_secret_env:
        raise RuntimeError(f"{CONFIG_ENV} must name signer_secret_env")
    if not str(os.getenv(signer_secret_env, "") or "").strip():
        raise RuntimeError(
            f"{signer_secret_env} is required by {CONFIG_ENV} when {OPT_IN}=1"
        )
    authorization = payload["policy_authorization"]
    required_authorization = (
        "invocation_hash",
        "approval_id",
        "grant_id",
        "duration_type",
    )
    if any(not str(authorization.get(key, "") or "") for key in required_authorization):
        raise RuntimeError(
            f"{CONFIG_ENV} policy_authorization must contain "
            + ", ".join(required_authorization)
        )
    return path, payload, signer_secret_env


def _terminal_status(
    preparation_digest: str, context: object, *, timeout_seconds: float
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while True:
        status = blockchain_runtime.inspect_blockchain(
            {
                "action": "operation_status",
                "preparation_digest": preparation_digest,
            },
            context,
        )
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
    if evidence.get("protocol") != "direct_runtime_protocol":
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
    if send.get("ok") is not True or send.get("data", {}).get("preparation_digest") != preparation_digest:
        raise ValueError("testnet send does not join to preparation")
    if send.get("data", {}).get("broadcast_attempts") != 1:
        raise ValueError("testnet send must prove exactly one broadcast attempt")
    for key in ("operation_status", "restart_status"):
        status = evidence.get(key, {})
        if status.get("ok") is not True or status.get("data", {}).get("state") != "succeeded":
            raise ValueError(f"{key} is not succeeded")
        if status.get("data", {}).get("preparation_digest") != preparation_digest:
            raise ValueError(f"{key} does not join to preparation")
        postconditions = status.get("data", {}).get("postcondition_results", ())
        if not postconditions or any(item.get("matched") is not True for item in postconditions):
            raise ValueError(f"{key} lacks matched postcondition evidence")
    audit_events = evidence.get("audit_events")
    if not isinstance(audit_events, list) or len(audit_events) != 1:
        raise ValueError("testnet exact mutation audit evidence is missing")


def main() -> int:
    if os.getenv(OPT_IN) != "1":
        raise RuntimeError(f"{OPT_IN}=1 is required")
    config_path, payload, signer_secret_env = _required_config()
    signer_secret = os.environ[signer_secret_env]
    authorization = payload["policy_authorization"]
    audit_events: list[dict] = []
    RUNTIME_ROOT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=RUNTIME_ROOT.parent) as root:
        data_root = Path(root)
        env = EnvironmentConfig(
            values={
                "OPENMINION_HOME": str(data_root),
                "OPENMINION_DATA_ROOT": str(data_root),
            }
        )
        metadata = {
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
        context = SimpleNamespace(
            session_id="abo-disposable-testnet-write",
            env=env,
            metadata=metadata,
            secret_service=SimpleNamespace(
                get_secret_sync=lambda _key, *, namespace: signer_secret
            ),
            policy_authorization=SimpleNamespace(**authorization),
            invocation_id="abo-disposable-testnet-send",
            write_audit_event=lambda event: audit_events.append(event) or True,
        )
        resolution = resolve_contract(payload["resolve_request"], context)
        if resolution.get("ok") is not True:
            raise RuntimeError(f"testnet contract resolution failed: {resolution}")
        preparation = blockchain_runtime.prepare_transaction(
            {
                **payload["prepare_request"],
                "kind": "resolved_contract_call",
                "resolution_digest": resolution["data"]["resolution_digest"],
            },
            context,
        )
        if preparation.get("ok") is not True:
            raise RuntimeError(f"testnet transaction preparation failed: {preparation}")
        prepared = resolved_operations._load_resolved_preparation(
            preparation["preparation_digest"], context
        )
        sent = blockchain_runtime.send_transaction(prepared, context)
        if sent.get("ok") is not True:
            raise RuntimeError(f"testnet transaction submission failed: {sent}")
        timeout_seconds = float(os.getenv(TIMEOUT_ENV, "120"))
        if not 1 <= timeout_seconds <= 900:
            raise RuntimeError(f"{TIMEOUT_ENV} must be between 1 and 900 seconds")
        status = _terminal_status(
            preparation["preparation_digest"],
            context,
            timeout_seconds=timeout_seconds,
        )
        if status.get("data", {}).get("state") != "succeeded":
            raise RuntimeError(f"testnet operation did not succeed: {status}")
        restart_context = SimpleNamespace(
            session_id=context.session_id,
            env=env,
            metadata=metadata,
        )
        restart_status = blockchain_runtime.inspect_blockchain(
            {
                "action": "operation_status",
                "preparation_digest": preparation["preparation_digest"],
            },
            restart_context,
        )
        if restart_status.get("ok") is not True or restart_status.get("data", {}).get("state") != "succeeded":
            raise RuntimeError(
                f"testnet restart reconstruction failed: {restart_status}"
            )
        evidence = {
            "schema_version": "blockchain-autonomous-testnet-write-evidence-v1",
            "protocol": "direct_runtime_protocol",
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
            "audit_events": audit_events,
            "confirmation_depth": 1,
        }
        validate_evidence(evidence)
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_ROOT / "evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"ABO testnet write PASS evidence={EVIDENCE_ROOT / 'evidence.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
