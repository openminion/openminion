from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.helpers.live_e2e_profiles import resolve_live_framework_root  # noqa: E402
from tests.helpers.runtime_roots import (  # noqa: E402
    RUNTIME_ROOT_ENV_VARS,
    isolate_runtime_roots,
)

FRAMEWORK_ROOT = resolve_live_framework_root(ROOT)
OPT_IN = "OPENMINION_BLOCKCHAIN_PUBLIC_READ_E2E"
CONFIG_ENV = "OPENMINION_BLOCKCHAIN_PUBLIC_READ_CONFIG"


@contextmanager
def _private_runtime_root() -> Iterator[Path]:
    previous = {name: os.environ.get(name) for name in RUNTIME_ROOT_ENV_VARS}
    runtime_root = isolate_runtime_roots(prefix="openminion-abo-public-read-")
    try:
        runtime_root.parent.mkdir(parents=True, exist_ok=True)
        yield runtime_root
    finally:
        try:
            shutil.rmtree(runtime_root.parents[1])
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


with _private_runtime_root():
    from openminion.base.config.env import EnvironmentConfig  # noqa: E402
    from openminion.tools.blockchain.runtime import inspect_blockchain  # noqa: E402
    from openminion.tools.blockchain.resolution import resolve_contract  # noqa: E402


def validate_evidence(
    evidence: object, expected: dict[str, object] | None = None
) -> None:
    if not isinstance(evidence, dict):
        raise ValueError("public-read evidence must be an object")
    if (
        evidence.get("schema_version")
        != "blockchain-autonomous-public-read-evidence-v2"
    ):
        raise ValueError("public-read evidence schema is missing")
    if evidence.get("scenario_id") != "approved-public-contract-read":
        raise ValueError("public-read evidence scenario is missing")
    if evidence.get("protocol") != "direct_runtime_protocol":
        raise ValueError("public-read protocol is missing")
    if not evidence.get("source_commit") or evidence.get("write_attempts") != 0:
        raise ValueError("public-read provenance or write boundary is missing")
    resolution = evidence.get("resolution", {})
    read = evidence.get("read", {})
    digest = resolution.get("data", {}).get("resolution_digest")
    if resolution.get("ok") is not True or not digest:
        raise ValueError("public resolution evidence is incomplete")
    if (
        read.get("ok") is not True
        or read.get("data", {}).get("resolution_digest") != digest
    ):
        raise ValueError("public read does not join to resolution")
    if expected is not None:
        for key, value in expected.items():
            if evidence.get(key) != value:
                raise ValueError(f"public-read evidence {key} does not match approval")
    elapsed = evidence.get("elapsed_seconds")
    maximum = evidence.get("max_duration_seconds")
    if not isinstance(elapsed, (int, float)) or not isinstance(maximum, int):
        raise ValueError("public-read duration evidence is missing")
    if elapsed > maximum:
        raise ValueError("public-read duration exceeded the approved maximum")
    if evidence.get("broadcast_count") != 0:
        raise ValueError("public-read evidence contains a broadcast")
    if evidence.get("terminal_result") != "completed":
        raise ValueError("public-read terminal result is incomplete")


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


def _required(name: str) -> str:
    value = str(os.getenv(name, "") or "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_commit() -> str:
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    if commit != _required("OPENMINION_BLOCKCHAIN_E2E_EXPECTED_SOURCE_COMMIT"):
        raise RuntimeError("current source commit does not match Stage C approval")
    return commit


def _evidence_root() -> Path:
    approved = (
        Path(_required("OPENMINION_BLOCKCHAIN_E2E_APPROVED_ROOT"))
        .expanduser()
        .resolve()
    )
    root = (
        Path(_required("OPENMINION_BLOCKCHAIN_E2E_EVIDENCE_ROOT"))
        .expanduser()
        .resolve()
    )
    if not approved.is_dir():
        raise RuntimeError("approved evidence root does not exist")
    try:
        root.relative_to(approved)
    except ValueError as exc:
        raise RuntimeError("evidence root is outside the approved root") from exc
    if root.exists():
        raise RuntimeError("evidence root must be fresh")
    root.mkdir(mode=0o700)
    return root


def _stage_c_inputs(config_path: Path, request: dict) -> dict[str, object]:
    config_sha256 = _file_sha256(config_path)
    if config_sha256 != _required("OPENMINION_BLOCKCHAIN_E2E_EXPECTED_CONFIG_SHA256"):
        raise RuntimeError("public-read config fingerprint does not match approval")
    maximum = int(_required("OPENMINION_BLOCKCHAIN_E2E_MAX_DURATION_SECONDS"))
    if maximum <= 0:
        raise RuntimeError("maximum duration must be positive")
    chain_id = int(_required("OPENMINION_BLOCKCHAIN_E2E_EXPECTED_CHAIN_ID"))
    if int(request.get("expected_chain_id", 0)) != chain_id:
        raise RuntimeError("public-read chain ID does not match approval")
    rpc_url = str(request.get("rpc_url", ""))
    parsed_rpc = urlsplit(rpc_url)
    origin = f"{parsed_rpc.scheme}://{parsed_rpc.netloc}"
    if origin != _required("OPENMINION_BLOCKCHAIN_E2E_EXPECTED_RPC_ORIGIN"):
        raise RuntimeError("public-read RPC origin does not match approval")
    if parsed_rpc.username or parsed_rpc.password:
        raise RuntimeError("public-read RPC URL must not contain credentials")
    if _required("OPENMINION_BLOCKCHAIN_E2E_EXPECTED_MAX_BROADCASTS") != "0":
        raise RuntimeError("public-read proof requires a zero-broadcast approval")
    return {
        "focus_agent_id": _required(
            "OPENMINION_BLOCKCHAIN_E2E_EXPECTED_FOCUS_AGENT_ID"
        ),
        "config_sha256": config_sha256,
        "focus_config_sha256": _required(
            "OPENMINION_BLOCKCHAIN_E2E_EXPECTED_FOCUS_CONFIG_SHA256"
        ),
        "expected_chain_id": chain_id,
        "expected_rpc_origin": origin,
        "max_duration_seconds": maximum,
        "max_broadcasts": 0,
    }


@contextmanager
def _deadline(seconds: int):
    if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
        raise RuntimeError("public-read deadline is unavailable on this platform")

    def expired(_signum: int, _frame: object) -> None:
        raise TimeoutError("public-read evidence exceeded the approved duration")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def main() -> int:
    if os.getenv(OPT_IN) != "1":
        raise RuntimeError(f"{OPT_IN}=1 is required")
    config_path, request, read_request = _required_config()
    approved = _stage_c_inputs(config_path, request)
    source_commit = _source_commit()
    evidence_root = _evidence_root()
    started = time.monotonic()
    with _deadline(int(approved["max_duration_seconds"])):
        with _private_runtime_root() as runtime_root:
            with tempfile.TemporaryDirectory(
                prefix="run-", dir=runtime_root.parent
            ) as root:
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
                    raise RuntimeError(
                        f"public contract resolution failed: {resolution}"
                    )
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
    elapsed_seconds = round(time.monotonic() - started, 3)
    evidence = {
        "schema_version": "blockchain-autonomous-public-read-evidence-v2",
        "scenario_id": "approved-public-contract-read",
        "protocol": "direct_runtime_protocol",
        "source_commit": source_commit,
        "resolution": resolution,
        "read": result,
        "write_attempts": 0,
        "broadcast_count": 0,
        "elapsed_seconds": elapsed_seconds,
        "terminal_result": "completed",
        **approved,
    }
    validate_evidence(evidence, {"source_commit": source_commit, **approved})
    (evidence_root / "evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"ABO public read PASS evidence={evidence_root / 'evidence.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
