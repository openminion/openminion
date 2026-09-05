from types import SimpleNamespace

from openminion.base.config.runtime.tools import (
    BlockchainToolRuntimeConfig,
    ToolRuntimeConfig,
)
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.modules.tool.base import ToolExecutionContext


def _config(enabled: bool):
    blockchain = (
        BlockchainToolRuntimeConfig(
            enabled=True,
            rpc_url="http://127.0.0.1:8545",
            chain_id=31337,
        )
        if enabled
        else None
    )
    return SimpleNamespace(
        runtime=SimpleNamespace(tools=ToolRuntimeConfig(blockchain=blockchain)),
        mcp_servers=None,
        tool_selection=None,
    )


def test_enabled_bootstrap_registers_exact_four_blockchain_tools(tmp_path) -> None:
    bootstrap = build_runtime_bootstrap(
        config=_config(True),
        workspace_root=tmp_path,
        run_root=tmp_path / "run",
        strict=False,
    )

    assert {
        name for name in bootstrap.registry.list() if name.startswith("blockchain.")
    } == {
        "blockchain.debug",
        "blockchain.inspect",
        "blockchain.prepare_transaction",
        "blockchain.send_transaction",
    }
    assert bootstrap.contract_drift_report is not None
    assert bootstrap.contract_drift_report.has_drift is False


def test_disabled_bootstrap_registers_no_blockchain_tools_and_no_drift(
    tmp_path,
) -> None:
    bootstrap = build_runtime_bootstrap(
        config=_config(False),
        workspace_root=tmp_path,
        run_root=tmp_path / "run",
        strict=False,
    )

    assert not {
        name for name in bootstrap.registry.list() if name.startswith("blockchain.")
    }
    assert bootstrap.contract_drift_report is not None
    assert bootstrap.contract_drift_report.has_drift is False


def test_direct_registry_send_is_rejected_before_handler(tmp_path) -> None:
    bootstrap = build_runtime_bootstrap(
        config=_config(True),
        workspace_root=tmp_path,
        run_root=tmp_path / "run",
        strict=False,
    )
    tool = bootstrap.registry.get("blockchain.send_transaction")

    result = execute_tool_spec_call(
        tool=tool,
        arguments={},
        context=ToolExecutionContext(
            channel="test",
            target="test",
            session_id="session",
        ),
    )

    assert result.ok is False
    assert result.data["error_code"] == "POLICY_MODE_UNSUPPORTED"
