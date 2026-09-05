import json
from types import SimpleNamespace

import pytest

from openminion.base.config import ToolRuntimeConfig
from openminion.base.config.runtime.tools import BlockchainToolRuntimeConfig
from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.brain.adapters.tool.blockchain_authorization import (
    consume_blockchain_send_authorization,
)
from openminion.modules.brain.loop.adaptive.context import _AdaptiveLoopContextAdapter
from openminion.modules.brain.loop.tools.confirmation import (
    attach_confirmation_replay_queue,
    confirmation_replay_batch_size,
    confirmation_required_user_message,
)
from openminion.modules.brain.loop.tools.contracts import (
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
)
from openminion.modules.brain.loop.tools.iteration.execution import (
    execute_iteration_results,
)
from openminion.modules.brain.loop.tools.parallel import execute_parallel_tool_batch
from openminion.modules.brain.runner.delegates import _approve_delegate
from openminion.modules.brain.runner.tick.context import (
    _clear_pending_confirmation_metadata,
    _parse_confirmation_response,
)
from openminion.modules.brain.runner.tick.orchestrator import _capture_new_user_input
from openminion.modules.brain.schemas import ToolCommand, WorkingState
from openminion.modules.brain.tools.executor import RunnerCommandExecutor
from openminion.modules.llm.schemas import ToolCall
from openminion.modules.policy.adapters.brain import PolicyCtlBrainAdapter
from openminion.modules.policy.models import (
    PolicyConfig,
    RiskSpec,
    stable_invocation_hash,
)
from openminion.modules.policy.runtime.service import PolicyCtl
from openminion.modules.policy.storage.store import SQLitePolicyStore
from openminion.modules.tool.plugin_api import PolicyAuthorization
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.tools.blockchain.schemas import SendTransactionArgs
from openminion.tools.blockchain.confirmation import (
    build_blockchain_send_confirmation_preview,
)
from openminion.tools.blockchain.runtime import preparation_digest


def _command(*, recipient_byte: str = "22", nonce: int = 0) -> ToolCommand:
    transaction = {
        "schema_version": "evm-transaction-v1",
        "transaction_type": "eip1559",
        "chain_id": 31337,
        "from_address": "0x" + "11" * 20,
        "to_address": "0x" + recipient_byte * 20,
        "value_wei": "1",
        "nonce": str(nonce),
        "gas_limit": "21000",
        "data": "0x",
        "max_fee_per_gas_wei": "2",
        "max_priority_fee_per_gas_wei": "1",
        "max_total_fee_wei": "42000",
    }
    return ToolCommand(
        kind="tool",
        title="Send transaction",
        tool_name="blockchain.send_transaction",
        args={
            "transaction": transaction,
            "call_context": None,
            "preparation_digest": preparation_digest(transaction, None),
        },
        inputs={},
        idempotency_key=f"invocation-{nonce + 1}",
    )


class _BlockchainBatchLoopContext:
    prepared_parallel_dispatch_supported = True
    provider_retry_max_attempts = 0
    session_api = None

    def __init__(self, state, executor, finalizer):
        self.state = state
        self._executor = executor
        self._finalizer = finalizer
        self._logger = SimpleNamespace(emit=lambda *args, **kwargs: None)

    def prepare_tool_dispatch(self, *, command, include_reflect=False):
        return self._executor.prepare_tool_dispatch(
            state=self.state,
            command=command,
            logger=self._logger,
            include_reflect=include_reflect,
        )

    def execute_prepared_tool_dispatch(self, **_kwargs):
        raise AssertionError("confirmation batch must not execute a send")

    def finalize_tool_result(self, **_kwargs):
        raise AssertionError("confirmation batch must not finalize a send")

    def finalize_prepare_outcome(self, *, prepare_outcome):
        return self._finalizer.finalize_prepare_outcome(prepare_outcome=prepare_outcome)

    def emit_status(self, **_kwargs):
        pass


def _select_first_batch_result(loop_ctx, ordered_results):
    return execute_iteration_results(
        loop_ctx,
        profile=AdaptiveToolLoopProfile(
            profile_name="blockchain-confirmation-probe",
            mode_name="act",
            allowed_tools=frozenset({"blockchain.send_transaction"}),
        ),
        loop_state=AdaptiveToolLoopState(),
        runtime=None,
        model="probe",
        max_output_tokens=None,
        metadata=None,
        allowed_tools=frozenset({"blockchain.send_transaction"}),
        public_mode_tag="Act",
        signature="probe",
        ordered_tool_results=list(ordered_results),
        cached_indices=frozenset(),
        iter_batch_parallel_count=0,
        initial_batch_had_progress=False,
        loop_cache=SimpleNamespace(
            invalidate_for_write=lambda *args, **kwargs: None,
            put=lambda *args, **kwargs: None,
        ),
        loop_profiler=SimpleNamespace(record_tool_call=lambda *args, **kwargs: None),
        on_tool_result=None,
        iter_tool_records=[],
        append_tool_result_payload=lambda *args, **kwargs: None,
        set_turn_progress=lambda *args, **kwargs: None,
        effective_cap=lambda *args, **kwargs: 1,
        debit_tool_budget=lambda *args, **kwargs: None,
        profile_budget_exhausted=lambda *args, **kwargs: False,
        tool_budget_exhausted_for_answer_only=lambda *args, **kwargs: False,
        force_budget_answer_only_finalization=lambda *args, **kwargs: None,
        build_missing_action_result=lambda name: (_ for _ in ()).throw(
            AssertionError(name)
        ),
        build_tool_failure_recovery_message=lambda **kwargs: None,
        build_enrichment_message=lambda **kwargs: None,
        direct_tool_turn_active=lambda *args, **kwargs: False,
        trigger_macro_correction=lambda **kwargs: None,
        dispatch_correction_plan=lambda **kwargs: None,
    )


def test_policy_authorization_is_exact_and_not_command_carried() -> None:
    authorization = PolicyAuthorization(
        tool="blockchain",
        method="send_transaction",
        invocation_hash="hash",
        approval_id="approval",
        grant_id="grant",
        duration_type="once",
    )

    assert authorization.duration_type == "once"
    payload = _command().model_dump(mode="json")
    assert "policy_authorization" not in payload
    assert "approval_id" not in payload
    assert "grant_id" not in payload


def test_brain_adapter_copies_server_owned_approval_id(tmp_path) -> None:
    ctl = PolicyCtl(
        store=SQLitePolicyStore(tmp_path / "policy.sqlite"),
        config=PolicyConfig(mode="enforce"),
    )
    ctl.register_risk(
        "blockchain.send_transaction",
        RiskSpec(
            risk_class="financial",
            side_effects="external_account",
            reversibility="irreversible",
        ),
    )
    adapter = PolicyCtlBrainAdapter(ctl)
    state = SimpleNamespace(
        session_id="session",
        agent_id="agent",
        trace_id="trace",
        session_action_policy_mode_override=None,
    )

    decision = adapter.evaluate(
        command=_command(),
        working_state=state,
        session_context={"subject_id": "local", "mode_name": "act"},
    )

    assert decision.outcome == "REQUIRE_CONFIRMATION"
    assert decision.approval_id
    assert decision.confirmation_preview is not None
    assert decision.confirmation_preview.to_address.endswith("22" * 20)


def test_brain_adapter_denies_invalid_preview_without_pending_confirmation(
    tmp_path,
) -> None:
    ctl = PolicyCtl(
        store=SQLitePolicyStore(tmp_path / "policy.sqlite"),
        config=PolicyConfig(mode="enforce"),
    )
    ctl.register_risk(
        "blockchain.send_transaction",
        RiskSpec(risk_class="financial", side_effects="external_account"),
    )
    adapter = PolicyCtlBrainAdapter(ctl)
    command = _command().model_copy(deep=True)
    command.args["preparation_digest"] = "sha256:" + "0" * 64
    state = SimpleNamespace(
        session_id="session",
        agent_id="agent",
        trace_id="trace",
        session_action_policy_mode_override=None,
    )

    decision = adapter.evaluate(
        command=command,
        working_state=state,
        session_context={"subject_id": "local", "mode_name": "act"},
    )

    assert decision.outcome == "DENY"
    assert (
        decision.explanation
        == "Blockchain transaction approval preview could not be verified."
    )
    assert (
        ctl._store._record_store.query_dicts(
            "SELECT approval_id FROM policy_pending_confirmations"
        )
        == []
    )


def test_authorization_adapter_translates_invalid_preview_lineage(tmp_path) -> None:
    ctl = PolicyCtl(
        store=SQLitePolicyStore(tmp_path / "policy.sqlite"),
        config=PolicyConfig(mode="enforce"),
    )
    ctl.register_risk(
        "blockchain.send_transaction",
        RiskSpec(risk_class="financial", side_effects="external_account"),
    )
    adapter = PolicyCtlBrainAdapter(ctl)
    command = _command()
    state = SimpleNamespace(
        session_id="session",
        agent_id="agent",
        trace_id="trace",
        session_action_policy_mode_override=None,
    )
    decision = adapter.evaluate(
        command=command,
        working_state=state,
        session_context={"subject_id": "local", "mode_name": "act"},
    )
    grant_id = ctl.resolve_confirmation(decision.approval_id or "", "allow_once")
    ctl._store._record_store.execute_count(
        "UPDATE policy_pending_confirmations SET preview_json = '{}' "
        "WHERE approval_id = ?",
        (decision.approval_id,),
    )

    with pytest.raises(ToolRuntimeError) as captured:
        consume_blockchain_send_authorization(
            policy_ctl=ctl,
            permission_mode="default",
            args=command.args,
        )

    assert captured.value.code == "BLOCKCHAIN_CONFIRMATION_PREVIEW_INVALID"
    assert captured.value.details == {
        "stage": "authorization",
        "approval_id": decision.approval_id,
        "broadcast_attempted": False,
    }
    assert ctl._store.get_grant(grant_id or "").revoked_at is not None


def test_pending_approval_is_cleared_with_confirmation_metadata() -> None:
    state = SimpleNamespace(
        pending_policy_approval_id="approval",
        pending_policy_confirmation_preview=build_blockchain_send_confirmation_preview(
            _command().args
        ),
        pending_confirmation_sub_intents=[],
        pending_confirmation_sub_intent_refs=[],
        pending_confirmation_goal=None,
        pending_confirmation_last_user_input="",
        pending_confirmation_rationale="",
        pending_confirmation_success_criteria={},
        pending_confirmation_feasibility_state={},
        pending_confirmation_feasibility_report=None,
    )

    _clear_pending_confirmation_metadata(state)

    assert state.pending_policy_approval_id is None
    assert state.pending_policy_confirmation_preview is None


def test_blockchain_confirmation_renderer_shows_verified_preview() -> None:
    command = _command()
    preview = build_blockchain_send_confirmation_preview(command.args)
    message = confirmation_required_user_message(command, preview)

    assert "allow once" in message
    assert "no to cancel" in message
    assert "session" not in message
    assert f"To: {preview.to_address}" in message
    assert "Value (wei): 1" in message
    assert "Gas limit: 21000" in message
    assert f"Calldata SHA-256: {preview.calldata_sha256}" in message
    assert f"Preparation digest: {preview.preparation_digest}" in message


@pytest.mark.parametrize(
    "reply", ["y", "proceed", "go", "sure", "yes!", "session", "s"]
)
def test_blockchain_confirmation_rejects_non_exact_affirmations(reply: str) -> None:
    runner = SimpleNamespace(
        policy_api=SimpleNamespace(parse_confirmation_response=lambda _text: "affirm")
    )

    assert _parse_confirmation_response(runner, reply, _command()) == "unclear"
    assert _parse_confirmation_response(runner, "yes", _command()) == "affirm"
    assert _parse_confirmation_response(runner, "no", _command()) == "deny"


def test_blockchain_session_reply_is_captured_as_user_input() -> None:
    state = SimpleNamespace(
        pending_confirmation_command=_command(),
        last_user_input="",
        trace_id="",
    )
    runner = SimpleNamespace(
        policy_api=SimpleNamespace(parse_confirmation_response=lambda _text: "affirm")
    )

    _capture_new_user_input(
        runner,
        state,
        user_input="session",
        trace_id="trace-session-reply",
    )

    assert state.last_user_input == "session"


def test_blockchain_confirmation_never_covers_a_queued_batch() -> None:
    command = _command()
    queued = attach_confirmation_replay_queue(command, [_command()])
    message = confirmation_required_user_message(
        queued, build_blockchain_send_confirmation_preview(command.args)
    )

    assert confirmation_replay_batch_size(queued) == 1
    assert "queued" not in message
    assert "session" not in message


def test_two_blockchain_sends_bind_only_first_pending_approval(tmp_path) -> None:
    store = SQLitePolicyStore(tmp_path / "policy.sqlite")
    ctl = PolicyCtl(store=store, config=PolicyConfig(mode="enforce"))
    ctl.register_risk(
        "blockchain.send_transaction",
        RiskSpec(
            risk_class="financial",
            side_effects="external_account",
            reversibility="irreversible",
        ),
    )

    runner = SimpleNamespace(
        policy_api=PolicyCtlBrainAdapter(ctl),
        memory_api=None,
    )
    runner._approve = lambda *, state, command, logger: _approve_delegate(
        runner,
        state=state,
        command=command,
        logger=logger,
    )
    state = WorkingState(
        session_id="two-send-session",
        agent_id="two-send-agent",
        trace_id="two-send-trace",
        budgets_remaining={
            "ticks": 8,
            "tool_calls": 8,
            "a2a_calls": 0,
            "tokens": 100_000,
            "time_ms": 45_000,
        },
    )
    executor = RunnerCommandExecutor(runner)
    finalizer = object.__new__(_AdaptiveLoopContextAdapter)
    finalizer.state = state
    finalizer._runner = runner
    finalizer._intent_step_index = 0
    loop_ctx = _BlockchainBatchLoopContext(state, executor, finalizer)
    first_address = "0x" + "22" * 20

    batch = execute_parallel_tool_batch(
        loop_ctx=loop_ctx,
        tool_calls=[
            ToolCall(
                id="call-first",
                name="blockchain.send_transaction",
                arguments=_command().args,
            ),
            ToolCall(
                id="call-second",
                name="blockchain.send_transaction",
                arguments=_command(recipient_byte="33", nonce=1).args,
            ),
        ],
        include_reflect=False,
        provider_parallel_tool_capacity=1,
    )
    first_outcome = batch.ordered_results[0][1]
    second_outcome = batch.ordered_results[1][1]
    result = _select_first_batch_result(loop_ctx, batch.ordered_results)
    pending_rows = store._record_store.query_dicts(
        "SELECT approval_id, state FROM policy_pending_confirmations "
        "ORDER BY created_at"
    )

    assert result.outcome is not None
    assert result.outcome.termination_reason == "needs_user"
    assert f"To: {first_address}" in state.post_action_user_message
    assert state.pending_confirmation_command is not None
    assert state.pending_confirmation_command.command_id == "call-first"
    assert state.pending_confirmation_command.tool_name == (
        "blockchain.send_transaction"
    )
    assert (
        state.pending_confirmation_command.args["transaction"]["to_address"]
        == first_address
    )
    assert state.pending_policy_confirmation_preview is not None
    assert state.pending_policy_confirmation_preview.to_address == first_address
    assert state.pending_policy_approval_id == first_outcome.policy_approval_id
    assert confirmation_replay_batch_size(state.pending_confirmation_command) == 1
    assert second_outcome.policy_approval_id is None
    assert second_outcome.policy_confirmation_preview is None
    assert (
        second_outcome.action_result.summary
        == "This action requires a separate confirmation."
    )
    assert pending_rows == [
        {
            "approval_id": first_outcome.policy_approval_id,
            "state": "pending",
        }
    ]


def test_tool_adapter_consumes_grant_for_canonical_invocation(tmp_path) -> None:
    transaction = {
        "schema_version": "evm-transaction-v1",
        "transaction_type": "eip1559",
        "chain_id": 31337,
        "from_address": "0x" + "11" * 20,
        "to_address": "0x" + "22" * 20,
        "value_wei": "1",
        "nonce": "0",
        "gas_limit": "21000",
        "data": "0x",
        "max_fee_per_gas_wei": "2",
        "max_priority_fee_per_gas_wei": "1",
        "max_total_fee_wei": "42000",
    }
    canonical_args = {
        "transaction": transaction,
        "call_context": None,
        "preparation_digest": "sha256:" + "0" * 64,
    }
    args = {**canonical_args, "transaction": json.dumps(transaction)}
    expected_hash = stable_invocation_hash(
        tool="blockchain",
        method="send_transaction",
        args=SendTransactionArgs.model_validate(args).model_dump(mode="json"),
    )
    captured: dict[str, object] = {}

    def handler(_args, context):
        captured["authorization"] = context.policy_authorization
        return {"ok": True}

    class PolicyCtlStub:
        def mode(self) -> str:
            return "enforce"

        def resolve_matching_active_grant_for_use(self, **criteria):
            captured["criteria"] = criteria
            return SimpleNamespace(
                approval_id="approval",
                grant_id="grant",
            )

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="blockchain.send_transaction",
            args_model=SendTransactionArgs,
            min_scope="READ_ONLY",
            handler=handler,
            dangerous=False,
            idempotent=False,
        )
    )
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        runtime_config=SimpleNamespace(
            tools=ToolRuntimeConfig(
                blockchain=BlockchainToolRuntimeConfig(
                    enabled=True,
                    rpc_url="http://127.0.0.1:8545",
                    chain_id=31337,
                )
            )
        ),
        runtime_registry=registry,
        policy_ctl=PolicyCtlStub(),
    )

    result = adapter.execute(
        command={"tool_name": "blockchain.send_transaction", "args": args},
        session_id="session",
        trace_id="trace",
    )

    assert result["status"] == "success"
    assert captured["criteria"] == {
        "subject_id": "local",
        "tool": "blockchain",
        "method": "send_transaction",
        "invocation_hash": expected_hash,
    }
    assert captured["authorization"] == PolicyAuthorization(
        tool="blockchain",
        method="send_transaction",
        invocation_hash=expected_hash,
        approval_id="approval",
        grant_id="grant",
        duration_type="once",
    )
