from __future__ import annotations

import atexit
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.e2e.cli.focus.harness import FocusProbe  # noqa: E402
from tests.e2e.cli.focus.harness.assertions import read_focus_evidence  # noqa: E402
from tests.e2e.cli.focus.harness.scenarios import FocusScenario  # noqa: E402
from tests.helpers.live_e2e_profiles import resolve_live_framework_root  # noqa: E402
from tests.helpers.runtime_roots import isolate_runtime_roots  # noqa: E402

FRAMEWORK_ROOT = resolve_live_framework_root(ROOT)
OPT_IN = "OPENMINION_LIVE_CLI_FOCUS_E2E"
CONFIG_ENV = "OPENMINION_BLOCKCHAIN_AUTONOMOUS_FOCUS_CONFIG"
AGENT_ENV = "OPENMINION_BLOCKCHAIN_AUTONOMOUS_FOCUS_AGENT"
PROMPT = (
    "Research a currently deployed public Uniswap smart contract, verify its "
    "deployment and interface against current on-chain state, and read one current "
    "protocol value directly from the contract. Discover the network, RPC, contract "
    "address, ABI, function, and arguments yourself. Do not report a value from "
    "articles, search snippets, or explorer pages alone. If a candidate endpoint "
    "fails verification, research a different public candidate and verify it."
)


def validate_evidence(
    evidence: object, expected: dict[str, object] | None = None
) -> None:
    if not isinstance(evidence, dict):
        raise ValueError("Focus evidence must be an object")
    if evidence.get("schema_version") != "blockchain-autonomous-focus-evidence-v2":
        raise ValueError("Focus evidence schema is missing")
    if evidence.get("scenario_id") != "autonomous-public-discovery":
        raise ValueError("Focus evidence scenario is missing")
    if evidence.get("protocol") != "general_loop_focus_cli":
        raise ValueError("Focus evidence protocol is missing")
    if not evidence.get("source_commit"):
        raise ValueError("Focus source commit is missing")
    if evidence.get("persisted_event_count", 0) < 1:
        raise ValueError("Focus current-session events are missing")
    requested = set(evidence.get("requested_tools", ()))
    if not {"web.search", "blockchain.resolve_contract", "blockchain.inspect"}.issubset(
        requested
    ):
        raise ValueError("Focus research, resolution, and read evidence is incomplete")
    resolution_digests = set(evidence.get("successful_resolution_digests", ()))
    resolved_reads = evidence.get("successful_resolved_reads", ())
    if not resolution_digests or not isinstance(resolved_reads, list):
        raise ValueError("Focus successful resolution evidence is missing")
    if not any(
        isinstance(read, dict)
        and read.get("resolution_digest") in resolution_digests
        and read.get("block_number")
        and read.get("raw_return_digest")
        for read in resolved_reads
    ):
        raise ValueError("Focus successful resolved read does not join to resolution")
    transcript = str(evidence.get("transcript", "")).lower()
    if not all(marker in transcript for marker in ("contract", "block")):
        raise ValueError("Focus answer lacks meaningful contract and block evidence")
    if any(marker in transcript for marker in ("cannot help", "unable to", "refuse")):
        raise ValueError("Focus refusal is not successful evidence")
    if expected is not None:
        for key, value in expected.items():
            if evidence.get(key) != value:
                raise ValueError(f"Focus evidence {key} does not match approval")
    if evidence.get("permission_mode") != "readonly":
        raise ValueError("Focus evidence is not read-only")
    if evidence.get("allow_unsandboxed_exec") is not False:
        raise ValueError("Focus evidence allowed unsandboxed execution")
    if evidence.get("approval_prompt_count") != 0:
        raise ValueError("Focus evidence contains an approval prompt")
    if evidence.get("broadcast_count") != 0:
        raise ValueError("Focus evidence contains a broadcast")
    if evidence.get("terminal_result") != "completed":
        raise ValueError("Focus terminal result is incomplete")
    elapsed = evidence.get("elapsed_seconds")
    maximum = evidence.get("max_duration_seconds")
    if not isinstance(elapsed, (int, float)) or not isinstance(maximum, int):
        raise ValueError("Focus duration evidence is missing")
    if elapsed > maximum:
        raise ValueError("Focus duration exceeded the approved maximum")


def _successful_blockchain_evidence(events: list[Any]) -> tuple[list[str], list[dict]]:
    requests = {
        str(event.data.get("call_id")): str(event.data.get("canonical_name") or "")
        for event in events
        if event.event_type == "tool.call.requested" and event.data.get("call_id")
    }
    resolution_digests: set[str] = set()
    resolved_reads: list[dict] = []
    for event in events:
        if (
            event.event_type != "tool.call.completed"
            or event.data.get("status") != "success"
        ):
            continue
        output = event.data.get("output")
        payload = output.get("outputs") if isinstance(output, dict) else None
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            continue
        tool_name = requests.get(str(event.data.get("call_id")), "")
        data = payload.get("data")
        if not isinstance(data, dict):
            continue
        if (
            tool_name == "blockchain.resolve_contract"
            and payload.get("action") == "resolve_contract"
        ):
            digest = data.get("resolution_digest")
            if isinstance(digest, str):
                resolution_digests.add(digest)
        elif (
            tool_name == "blockchain.inspect"
            and payload.get("action") == "resolved_contract_call"
        ):
            resolved_reads.append(dict(data))
    return sorted(resolution_digests), resolved_reads


def _required_config() -> tuple[Path, dict]:
    raw = str(os.getenv(CONFIG_ENV, "") or "").strip()
    if not raw:
        raise RuntimeError(f"{CONFIG_ENV} is required when {OPT_IN}=1")
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"{CONFIG_ENV} does not name a readable file: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    blockchain = (
        payload.get("runtime", {}).get("tools", {}).get("blockchain", {})
        if isinstance(payload, dict)
        else {}
    )
    if not isinstance(blockchain, dict):
        raise RuntimeError(f"{CONFIG_ENV} blockchain configuration must be an object")
    configured_resources = {
        key for key in ("rpc_url", "chain_id") if blockchain.get(key) not in (None, "")
    }
    if configured_resources:
        raise RuntimeError(
            f"{CONFIG_ENV} must not configure blockchain rpc_url or chain_id for "
            "the autonomous Focus proof"
        )
    return path, payload


def _write_private_config(payload: dict, data_root: Path) -> Path:
    private_payload = json.loads(json.dumps(payload))
    private_payload.setdefault("runtime", {}).setdefault("tools", {})["blockchain"] = {
        "enabled": True
    }
    path = data_root / "focus-config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(private_payload, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)
    return path


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
    expected = _required("OPENMINION_BLOCKCHAIN_E2E_EXPECTED_SOURCE_COMMIT")
    if commit != expected:
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


def _secret_values(payload: object) -> set[bytes]:
    values: set[bytes] = set()

    def visit(value: object, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                visit(child_value, str(child_key).lower())
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif any(marker in key for marker in ("key", "token", "secret", "password")):
            text = str(value).strip()
            if len(text) >= 8:
                values.add(text.encode())

    visit(payload)
    return values


def _assert_retained_evidence_safe(root: Path, secrets: set[bytes]) -> None:
    forbidden = (
        b"minimax_api_key",
        b'"api_key"',
        b'"private_key"',
        b'"raw_transaction"',
        b'"signer_secret',
    )
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_bytes().lower()
        if any(marker in content for marker in forbidden) or any(
            secret in path.read_bytes() for secret in secrets
        ):
            raise RuntimeError("retained evidence contains credential material")


def _stage_c_inputs(config_path: Path, agent_id: str) -> dict[str, object]:
    config_sha256 = _file_sha256(config_path)
    expected_config = _required("OPENMINION_BLOCKCHAIN_E2E_EXPECTED_CONFIG_SHA256")
    if config_sha256 != expected_config:
        raise RuntimeError("Focus config fingerprint does not match Stage C approval")
    maximum = int(_required("OPENMINION_BLOCKCHAIN_E2E_MAX_DURATION_SECONDS"))
    if maximum <= 0:
        raise RuntimeError("maximum duration must be positive")
    chain_id = int(_required("OPENMINION_BLOCKCHAIN_E2E_EXPECTED_CHAIN_ID"))
    rpc_origin = _required("OPENMINION_BLOCKCHAIN_E2E_EXPECTED_RPC_ORIGIN")
    parsed_origin = urlsplit(rpc_origin)
    if not parsed_origin.scheme or not parsed_origin.netloc:
        raise RuntimeError("approved RPC origin is invalid")
    if _required("OPENMINION_BLOCKCHAIN_E2E_EXPECTED_MAX_BROADCASTS") != "0":
        raise RuntimeError("Focus proof requires a zero-broadcast approval")
    if _required("OPENMINION_BLOCKCHAIN_E2E_PERMISSION_MODE") != "readonly":
        raise RuntimeError("Focus proof requires readonly permission mode")
    if _required("OPENMINION_BLOCKCHAIN_E2E_ALLOW_UNSANDBOXED_EXEC") != "0":
        raise RuntimeError("Focus proof forbids unsandboxed execution")
    if _required("OPENMINION_BLOCKCHAIN_E2E_REQUIRES_APPROVAL") != "0":
        raise RuntimeError("Focus proof forbids approval interaction")
    return {
        "focus_agent_id": agent_id,
        "config_sha256": config_sha256,
        "public_read_config_sha256": _required(
            "OPENMINION_BLOCKCHAIN_E2E_EXPECTED_PUBLIC_READ_CONFIG_SHA256"
        ),
        "expected_chain_id": chain_id,
        "expected_rpc_origin": f"{parsed_origin.scheme}://{parsed_origin.netloc}",
        "max_duration_seconds": maximum,
        "max_broadcasts": 0,
    }


def main() -> int:
    if os.getenv(OPT_IN) != "1":
        raise RuntimeError(f"{OPT_IN}=1 is required")
    _config_source, config_payload = _required_config()
    agent_id = _required(AGENT_ENV)
    source_commit = _source_commit()
    evidence_root = _evidence_root()
    data_root = isolate_runtime_roots(prefix="openminion-abo-focus-").parent
    atexit.register(shutil.rmtree, data_root.parent, ignore_errors=True)
    config_path = _write_private_config(config_payload, data_root)
    approved = _stage_c_inputs(config_path, agent_id)
    probe = FocusProbe(
        python_bin=Path(sys.executable),
        openminion_root=ROOT,
        framework_root=FRAMEWORK_ROOT,
        data_root=data_root,
        config_path=config_path,
        agent_id=agent_id,
        workdir=FRAMEWORK_ROOT,
        session_id="abo-focus-minimax",
        include_project_context=False,
        allow_unsandboxed_exec=False,
    )
    scenario = FocusScenario(
        scenario_id="autonomous-public-discovery",
        prompt=PROMPT,
        expected_markers=(),
        timeout=int(approved["max_duration_seconds"]),
        include_project_context=False,
        requires_approval=False,
        max_auto_approvals=0,
        max_auto_continuations=3,
    )
    started = time.monotonic()
    with probe.session(rows=48, cols=160) as session:
        probe.wait_ready(session)
        probe.run_slash(
            session, "/permissions readonly", marker="permissions → read-only"
        )
        transcript = probe.run_turn(session, scenario)
    elapsed_seconds = round(time.monotonic() - started, 3)
    transcript_path = evidence_root / "transcript.txt"
    transcript_path.write_text(transcript, encoding="utf-8")
    events, _messages, brain_session_id = read_focus_evidence(
        probe.environment(), probe.session_id
    )
    current_events = [event for event in events if event.session_id == brain_session_id]
    requested_tools = sorted(
        {
            str(event.data.get("canonical_name") or "")
            for event in current_events
            if event.event_type == "tool.call.requested"
        }
        - {""}
    )
    completed_tools = sorted(
        {
            str(event.data.get("canonical_name") or "")
            for event in current_events
            if event.event_type == "tool.call.completed"
        }
        - {""}
    )
    if "blockchain.send_transaction" in requested_tools:
        raise RuntimeError("read-only Focus scenario attempted a transaction send")
    successful_resolution_digests, successful_resolved_reads = (
        _successful_blockchain_evidence(current_events)
    )
    evidence = {
        "schema_version": "blockchain-autonomous-focus-evidence-v2",
        "scenario_id": "autonomous-public-discovery",
        "protocol": "general_loop_focus_cli",
        "source_commit": source_commit,
        "prompt": PROMPT,
        "hidden_context": {},
        "transcript_path": transcript_path.name,
        "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
        "transcript": transcript,
        "brain_session_id": brain_session_id,
        "persisted_event_count": len(current_events),
        "persisted_event_types": sorted({event.event_type for event in current_events}),
        "requested_tools": requested_tools,
        "completed_tools": completed_tools,
        "successful_resolution_digests": successful_resolution_digests,
        "successful_resolved_reads": successful_resolved_reads,
        "permission_mode": "readonly",
        "allow_unsandboxed_exec": False,
        "approval_prompt_count": 0,
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
    _assert_retained_evidence_safe(evidence_root, _secret_values(config_payload))
    print(f"ABO Focus MiniMax completed evidence={evidence_root / 'evidence.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
