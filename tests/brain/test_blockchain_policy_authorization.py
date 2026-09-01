import json
from types import SimpleNamespace

from openminion.base.config import ToolRuntimeConfig
from openminion.base.config.runtime.tools import BlockchainToolRuntimeConfig
from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.brain.loop.tools.confirmation import (
    confirmation_required_user_message,
)
from openminion.modules.brain.runner.tick.context import (
    _clear_pending_confirmation_metadata,
)
from openminion.modules.brain.schemas import ToolCommand
from openminion.modules.policy.adapters.brain import PolicyCtlBrainAdapter
from openminion.modules.policy.models import (
    PolicyConfig,
    RiskSpec,
    stable_invocation_hash,
)
from openminion.modules.policy.runtime.service import PolicyCtl
from openminion.modules.policy.storage.store import SQLitePolicyStore
from openminion.modules.tool.plugin_api import PolicyAuthorization
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.tools.blockchain.schemas import SendTransactionArgs


def _command() -> ToolCommand:
    return ToolCommand(
        kind="tool",
        title="Send transaction",
        tool_name="blockchain.send_transaction",
        args={"preparation_digest": "sha256:" + "1" * 64},
        inputs={},
        idempotency_key="invocation-1",
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


def test_pending_approval_is_cleared_with_confirmation_metadata() -> None:
    state = SimpleNamespace(
        pending_policy_approval_id="approval",
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


def test_blockchain_confirmation_renderer_offers_once_or_deny_only() -> None:
    message = confirmation_required_user_message(_command())

    assert "allow once" in message
    assert "no to cancel" in message
    assert "session" not in message


def test_tool_adapter_consumes_grant_for_exact_raw_invocation(tmp_path) -> None:
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
    args = {
        "transaction": json.dumps(transaction),
        "call_context": None,
        "preparation_digest": "sha256:" + "0" * 64,
    }
    expected_hash = stable_invocation_hash(
        tool="blockchain",
        method="send_transaction",
        args=args,
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
