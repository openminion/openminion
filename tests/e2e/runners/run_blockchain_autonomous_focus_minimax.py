from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.e2e.cli.focus.harness import FocusProbe  # noqa: E402
from tests.e2e.cli.focus.harness.assertions import read_focus_evidence  # noqa: E402
from tests.e2e.cli.focus.harness.scenarios import FocusScenario  # noqa: E402
from tests.helpers.live_e2e_profiles import resolve_live_framework_root  # noqa: E402
from tests.helpers.runtime_roots import isolate_runtime_roots  # noqa: E402

FRAMEWORK_ROOT = resolve_live_framework_root(ROOT)
EVIDENCE_ROOT = FRAMEWORK_ROOT / "workspace-tmp" / "abo-e2e" / "focus"
OPT_IN = "OPENMINION_LIVE_CLI_FOCUS_E2E"
CONFIG_ENV = "OPENMINION_BLOCKCHAIN_AUTONOMOUS_FOCUS_CONFIG"
PROMPT = (
    "Research a currently deployed public Uniswap smart contract, verify it from "
    "public sources, and report one read-only protocol value. Discover the network, "
    "RPC, contract address, ABI, function, and arguments yourself."
)


def validate_evidence(evidence: object) -> None:
    if not isinstance(evidence, dict):
        raise ValueError("Focus evidence must be an object")
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


def _successful_blockchain_evidence(events: list[Any]) -> tuple[list[str], list[dict]]:
    requests = {
        str(event.data.get("call_id")): str(event.data.get("canonical_name") or "")
        for event in events
        if event.event_type == "tool.call.requested" and event.data.get("call_id")
    }
    resolution_digests: set[str] = set()
    resolved_reads: list[dict] = []
    for event in events:
        if event.event_type != "tool.call.completed" or event.data.get("status") != "success":
            continue
        output = event.data.get("output")
        payload = output.get("outputs") if isinstance(output, dict) else None
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            continue
        tool_name = requests.get(str(event.data.get("call_id")), "")
        data = payload.get("data")
        if not isinstance(data, dict):
            continue
        if tool_name == "blockchain.resolve_contract" and payload.get("action") == "resolve_contract":
            digest = data.get("resolution_digest")
            if isinstance(digest, str):
                resolution_digests.add(digest)
        elif tool_name == "blockchain.inspect" and payload.get("action") == "resolved_contract_call":
            resolved_reads.append(dict(data))
    return sorted(resolution_digests), resolved_reads


def _required_config() -> Path:
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
    if not isinstance(blockchain, dict) or blockchain.get("enabled") is not True:
        raise RuntimeError(
            f"{CONFIG_ENV} must enable runtime.tools.blockchain without injecting "
            "task resources into the prompt"
        )
    configured_resources = {
        key for key in ("rpc_url", "chain_id") if blockchain.get(key) not in (None, "")
    }
    if configured_resources:
        raise RuntimeError(
            f"{CONFIG_ENV} must not configure blockchain rpc_url or chain_id for "
            "the autonomous Focus proof"
        )
    return path


def main() -> int:
    if os.getenv(OPT_IN) != "1":
        raise RuntimeError(f"{OPT_IN}=1 is required")
    config_path = _required_config()
    data_root = isolate_runtime_roots(prefix="openminion-abo-focus-").parent
    probe = FocusProbe(
        python_bin=Path(sys.executable),
        openminion_root=ROOT,
        framework_root=FRAMEWORK_ROOT,
        data_root=data_root,
        config_path=config_path,
        agent_id=os.getenv("OPENMINION_BLOCKCHAIN_AUTONOMOUS_FOCUS_AGENT")
        or "minimax-m2-7",
        workdir=FRAMEWORK_ROOT,
        session_id="abo-focus-minimax",
        include_project_context=False,
    )
    scenario = FocusScenario(
        scenario_id="autonomous-public-discovery",
        prompt=PROMPT,
        expected_markers=(),
        timeout=480,
        include_project_context=False,
        max_auto_continuations=3,
    )
    with probe.session(rows=48, cols=160) as session:
        probe.wait_ready(session)
        transcript = probe.run_turn(session, scenario)
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    transcript_path = EVIDENCE_ROOT / "transcript.txt"
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
    successful_resolution_digests, successful_resolved_reads = (
        _successful_blockchain_evidence(current_events)
    )
    evidence = {
        "schema_version": "blockchain-autonomous-focus-evidence-v1",
        "protocol": "general_loop_focus_cli",
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "prompt": PROMPT,
        "hidden_context": {},
        "config_path": str(config_path),
        "transcript_path": str(transcript_path),
        "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
        "transcript": transcript,
        "brain_session_id": brain_session_id,
        "persisted_event_count": len(current_events),
        "persisted_event_types": sorted({event.event_type for event in current_events}),
        "requested_tools": requested_tools,
        "successful_resolution_digests": successful_resolution_digests,
        "successful_resolved_reads": successful_resolved_reads,
    }
    validate_evidence(evidence)
    (EVIDENCE_ROOT / "evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"ABO Focus MiniMax completed evidence={EVIDENCE_ROOT / 'evidence.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
