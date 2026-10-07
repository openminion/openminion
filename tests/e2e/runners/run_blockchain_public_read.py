from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.helpers.live_e2e_profiles import resolve_live_framework_root  # noqa: E402
from tests.helpers.runtime_roots import isolate_runtime_roots  # noqa: E402

FRAMEWORK_ROOT = resolve_live_framework_root(ROOT)
RUNTIME_ROOT = isolate_runtime_roots(prefix="openminion-abo-public-read-")
EVIDENCE_ROOT = FRAMEWORK_ROOT / "workspace-tmp" / "abo-e2e" / "public-read"
OPT_IN = "OPENMINION_BLOCKCHAIN_PUBLIC_READ_E2E"
CONFIG_ENV = "OPENMINION_BLOCKCHAIN_PUBLIC_READ_CONFIG"

from openminion.base.config.env import EnvironmentConfig  # noqa: E402
from openminion.tools.blockchain.runtime import inspect_blockchain  # noqa: E402
from openminion.tools.blockchain.resolution import resolve_contract  # noqa: E402


def validate_evidence(evidence: object) -> None:
    if not isinstance(evidence, dict):
        raise ValueError("public-read evidence must be an object")
    if evidence.get("protocol") != "direct_runtime_protocol":
        raise ValueError("public-read protocol is missing")
    if not evidence.get("source_commit") or evidence.get("write_attempts") != 0:
        raise ValueError("public-read provenance or write boundary is missing")
    resolution = evidence.get("resolution", {})
    read = evidence.get("read", {})
    digest = resolution.get("data", {}).get("resolution_digest")
    if resolution.get("ok") is not True or not digest:
        raise ValueError("public resolution evidence is incomplete")
    if read.get("ok") is not True or read.get("data", {}).get("resolution_digest") != digest:
        raise ValueError("public read does not join to resolution")


def _required_config() -> tuple[Path, dict, dict]:
    raw = str(os.getenv(CONFIG_ENV, "") or "").strip()
    if not raw:
        raise RuntimeError(f"{CONFIG_ENV} is required when {OPT_IN}=1")
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"{CONFIG_ENV} does not name a readable file: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    request = payload.get("resolve_request") if isinstance(payload, dict) else None
    if not isinstance(request, dict):
        raise RuntimeError(f"{CONFIG_ENV} must contain a resolve_request object")
    read_request = payload.get("read_request")
    if not isinstance(read_request, dict):
        raise RuntimeError(f"{CONFIG_ENV} must contain a read_request object")
    return path, request, read_request


def main() -> int:
    if os.getenv(OPT_IN) != "1":
        raise RuntimeError(f"{OPT_IN}=1 is required")
    config_path, request, read_request = _required_config()
    RUNTIME_ROOT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=RUNTIME_ROOT.parent) as root:
        data_root = Path(root)
        context = SimpleNamespace(
            session_id="abo-public-read",
            env=EnvironmentConfig(
                values={
                    "OPENMINION_HOME": str(data_root),
                    "OPENMINION_DATA_ROOT": str(data_root),
                }
            ),
            metadata={"runtime_tools": {"blockchain": {"enabled": True}}},
        )
        resolution = resolve_contract(request, context)
        if resolution.get("ok") is not True:
            raise RuntimeError(f"public contract resolution failed: {resolution}")
        result = inspect_blockchain(
            {
                **read_request,
                "action": "resolved_contract_call",
                "resolution_digest": resolution["data"]["resolution_digest"],
            },
            context,
        )
        if result.get("ok") is not True:
            raise RuntimeError(f"public contract read failed: {result}")
    evidence = {
        "schema_version": "blockchain-autonomous-public-read-evidence-v1",
        "protocol": "direct_runtime_protocol",
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "config_path": str(config_path),
        "resolution": resolution,
        "read": result,
        "write_attempts": 0,
    }
    validate_evidence(evidence)
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_ROOT / "evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"ABO public read PASS evidence={EVIDENCE_ROOT / 'evidence.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
