from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from pydantic import BaseModel, ConfigDict
from web3 import Web3

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.helpers.live_e2e_profiles import resolve_live_framework_root  # noqa: E402
from tests.helpers.runtime_roots import isolate_runtime_roots  # noqa: E402

FRAMEWORK_ROOT = resolve_live_framework_root(ROOT)
RUNTIME_ROOT = isolate_runtime_roots(prefix="openminion-abo-local-")
EVIDENCE_ROOT = FRAMEWORK_ROOT / "workspace-tmp" / "abo-e2e" / "local"
FIXTURE = ROOT / "tests" / "e2e" / "fixtures" / "blockchain" / "reference_swap.json"
PUBLIC_RPC_URL = "https://rpc.fixture.test/"
RESEARCH_URL = "https://research.fixture.test/acme-staking"
PRIVATE_KEY = "0x" + "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
PROMPT = (
    "Find the Acme staking contract, verify the deployment, and report what its "
    "public quote function returns for 7 units."
)
_SHA256_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")

from openminion.base.config.env import EnvironmentConfig  # noqa: E402
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
    new_uuid,
)
from openminion.modules.brain.schemas.closure import ClosureJudgment  # noqa: E402
from openminion.modules.brain.tools.executor import (  # noqa: E402
    CommandExecutionOutcome,
)
from openminion.modules.llm.schemas import LLMResponse, ToolCall  # noqa: E402
from openminion.modules.tool.registry import ToolRegistry, ToolSpec  # noqa: E402
from openminion.modules.tool.runtime.public_https import (  # noqa: E402
    PublicHttpsResponse,
)
from openminion.tools.blockchain.resolution import (  # noqa: E402
    ResolveContractArgs,
    resolve_contract,
)
from openminion.tools.blockchain.schemas import (  # noqa: E402
    InspectArgs,
    PrepareArgs,
    SendPreparedTransactionArgs,
)
from openminion.tools.blockchain import resolution as resolution_runtime  # noqa: E402
from openminion.tools.blockchain import runtime as blockchain_runtime  # noqa: E402


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


class _CommandExecutor:
    def __init__(
        self,
        *,
        context: Any,
        transport: _AnvilHttpsFixture,
        research_payload: dict[str, Any],
    ) -> None:
        self.context = context
        self.transport = transport
        self.research_payload = research_payload
        self.calls: list[dict[str, Any]] = []

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

    def _resolved_runtime_call(self, name: str, arguments: dict[str, Any]) -> Any:
        with (
            patch.object(blockchain_runtime, "rpc_call", self._rpc_call),
            patch.object(
                blockchain_runtime,
                "revalidate_resolution",
                self._revalidate,
            ),
        ):
            if name == "blockchain.inspect":
                return blockchain_runtime.inspect_blockchain(arguments, self.context)
            if name == "blockchain.prepare_transaction":
                return blockchain_runtime.prepare_transaction(arguments, self.context)
            if name == "blockchain.send_transaction":
                prepared = blockchain_runtime._load_resolved_preparation(
                    arguments["preparation_digest"], self.context
                )
                return blockchain_runtime.send_transaction(prepared, self.context)
        raise AssertionError(f"unexpected resolved runtime call: {name}")

    def execute_command(
        self,
        *,
        state: WorkingState,
        command: Any,
        logger: Any,
        include_reflect: bool = False,
        **_kwargs: Any,
    ) -> CommandExecutionOutcome:
        del state, logger, include_reflect
        name = str(command.tool_name)
        arguments = dict(command.args)
        self.calls.append({"tool_name": name, "arguments": arguments})
        if name == "web.search":
            summary = "Found the Acme staking deployment guide."
            outputs = {
                "results": [{"title": "Acme staking", "url": RESEARCH_URL, "rank": 1}]
            }
        elif name == "web.fetch":
            summary = "Fetched the Acme staking deployment facts."
            outputs = {"url": RESEARCH_URL, "content": self.research_payload}
        elif name == "blockchain.resolve_contract":
            outputs = resolve_contract(
                arguments,
                self.context,
                https_request=self.transport,
            )
            if outputs.get("ok") is not True:
                raise AssertionError(outputs)
            summary = "Verified and stored the researched contract candidate."
        elif name in {
            "blockchain.inspect",
            "blockchain.prepare_transaction",
            "blockchain.send_transaction",
        }:
            outputs = self._resolved_runtime_call(name, arguments)
            if outputs.get("ok") is not True:
                raise AssertionError(outputs)
            summary = f"Completed {name}."
        else:
            raise AssertionError(f"unexpected tool call: {name}")
        return CommandExecutionOutcome(
            approved_command=command,
            action_result=ActionResult(
                command_id=new_uuid(),
                status="success",
                summary=summary,
                outputs=outputs,
            ),
        )

    def advance_after_action(self, **_kwargs: Any) -> None:
        return None


def _registry() -> ToolRegistry:
    registry = ToolRegistry()

    def _unused_handler(_args: dict[str, Any], _context: Any) -> dict[str, Any]:
        raise AssertionError("fixture execution is owned by _CommandExecutor")

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
                handler=_unused_handler,
                prompt_visible_runtime_name=True,
                description=f"Deterministic E2E fixture for {name}.",
            )
        )
    registry.register(
        ToolSpec(
            name="blockchain.resolve_contract",
            args_model=ResolveContractArgs,
            min_scope="READ_ONLY",
            handler=_unused_handler,
            prompt_visible_runtime_name=True,
            description="Validate one already-researched EVM contract candidate.",
        )
    )
    for name, args_model, description in (
        (
            "blockchain.inspect",
            InspectArgs,
            "Inspect configured or digest-bound blockchain state.",
        ),
        (
            "blockchain.prepare_transaction",
            PrepareArgs,
            "Prepare and simulate one exact blockchain transaction.",
        ),
        (
            "blockchain.send_transaction",
            SendPreparedTransactionArgs,
            "Submit one earlier approved preparation by digest.",
        ),
    ):
        registry.register(
            ToolSpec(
                name=name,
                args_model=args_model,
                min_scope="READ_ONLY",
                handler=_unused_handler,
                prompt_visible_runtime_name=True,
                description=description,
            )
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


def _run() -> dict[str, Any]:
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
        with tempfile.TemporaryDirectory(
            prefix="run-", dir=RUNTIME_ROOT.parent
        ) as temporary:
            data_root = Path(temporary)
            env = EnvironmentConfig(
                values={
                    "OPENMINION_HOME": str(data_root),
                    "OPENMINION_DATA_ROOT": str(data_root),
                }
            )
            session_id = "abo-local-autonomous"
            session_records = data_root / "blockchain" / "sessions"
            records_before = list(session_records.rglob("*.json"))
            audit_events: list[dict[str, Any]] = []
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
            context = SimpleNamespace(
                session_id=session_id,
                env=env,
                metadata=runtime_metadata,
                secret_service=SimpleNamespace(
                    get_secret_sync=lambda _key, *, namespace: PRIVATE_KEY
                ),
                policy_authorization=SimpleNamespace(
                    invocation_hash="a" * 64,
                    approval_id="approval-abo-local",
                    grant_id="grant-abo-local",
                    duration_type="allow_once",
                ),
                invocation_id="abo-local-send",
                write_audit_event=lambda payload: audit_events.append(payload) or True,
            )
            executor = _CommandExecutor(
                context=context,
                transport=transport,
                research_payload=research_payload,
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
            registry = _registry()
            allowed = frozenset({*registry.list(), "tool.request"})
            session_api = _SessionEvents()
            runner = SimpleNamespace(
                tool_api=SimpleNamespace(
                    registry=registry,
                    is_tool_allowed=lambda name: name in allowed,
                ),
                session_api=session_api,
                options=SimpleNamespace(
                    failure_strategy="halt",
                    tool_schema_shortlisting_enabled=True,
                ),
                turn_input_queue=None,
                llm_api=None,
                skill_api=None,
            )
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
                    logger=SimpleNamespace(info=lambda *_a, **_k: None),
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
                event["payload"]["output"]["outputs"]
                for event in session_api.events
                if event["event_type"] == "tool.call.completed"
                and event["payload"]["call_id"] == "send"
            )
            operation_status = next(
                event["payload"]["output"]["outputs"]
                for event in session_api.events
                if event["event_type"] == "tool.call.completed"
                and event["payload"]["call_id"] == "operation-status"
            )
            preparation_digest_value = preparation_result["preparation_digest"]
            restart_context = SimpleNamespace(
                session_id=session_id,
                env=env,
                metadata=runtime_metadata,
            )
            restart_executor = _CommandExecutor(
                context=restart_context,
                transport=transport,
                research_payload=research_payload,
            )
            broadcasts_before_restart = sum(
                call.get("rpc_method") == "eth_sendRawTransaction"
                for call in transport.calls
            )
            restart_status = restart_executor._resolved_runtime_call(
                "blockchain.inspect",
                {
                    "action": "operation_status",
                    "preparation_digest": preparation_digest_value,
                },
            )
            broadcasts_after_restart = sum(
                call.get("rpc_method") == "eth_sendRawTransaction"
                for call in transport.calls
            )
            evidence = {
                "schema_version": "blockchain-autonomous-local-evidence-v1",
                "source_commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                ).strip(),
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
