from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import time
from typing import Any

import pytest
import yaml
from web3 import Web3

from openminion.base.config import OpenMinionConfig
from openminion.base.config.env import EnvironmentConfig
from openminion.modules.brain.adapters.a2a.runtime import A2actlAdapter
from openminion.modules.tool.contracts.provider_types import ProviderToolSpec
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.delegation import A2aRuntimeDelegateAdapter
from openminion.modules.tool.selection.service import ToolSelectionService
from openminion.tools.blockchain import resolved_calls
from openminion.tools.blockchain.public_https import PublicHttpsResponse
from openminion.tools.blockchain import resolution as resolution_runtime
from openminion.tools.blockchain.resolution import resolve_contract
from openminion.tools.blockchain.runtime import prepare_transaction
from openminion.tools.agent.plugin import _h_task_delegate


_TARGET = Web3.to_checksum_address("0x" + "12" * 20)
_GENESIS_HASH = "0x" + "11" * 32
_BLOCK_HASH = "0x" + "22" * 32
_TARGET_CODE = "0x6000"
_PRIVATE_KEY = "0x" + "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


class _ResolutionTransport:
    def __call__(self, url: str, **kwargs: Any) -> PublicHttpsResponse:
        if url.startswith("https://sourcify.dev/"):
            metadata = {
                "chainId": "1",
                "address": _TARGET,
                "match": "exact_match",
                "abi": [
                    {
                        "type": "function",
                        "name": "setValue",
                        "inputs": [{"name": "value", "type": "uint256"}],
                        "outputs": [],
                        "stateMutability": "nonpayable",
                    }
                ],
                "runtimeBytecode": {"onchainBytecode": _TARGET_CODE},
            }
            return PublicHttpsResponse(
                status=200,
                headers={},
                body=json.dumps(metadata).encode(),
            )

        request = json.loads(kwargs["body"])
        method = request["method"]
        params = request["params"]
        if method == "eth_chainId":
            result: Any = "0x1"
        elif method == "eth_getBlockByNumber" and params[0] == "0x0":
            result = {
                "number": "0x0",
                "hash": _GENESIS_HASH,
                "parentHash": "0x" + "00" * 32,
            }
        elif method == "eth_getBlockByNumber":
            result = {
                "number": "0x10",
                "hash": _BLOCK_HASH,
                "baseFeePerGas": "0xa",
            }
        elif method == "eth_getCode":
            result = _TARGET_CODE
        elif method == "eth_getStorageAt":
            result = "0x" + "00" * 32
        elif method == "eth_getBalance":
            result = "0xde0b6b3a7640000"
        elif method == "eth_getTransactionCount":
            result = "0x1"
        elif method == "eth_call":
            result = "0x"
        elif method == "eth_estimateGas":
            result = "0x5208"
        elif method == "eth_maxPriorityFeePerGas":
            result = "0x3"
        else:
            raise AssertionError((method, params))
        return PublicHttpsResponse(
            status=200,
            headers={},
            body=json.dumps(
                {"jsonrpc": "2.0", "id": request["id"], "result": result}
            ).encode(),
        )


class _RecordingTelemetry:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit_canonical_event(
        self,
        session_id: str,
        turn_id: str,
        event_type: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        self.events.append(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "event_type": event_type,
                "payload": dict(payload),
                "status": kwargs.get("status"),
            }
        )


class _ConfiguredResearchRuntime:
    def __init__(self, profile_path: Path) -> None:
        self.profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        self.calls: list[dict[str, Any]] = []
        self.exposed_tools: list[list[str]] = []
        self.broadcast_count = 0

    def list_registered_agents(self) -> list[str]:
        return [str(self.profile["agent_id"])]

    def run_turn(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        specs = [
            ProviderToolSpec(name=name, description=name)
            for name in (
                "web.search",
                "web.fetch",
                "blockchain.prepare_transaction",
                "blockchain.send_transaction",
            )
        ]
        selection = ToolSelectionService(
            OpenMinionConfig().runtime.tool_selection,
            ToolRegistry(),
        )._apply_identity_tool_filter(specs, self.profile["tool_posture"])
        exposed = [tool.name for tool in selection.specs]
        self.exposed_tools.append(exposed)
        self.broadcast_count += sum(
            name == "blockchain.send_transaction" for name in exposed
        )

        payload = kwargs["payload"]
        assert payload["agent_id"] == "bogr-readonly-researcher"
        assert payload["inbound_metadata"]["permission_mode"] == "readonly"
        return {
            "body": "Public-source research complete.",
            "session_id": payload["session_id"],
            "run_id": "child-run-1",
            "metadata": {
                "brain_status": "done",
                "session_id": payload["session_id"],
                "delegation_result_summary": {
                    "summary": "Two public sources were checked.",
                    "artifacts_produced": [
                        "https://example.test/source-a",
                        "https://example.test/source-b",
                    ],
                    "status": "complete",
                },
            },
        }


def _tool_context(
    adapter: A2aRuntimeDelegateAdapter,
    *,
    session_id: str,
    workspace: Path,
) -> SimpleNamespace:
    return SimpleNamespace(
        a2a_delegate_api=adapter,
        permission_mode="ask",
        policy=SimpleNamespace(
            raw={
                "context_metadata": {
                    "invocation_id": "11111111-1111-4111-8111-111111111111",
                    "execution_id": "22222222-2222-4222-8222-222222222222",
                }
            }
        ),
        session_id=session_id,
        telemetry_session_id=session_id,
        telemetry_turn_id="turn-1",
        workspace=workspace,
    )


def test_readonly_child_delegation_uses_configured_a2a_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    profile_path = (
        Path(__file__).parents[1]
        / "e2e"
        / "fixtures"
        / "blockchain"
        / "bogr-readonly-researcher.yaml"
    )
    runtime = _ConfiguredResearchRuntime(profile_path)
    approval_callback = object()
    a2actl = A2actlAdapter(
        home_root=tmp_path,
        agent_id="parent",
        runtime_resolver=lambda: runtime,
        approval_callback=approval_callback,
    )
    telemetry = _RecordingTelemetry()
    delegate = A2aRuntimeDelegateAdapter(
        a2a_call=a2actl.call,
        parent_agent_id="parent",
        telemetryctl=telemetry,
    )
    parent_context = _tool_context(
        delegate,
        session_id="parent-session",
        workspace=tmp_path,
    )

    try:
        completed = _h_task_delegate(
            {
                "mode": "sync",
                "agent_id": "bogr-readonly-researcher",
                "instruction": "Find public contract sources.",
                "child_permission_mode": "readonly",
            },
            parent_context,
        )
        sync_events = list(telemetry.events)
        transport = _ResolutionTransport()
        blockchain_context = SimpleNamespace(
            session_id="parent-session",
            env=EnvironmentConfig(
                values={
                    "OPENMINION_HOME": str(tmp_path),
                    "OPENMINION_DATA_ROOT": str(tmp_path / "data"),
                }
            ),
            metadata={
                "runtime_tools": {
                    "blockchain": {
                        "enabled": True,
                        "writes_enabled": True,
                        "signer_secret_key": "parent-signer",
                        "signer_secret_namespace": "blockchain",
                        "max_total_fee_wei": "10000000000000000",
                    }
                }
            },
            secret_service=SimpleNamespace(
                get_secret_sync=lambda _key, *, namespace: _PRIVATE_KEY
            ),
        )
        source_urls = completed["outputs"]["delegation_result_summary"][
            "artifacts_produced"
        ]
        resolution = resolve_contract(
            {
                "rpc_url": "https://rpc.example",
                "expected_chain_id": 1,
                "expected_genesis_hash": _GENESIS_HASH,
                "contract_address": _TARGET,
                "explorer_contract_url": (
                    "https://explorer.example/address?view=contract"
                ),
                "research_source_urls": source_urls,
            },
            blockchain_context,
            https_request=transport,
        )
        assert resolution["ok"] is True
        resolution_digest = resolution["data"]["resolution_digest"]
        monkeypatch.setattr(
            resolved_calls,
            "revalidate_resolution",
            lambda record: resolution_runtime.revalidate_resolution(
                record, https_request=transport
            ),
        )
        monkeypatch.setattr(
            resolved_calls,
            "rpc_call",
            lambda record, method, params: resolution_runtime.rpc_call(
                record,
                method,
                params,
                https_request=transport,
            ),
        )
        prepared = prepare_transaction(
            {
                "kind": "resolved_contract_call",
                "resolution_digest": resolution_digest,
                "function_signature": "setValue(uint256)",
                "arguments": [7],
            },
            blockchain_context,
        )
        assert prepared["ok"] is True
        assert prepared["resolution_digest"] == resolution_digest

        started = _h_task_delegate(
            {
                "mode": "async",
                "agent_id": "bogr-readonly-researcher",
                "instruction": "Revalidate public contract sources.",
                "child_permission_mode": "readonly",
            },
            parent_context,
        )

        foreign_context = _tool_context(
            delegate,
            session_id="other-session",
            workspace=tmp_path,
        )
        cross_session = _h_task_delegate(
            {"mode": "status", "task_id": started["task_id"]},
            foreign_context,
        )

        terminal: dict[str, Any] = {}
        for _ in range(100):
            terminal = _h_task_delegate(
                {"mode": "status", "task_id": started["task_id"]},
                parent_context,
            )
            if terminal["status"] not in {"pending", "running"}:
                break
            time.sleep(0.01)
    finally:
        a2actl.close()

    assert completed["child_permission_mode"] == "readonly"
    assert completed["outputs"]["delegation_result_summary"] == {
        "summary": "Two public sources were checked.",
        "artifacts_produced": [
            "https://example.test/source-a",
            "https://example.test/source-b",
        ],
        "status": "complete",
    }
    assert started["child_permission_mode"] == "readonly"
    assert terminal["status"] == "completed"
    assert cross_session["status"] == "failed"
    assert cross_session["outputs"]["error"]["code"] == "A2A_JOB_POLL_FAILED"
    assert "does not belong" in cross_session["outputs"]["error"]["message"]

    assert runtime.exposed_tools == [
        ["web.search", "web.fetch"],
        ["web.search", "web.fetch"],
    ]
    assert all("approval_callback" not in call for call in runtime.calls)
    assert runtime.broadcast_count == 0

    assert [event["event_type"] for event in sync_events] == [
        "agent.handoff.started",
        "agent.handoff.completed",
    ]
    assert {event["session_id"] for event in sync_events} == {"parent-session"}
    assert {event["turn_id"] for event in sync_events} == {"turn-1"}
    assert (
        sync_events[0]["payload"]["handoff_id"]
        == (sync_events[1]["payload"]["handoff_id"])
    )
    assert all(
        set(event["payload"])
        == {
            "handoff_id",
            "handoff_role",
            "target_agent",
        }
        for event in sync_events
    )
