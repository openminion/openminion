from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import patch

from pydantic import BaseModel, ConfigDict
from web3 import Web3

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.helpers.live_e2e_profiles import resolve_live_framework_root  # noqa: E402
from tests.helpers.runtime_roots import (  # noqa: E402
    configure_runtime_roots,
    isolate_runtime_roots,
)

FRAMEWORK_ROOT = resolve_live_framework_root(ROOT)
RUNTIME_ROOT = isolate_runtime_roots(prefix="openminion-abo-local-")
EVIDENCE_ROOT = FRAMEWORK_ROOT / "workspace-tmp" / "abo-e2e" / "local"
FIXTURE = ROOT / "tests" / "e2e" / "fixtures" / "blockchain" / "reference_swap.json"
PUBLIC_RPC_URL = "https://rpc.fixture.test/"
RESEARCH_URL = "https://research.fixture.test/acme-staking"
PRIVATE_KEY = "0x" + "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
PROMPT = (
    "Research the protocol deployment described by public sources, verify it, and "
    "report its quote for 7 units. Then prepare the documented test update for a "
    "fresh test recipient, show me the exact approval facts, and ask before sending. "
    "Only if I approve, send once and verify the confirmed postcondition."
)
_SHA256_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")

from openminion.base.config.runtime.tools import (  # noqa: E402
    BlockchainToolRuntimeConfig,
    ToolRuntimeConfig,
)
from openminion.modules.brain.adapters.tool.runtime import ToolAdapter  # noqa: E402
from openminion.modules.brain.execution.validation import (  # noqa: E402
    normalize_execution_result,
)
from openminion.modules.brain.runner.delegates import _approve_delegate  # noqa: E402
from openminion.modules.brain.bootstrap.resolve import (  # noqa: E402
    apply_resolved_act_route,
    build_internal_dispatch,
    resolve_working_act_route,
)
from openminion.modules.brain.execution.loop_contracts import (  # noqa: E402
    ExecutionContext,
)
from openminion.modules.brain.schemas import (  # noqa: E402
    ActionResult,
    ActDecision,
    BudgetCounters,
    WorkingState,
)
from openminion.modules.brain.schemas.commands import ToolCommand  # noqa: E402
from openminion.modules.brain.schemas.closure import ClosureJudgment  # noqa: E402
from openminion.modules.brain.tools.executor import RunnerCommandExecutor  # noqa: E402
from openminion.modules.llm.schemas import LLMResponse, ToolCall  # noqa: E402
from openminion.modules.policy.adapters.brain import PolicyCtlBrainAdapter  # noqa: E402
from openminion.modules.policy.models import PolicyConfig, RiskSpec  # noqa: E402
from openminion.modules.policy.runtime.service import PolicyCtl  # noqa: E402
from openminion.modules.tool.registry import ToolRegistry, ToolSpec  # noqa: E402
from openminion.modules.tool.runtime.policy import DEFAULT_POLICY, Policy  # noqa: E402
from openminion.modules.tool.runtime.public_https import (  # noqa: E402
    PublicHttpsResponse,
)
from openminion.tools.blockchain.resolution import (  # noqa: E402
    resolve_contract,
)
from openminion.tools.blockchain import resolution as resolution_runtime  # noqa: E402
from openminion.tools.blockchain import resolved_operations  # noqa: E402
from openminion.tools.blockchain import resolved_calls  # noqa: E402
from openminion.tools.blockchain import plugin as blockchain_plugin  # noqa: E402


class _FixtureArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class _ScriptedModel:
    name = "scripted-e2e"

    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages, tools=None, **overrides) -> LLMResponse:
        self.calls.append(
            {
                "messages": list(messages),
                "tools": list(tools or []),
                "tool_choice": overrides.get("tool_choice", "auto"),
                "metadata": dict(overrides.get("metadata") or {}),
            }
        )
        response = self.responses[len(self.calls) - 1]
        return response(list(messages)) if callable(response) else response


class _SessionEvents:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append_event(
        self,
        session_id: str,
        type: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> str:
        event_id = f"event-{len(self.events) + 1}"
        self.events.append(
            {
                "event_id": event_id,
                "session_id": session_id,
                "event_type": type,
                "payload": payload,
                "parent_event_id": kwargs.get("parent_event_id"),
            }
        )
        return event_id

    def get_tool_transcript(self, session_id: str) -> dict[str, Any]:
        return {
            "transcript_lane": "canonical_events",
            "events": [
                {
                    "event_type": event["event_type"],
                    "payload": event["payload"],
                }
                for event in self.events
                if event["session_id"] == session_id
            ],
        }

    def get_active_task_plan(self, session_id: str) -> None:
        del session_id
        return None

    def list_events(self, session_id: str, **_kwargs: Any) -> list[dict[str, Any]]:
        return [event for event in self.events if event["session_id"] == session_id]


class _Services:
    def __init__(self, runner: Any) -> None:
        self.runner = runner
        self.statuses: list[dict[str, Any]] = []

    def save_state(self, *, state: WorkingState) -> None:
        del state

    def emit_phase_status(self, *, state: WorkingState, **kwargs: Any) -> None:
        del state
        self.statuses.append(dict(kwargs))

    def respond_with_meta(
        self,
        *,
        state: WorkingState,
        message: str,
        status: str,
        action_result: ActionResult | None = None,
        **_kwargs: Any,
    ) -> Any:
        state.status = status
        return SimpleNamespace(
            session_id=state.session_id,
            status=status,
            message=message,
            working_state=state,
            action_result=action_result,
            kind="assistant",
        )

    def evaluate_turn_closure(self, **_kwargs: Any) -> ClosureJudgment:
        return ClosureJudgment(satisfied=True, next_action="close")

    def apply_closure_judgment(self, **_kwargs: Any) -> str:
        return "close"

    def extract_success_memories(self, **_kwargs: Any) -> list[Any]:
        return []


class _AnvilHttpsFixture:
    def __init__(self, web3: Web3, address: str, abi: list[dict[str, Any]]) -> None:
        self.web3 = web3
        self.address = address
        self.abi = abi
        code = web3.eth.get_code(address).hex()
        self.code = code if code.startswith("0x") else f"0x{code}"
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        url: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        headers: Any = None,
        timeout: float,
        max_body_bytes: int,
    ) -> PublicHttpsResponse:
        del headers, timeout, max_body_bytes
        self.calls.append({"url": url, "method": method})
        if url == PUBLIC_RPC_URL and method == "POST" and body is not None:
            request = json.loads(body)
            self.calls[-1]["rpc_method"] = request["method"]
            response = self.web3.provider.make_request(
                request["method"], request["params"]
            )
            payload = {"jsonrpc": "2.0", "id": request["id"]}
            if response.get("error") is not None:
                payload["error"] = response["error"]
            else:
                payload["result"] = response.get("result")
            return PublicHttpsResponse(
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps(payload).encode("utf-8"),
            )
        if url.startswith("https://sourcify.dev/server/v2/contract/"):
            payload = {
                "chainId": "31337",
                "address": self.address,
                "match": "exact_match",
                "abi": self.abi,
                "runtimeBytecode": {"onchainBytecode": self.code},
            }
            return PublicHttpsResponse(
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps(payload).encode("utf-8"),
            )
        raise AssertionError(f"unexpected fixture HTTPS request: {method} {url}")


class ProductionBlockchainExecution:
    def __init__(
        self,
        *,
        adapter: ToolAdapter,
        policy_ctl: PolicyCtl,
        transport: Callable[..., PublicHttpsResponse],
        approval_action: str,
    ) -> None:
        self.adapter = adapter
        self.policy_ctl = policy_ctl
        self.transport = transport
        self.approval_action = approval_action
        self.approval: dict[str, Any] | None = None
        self.calls: list[dict[str, Any]] = []
        self.runner = SimpleNamespace(
            tool_api=adapter,
            policy_api=PolicyCtlBrainAdapter(
                policy_ctl, tool_registry=adapter.registry
            ),
            memory_api=None,
        )
        self.runner._approve = lambda *, state, command, logger: _approve_delegate(
            self.runner, state=state, command=command, logger=logger
        )
        self.runner._act = self._act
        self.runner._advance_after_action = lambda **_kwargs: None
        self.executor = RunnerCommandExecutor(self.runner)

    def execute_command(self, **kwargs: Any) -> Any:
        outcome = self.executor.execute_command(**kwargs)
        state = kwargs["state"]
        approval_id = state.pending_policy_approval_id
        if approval_id and outcome.action_result.status == "needs_user":
            preview = state.pending_policy_confirmation_preview
            self.approval = {
                "approval_id": approval_id,
                "action": self.approval_action,
                "preview": dict(preview) if preview else None,
            }
            grant_id = self.policy_ctl.resolve_confirmation(
                approval_id, self.approval_action
            )
            self.approval["grant_id"] = grant_id
            return self.executor.execute_command(**kwargs)
        return outcome

    def advance_after_action(self, **kwargs: Any) -> None:
        self.executor.advance_after_action(**kwargs)

    def _rpc_call(self, record: dict[str, Any], method: str, params: list[Any]) -> Any:
        return resolution_runtime.rpc_call(
            record,
            method,
            params,
            https_request=self.transport,
        )

    def _revalidate(self, record: dict[str, Any]) -> dict[str, str]:
        return resolution_runtime.revalidate_resolution(
            record,
            https_request=self.transport,
        )

    def _revalidate_chain_identity(self, record: dict[str, Any]) -> dict[str, str]:
        return resolution_runtime.revalidate_chain_identity(
            record,
            https_request=self.transport,
        )

    def _act(self, *, state: WorkingState, command: Any, logger: Any) -> Any:
        del logger
        name = str(command.tool_name)
        arguments = dict(command.args)
        self.calls.append({"tool_name": name, "arguments": arguments})
        raw = self.execute_raw(
            name=name,
            arguments=arguments,
            session_id=state.session_id,
            trace_id=state.trace_id,
            inputs=dict(command.inputs),
            invocation_id=command.command_id,
        )
        return normalize_execution_result(
            command_id=command.command_id,
            raw=raw,
            provider="tool",
            tool_name=name,
        )

    def execute_raw(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        session_id: str,
        trace_id: str,
        inputs: dict[str, Any] | None = None,
        invocation_id: str,
    ) -> dict[str, Any]:
        with (
            patch.object(resolved_calls, "rpc_call", self._rpc_call),
            patch.object(
                resolved_calls,
                "revalidate_resolution",
                self._revalidate,
            ),
            patch.object(resolved_operations, "rpc_call", self._rpc_call),
            patch.object(
                resolved_operations,
                "revalidate_resolution",
                self._revalidate,
            ),
            patch.object(
                resolved_operations,
                "revalidate_chain_identity",
                self._revalidate_chain_identity,
            ),
        ):
            raw = self.adapter.execute(
                command={
                    "tool_name": name,
                    "args": arguments,
                    "inputs": dict(inputs or {}),
                    "idempotency_key": invocation_id,
                },
                session_id=session_id,
                trace_id=trace_id,
            )
        return raw

    def close(self) -> None:
        self.adapter.close()
        self.policy_ctl.close()


def _registry(
    research_payload: dict[str, Any], transport: _AnvilHttpsFixture
) -> ToolRegistry:
    registry = ToolRegistry()

    def _search(_args: dict[str, Any], _context: Any) -> dict[str, Any]:
        return {
            "results": [
                {"title": "Protocol deployment", "url": RESEARCH_URL, "rank": 1}
            ]
        }

    def _fetch(_args: dict[str, Any], _context: Any) -> dict[str, Any]:
        return {"url": RESEARCH_URL, "content": research_payload}

    def _unused_handler(_args: dict[str, Any], _context: Any) -> dict[str, Any]:
        raise AssertionError("unexpected deterministic fixture tool")

    for name in (
        "web.search",
        "web.fetch",
        "file.read",
        "file.find",
        "file.search",
        "exec.run",
        "time",
        "weather",
        "location",
    ):
        registry.register(
            ToolSpec(
                name=name,
                args_model=_FixtureArgs,
                min_scope="READ_ONLY",
                handler=(
                    _search
                    if name == "web.search"
                    else _fetch
                    if name == "web.fetch"
                    else _unused_handler
                ),
                prompt_visible_runtime_name=True,
                description=f"Deterministic E2E fixture for {name}.",
            )
        )
    blockchain_plugin.register(registry)
    registry.get("blockchain.resolve_contract").handler = lambda args, context: (
        resolve_contract(args, context, https_request=transport)
    )
    return registry


def _tool_response(call_id: str, name: str, arguments: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        ok=True,
        provider="scripted",
        model="scripted-e2e",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        finish_reason="tool_calls",
    )


def _tool_output(messages: list[Any], call_id: str) -> dict[str, Any]:
    message = next(
        item
        for item in reversed(messages)
        if item.role == "tool" and item.tool_call_id == call_id
    )
    payload = json.loads(message.content)
    return dict(payload.get("outputs") or {})


def _request_then_call(
    *, call_id: str, tool_name: str, arguments: Any
) -> tuple[LLMResponse, Any]:
    requested = _tool_response(
        f"request-{call_id}",
        "tool.request",
        {"name": tool_name},
    )

    def _call(messages: list[Any]) -> LLMResponse:
        resolved_arguments = arguments(messages) if callable(arguments) else arguments
        return _tool_response(call_id, tool_name, resolved_arguments)

    return requested, _call


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_anvil(web3: Web3, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if web3.is_connected():
            return
        if process.poll() is not None:
            raise RuntimeError("Anvil exited before becoming ready")
        time.sleep(0.1)
    raise RuntimeError("Anvil did not become ready")


def build_blockchain_policy(
    workspace: Path, runtime_metadata: dict[str, Any]
) -> Policy:
    raw = json.loads(json.dumps(DEFAULT_POLICY))
    raw["scope"] = "POWER_USER"
    raw["workspace_root"] = str(workspace)
    raw["tools"]["allow_prefix"].extend(("web.", "blockchain."))
    raw["audit"] = {"write_mode": "jsonl_only"}
    raw["context_metadata"] = runtime_metadata
    return Policy(raw=raw)


def _production_execution(
    *,
    session_id: str,
    registry: ToolRegistry,
    runtime_metadata: dict[str, Any],
    transport: _AnvilHttpsFixture,
    approval_action: str,
) -> ProductionBlockchainExecution:
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    policy_path = EVIDENCE_ROOT / f"{session_id}-policy.sqlite"
    policy_path.unlink(missing_ok=True)
    ctl = PolicyCtl.with_sqlite(policy_path, config=PolicyConfig(mode="enforce"))
    ctl.register_risk(
        "blockchain.send_transaction",
        RiskSpec(
            risk_class="financial",
            side_effects="external_account",
            reversibility="irreversible",
            default_confirm=True,
        ),
    )
    runtime_config = SimpleNamespace(
        tools=ToolRuntimeConfig(
            blockchain=BlockchainToolRuntimeConfig(
                enabled=True,
                writes_enabled=True,
                signer_secret_key="abo-local-signer",
                signer_secret_namespace="blockchain",
                max_total_fee_wei="10000000000000000",
                receipt_timeout_seconds=10,
                confirmation_depth=1,
            )
        )
    )
    adapter = ToolAdapter(
        workspace_root=EVIDENCE_ROOT,
        runtime_config=runtime_config,
        runtime_registry=registry,
        policy=build_blockchain_policy(EVIDENCE_ROOT, runtime_metadata),
        policy_ctl=ctl,
        secret_service=SimpleNamespace(
            get_secret_sync=lambda _key, *, namespace: PRIVATE_KEY,
            close_sync=lambda: None,
        ),
        agent_id="abo-local-agent",
    )
    return ProductionBlockchainExecution(
        adapter=adapter,
        policy_ctl=ctl,
        transport=transport,
        approval_action=approval_action,
    )


def _run() -> dict[str, Any]:
    configure_runtime_roots(RUNTIME_ROOT.parents[1])
    anvil = shutil.which("anvil")
    if not anvil:
        raise RuntimeError("anvil is required for blockchain autonomous local E2E")
    port = _free_port()
    process = subprocess.Popen(
        [anvil, "--port", str(port), "--chain-id", "31337", "--silent"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        web3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{port}"))
        _wait_for_anvil(web3, process)
        artifact = json.loads(FIXTURE.read_text(encoding="utf-8"))
        factory = web3.eth.contract(
            abi=artifact["abi"], bytecode=artifact["creation_bytecode"]
        )
        deployment_hash = factory.constructor().transact({"from": web3.eth.accounts[0]})
        receipt = web3.eth.wait_for_transaction_receipt(deployment_hash)
        address = Web3.to_checksum_address(receipt["contractAddress"])
        genesis_hash = web3.eth.get_block(0)["hash"].hex()
        if not genesis_hash.startswith("0x"):
            genesis_hash = f"0x{genesis_hash}"
        research_payload = {
            "rpc_url": PUBLIC_RPC_URL,
            "expected_chain_id": 31337,
            "expected_genesis_hash": genesis_hash,
            "contract_address": address,
            "explorer_contract_url": f"https://explorer.fixture.test/address/{address}",
            "research_source_urls": [RESEARCH_URL],
        }
        transport = _AnvilHttpsFixture(web3, address, artifact["abi"])

        RUNTIME_ROOT.parent.mkdir(parents=True, exist_ok=True)
        data_root = RUNTIME_ROOT.parent
        session_id = "abo-local-autonomous"
        session_records = data_root / "blockchain" / "sessions"
        records_before = list(session_records.rglob("*.json"))
        runtime_metadata = {
            "runtime_tools": {
                "blockchain": {
                    "enabled": True,
                    "writes_enabled": True,
                    "signer_secret_key": "abo-local-signer",
                    "signer_secret_namespace": "blockchain",
                    "max_total_fee_wei": "10000000000000000",
                    "receipt_timeout_seconds": 10,
                    "confirmation_depth": 1,
                }
            }
        }
        registry = _registry(research_payload, transport)
        executor = _production_execution(
            session_id=session_id,
            registry=registry,
            runtime_metadata=runtime_metadata,
            transport=transport,
            approval_action="allow_once",
        )
        recipient = Web3.to_checksum_address("0x" + "66" * 20)
        inspect_read = _request_then_call(
            call_id="inspect-read",
            tool_name="blockchain.inspect",
            arguments=lambda messages: {
                "action": "resolved_contract_call",
                "resolution_digest": _tool_output(messages, "resolve")["data"][
                    "resolution_digest"
                ],
                "function_signature": "quote(uint256)",
                "arguments": [7],
            },
        )
        prepare = _request_then_call(
            call_id="prepare",
            tool_name="blockchain.prepare_transaction",
            arguments=lambda messages: {
                "kind": "resolved_contract_call",
                "resolution_digest": _tool_output(messages, "resolve")["data"][
                    "resolution_digest"
                ],
                "function_signature": "swap((address,uint256,uint256))",
                "arguments": [[recipient, 7, 14]],
                "value_wei": "0",
                "postconditions": [
                    {
                        "function_signature": "outputOf(address)",
                        "arguments": [recipient],
                        "expected_result": ["14"],
                    }
                ],
            },
        )
        send = _request_then_call(
            call_id="send",
            tool_name="blockchain.send_transaction",
            arguments=lambda messages: {
                "preparation_digest": _tool_output(messages, "prepare")[
                    "preparation_digest"
                ]
            },
        )
        model = _ScriptedModel(
            [
                LLMResponse(
                    ok=True,
                    provider="scripted",
                    model="scripted-e2e",
                    output_text='{"tool_ids":["web.search","web.fetch"]}',
                ),
                _tool_response(
                    "search",
                    "web.search",
                    {"query": "Acme staking deployment public quote"},
                ),
                _tool_response("fetch", "web.fetch", {"url": RESEARCH_URL}),
                _tool_response(
                    "request-resolver",
                    "tool.request",
                    {"name": "blockchain.resolve_contract"},
                ),
                lambda messages: _tool_response(
                    "resolve",
                    "blockchain.resolve_contract",
                    dict(_tool_output(messages, "fetch")["content"]),
                ),
                *inspect_read,
                *prepare,
                *send,
                lambda messages: _tool_response(
                    "operation-status",
                    "blockchain.inspect",
                    {
                        "action": "operation_status",
                        "preparation_digest": _tool_output(messages, "prepare")[
                            "preparation_digest"
                        ],
                    },
                ),
                LLMResponse(
                    ok=True,
                    provider="scripted",
                    model="scripted-e2e",
                    output_text=(
                        "The researched contract was verified, read, updated once, "
                        "and the confirmed result was checked."
                    ),
                    finalization_status={
                        "status": "final_answer",
                        "reasoning": "Research and the typed operation completed.",
                    },
                    finish_reason="stop",
                ),
            ]
        )
        session_api = _SessionEvents()
        runner = executor.runner
        runner.session_api = session_api
        runner.options = SimpleNamespace(
            failure_strategy="halt",
            tool_schema_shortlisting_enabled=True,
        )
        runner.turn_input_queue = None
        runner.llm_api = None
        runner.skill_api = None
        services = _Services(runner)
        state = WorkingState(
            session_id=session_id,
            agent_id="abo-local-agent",
            trace_id="abo-local-turn",
            goal=PROMPT,
            budgets_remaining=BudgetCounters(
                ticks=20,
                tool_calls=10,
                a2a_calls=0,
                tokens=20_000,
                time_ms=120_000,
            ),
            llm_calls_max=20,
        )
        decision = ActDecision(route="act")
        route = resolve_working_act_route(
            decision=decision,
            state=state,
            default_act_profile=None,
            has_new_user_input=True,
        )
        apply_resolved_act_route(decision=decision, route=route)
        dispatch = build_internal_dispatch(
            SimpleNamespace(state=state, decision=decision, user_input=PROMPT)
        )
        dispatch.handler.apply_mode_config(
            config={"tool_schema_shortlisting_enabled": True},
            runner=runner,
            profile=None,
        )
        result = dispatch.handler.execute(
            ExecutionContext(
                state=state,
                decision=dispatch.decision,
                user_input=PROMPT,
                logger=SimpleNamespace(
                    info=lambda *_a, **_k: None,
                    emit=lambda *_a, **_k: None,
                ),
                options=SimpleNamespace(
                    profile=None,
                    agent_profile=None,
                    adaptive_budget_config=None,
                ),
                llm_adapter=SimpleNamespace(client=model),
                command_executor=executor,
                _services=services,
            )
        )
        records_after = sorted(session_records.rglob("*.json"))
        resolution_result = next(
            (
                event["payload"]["output"]["outputs"]
                for event in session_api.events
                if event["event_type"] == "tool.call.completed"
                and event["payload"]["call_id"] == "resolve"
            ),
            None,
        )
        if resolution_result is None:
            raise AssertionError(
                {
                    "result": {"status": result.status, "message": result.message},
                    "model_calls": len(model.calls),
                    "executed_tools": executor.calls,
                    "events": session_api.events,
                }
            )
        digest = resolution_result["data"]["resolution_digest"]
        read_result = next(
            event["payload"]["output"]["outputs"]
            for event in session_api.events
            if event["event_type"] == "tool.call.completed"
            and event["payload"]["call_id"] == "inspect-read"
        )
        preparation_result = next(
            event["payload"]["output"]["outputs"]
            for event in session_api.events
            if event["event_type"] == "tool.call.completed"
            and event["payload"]["call_id"] == "prepare"
        )
        send_result = next(
            (
                event["payload"]["output"]["outputs"]
                for event in session_api.events
                if event["event_type"] == "tool.call.completed"
                and event["payload"]["call_id"] == "send"
            ),
            None,
        )
        if send_result is None:
            raise AssertionError(
                {
                    "result": {"status": result.status, "message": result.message},
                    "executed_tools": executor.calls,
                    "approval": executor.approval,
                    "events": session_api.events,
                }
            )
        operation_status = next(
            event["payload"]["output"]["outputs"]
            for event in session_api.events
            if event["event_type"] == "tool.call.completed"
            and event["payload"]["call_id"] == "operation-status"
        )
        preparation_digest_value = preparation_result["preparation_digest"]
        broadcasts_before_restart = sum(
            call.get("rpc_method") == "eth_sendRawTransaction"
            for call in transport.calls
        )
        restart_raw = executor.execute_raw(
            name="blockchain.inspect",
            arguments={
                "action": "operation_status",
                "preparation_digest": preparation_digest_value,
            },
            session_id=session_id,
            trace_id="abo-local-restart",
            invocation_id="abo-local-restart-status",
        )
        restart_status = restart_raw["outputs"]
        broadcasts_after_restart = sum(
            call.get("rpc_method") == "eth_sendRawTransaction"
            for call in transport.calls
        )
        denied_session_id = "abo-local-denied"
        denied = _production_execution(
            session_id=denied_session_id,
            registry=registry,
            runtime_metadata=runtime_metadata,
            transport=transport,
            approval_action="deny",
        )
        denied_resolution = denied.execute_raw(
            name="blockchain.resolve_contract",
            arguments=research_payload,
            session_id=denied_session_id,
            trace_id="abo-local-denied",
            invocation_id="denied-resolve",
        )["outputs"]
        denied_recipient = Web3.to_checksum_address("0x" + "77" * 20)
        denied_preparation = denied.execute_raw(
            name="blockchain.prepare_transaction",
            arguments={
                "kind": "resolved_contract_call",
                "resolution_digest": denied_resolution["data"]["resolution_digest"],
                "function_signature": "swap((address,uint256,uint256))",
                "arguments": [[denied_recipient, 7, 14]],
                "value_wei": "0",
                "postconditions": [
                    {
                        "function_signature": "outputOf(address)",
                        "arguments": [denied_recipient],
                        "expected_result": ["14"],
                    }
                ],
            },
            session_id=denied_session_id,
            trace_id="abo-local-denied",
            invocation_id="denied-prepare",
        )["outputs"]
        denied_state = WorkingState(
            session_id=denied_session_id,
            agent_id="abo-local-agent",
            trace_id="abo-local-denied",
            goal=PROMPT,
            budgets_remaining=BudgetCounters(
                ticks=4,
                tool_calls=4,
                a2a_calls=0,
                tokens=4_000,
                time_ms=30_000,
            ),
        )
        denied_before = sum(
            call.get("rpc_method") == "eth_sendRawTransaction"
            for call in transport.calls
        )
        denied_outcome = denied.execute_command(
            state=denied_state,
            command=ToolCommand(
                kind="tool",
                title="blockchain.send_transaction",
                tool_name="blockchain.send_transaction",
                args={"preparation_digest": denied_preparation["preparation_digest"]},
                inputs={},
                idempotency_key="denied-send",
            ),
            logger=SimpleNamespace(emit=lambda *_a, **_k: None),
            include_reflect=False,
        )
        denied_after = sum(
            call.get("rpc_method") == "eth_sendRawTransaction"
            for call in transport.calls
        )
        audit_events = []
        for audit_path in sorted((data_root / "tool-runs").rglob("audit.jsonl")):
            for line in audit_path.read_text(encoding="utf-8").splitlines():
                audit = json.loads(line)
                if audit.get("preparation_digest") == preparation_digest_value:
                    audit_events.append(audit)
        evidence = {
            "schema_version": "blockchain-autonomous-local-evidence-v1",
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "session_id": session_id,
            "prompt": PROMPT,
            "hidden_context": {},
            "route": {
                "act_profile": route.act_profile,
                "execution_target": route.execution_target.kind,
                "handler": dispatch.handler.__class__.__name__,
            },
            "model_calls": [
                {
                    "tool_choice": call["tool_choice"],
                    "tools": [spec.name for spec in call["tools"]],
                    "tool_result_call_ids": [
                        str(message.tool_call_id)
                        for message in call["messages"]
                        if message.role == "tool" and message.tool_call_id
                    ],
                    "observed_digests": sorted(
                        {
                            digest
                            for message in call["messages"]
                            if message.role == "tool"
                            for digest in _SHA256_DIGEST.findall(
                                str(message.content or "")
                            )
                        }
                    ),
                }
                for call in model.calls
            ],
            "executed_tools": executor.calls,
            "events": session_api.events,
            "iteration_events": [
                status["payload"]
                for status in services.statuses
                if status.get("source_event") == "adaptive_loop_iteration"
            ],
            "session_records": {
                "before": len(records_before),
                "after": len(records_after),
                "files": [path.name for path in records_after],
            },
            "resolution": resolution_result,
            "resolution_digest": digest,
            "read": read_result,
            "preparation": preparation_result,
            "preparation_digest": preparation_digest_value,
            "send": send_result,
            "operation_status": operation_status,
            "restart_status": restart_status,
            "broadcasts_before_restart": broadcasts_before_restart,
            "broadcasts_after_restart": broadcasts_after_restart,
            "audit_events": audit_events,
            "approved_policy": executor.approval,
            "execution_owners": {
                "command_executor": executor.executor.__class__.__name__,
                "tool_adapter": executor.adapter.__class__.__name__,
                "policy_adapter": executor.runner.policy_api.__class__.__name__,
                "send_scope": registry.get("blockchain.send_transaction").min_scope,
            },
            "denied_policy": {
                "session_id": denied_session_id,
                "approval": denied.approval,
                "result_status": denied_outcome.action_result.status,
                "broadcasts_before": denied_before,
                "broadcasts_after": denied_after,
            },
            "transport_calls": transport.calls,
            "chain": {
                "chain_id": web3.eth.chain_id,
                "contract_address": address,
                "contract_code_sha256": hashlib.sha256(
                    bytes(web3.eth.get_code(address))
                ).hexdigest(),
            },
            "result": {"status": result.status, "message": result.message},
        }
        denied.close()
        executor.close()
        EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
        (EVIDENCE_ROOT / "evidence.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return evidence
    finally:
        process.terminate()
        process.wait(timeout=10)


def main() -> int:
    evidence = _run()
    print(
        "ABO local autonomous PASS "
        f"digest={evidence['resolution_digest']} evidence={EVIDENCE_ROOT / 'evidence.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
