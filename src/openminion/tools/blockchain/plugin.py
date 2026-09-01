from __future__ import annotations

from importlib import metadata

from openminion.modules.tool.contracts.dependencies import (
    ToolDependencyDecl,
    ToolDependencyProbeContext,
    ToolDependencySetupHint,
    ToolDependencyStatus,
)
from openminion.modules.tool.registry import ToolRegistry, ToolSpec

from .runtime import inspect_blockchain, prepare_transaction, send_transaction
from .schemas import InspectArgs, PrepareArgs, SendTransactionArgs

BLOCKCHAIN_INSPECT_DESCRIPTION = (
    "Inspect the configured Ethereum/EVM blockchain for chain summary, wallet "
    "balance, bytecode, smart-contract reads, transactions, or receipts. "
    "Read-only; never signs or sends."
)
BLOCKCHAIN_PREPARE_DESCRIPTION = (
    "Prepare and simulate an unsigned Ethereum/EVM transaction using the "
    "configured RPC and wallet signer. Returns canonical transaction fields "
    "and a digest; never signs or broadcasts."
)
BLOCKCHAIN_SEND_DESCRIPTION = (
    "Send one previously prepared Ethereum/EVM transaction after exact one-time "
    "financial approval. Copy transaction, call_context, and preparation_digest "
    "from the prepare result; call_context is null for native transfers and raw "
    "calls. Revalidates, signs, broadcasts once, and returns receipt state."
)

_WEB3_SETUP_HINT = ToolDependencySetupHint(
    platform="any",
    label="Install OpenMinion blockchain support",
    command=("python", "-m", "pip", "install", "openminion[blockchain]"),
    official_url="https://web3py.readthedocs.io/en/stable/quickstart.html",
    note="Installs the optional Web3.py dependency.",
)


def _probe_web3(
    _context: ToolDependencyProbeContext,
) -> ToolDependencyStatus:
    try:
        version = metadata.version("web3")
    except metadata.PackageNotFoundError:
        return ToolDependencyStatus(
            dependency_id="python:web3",
            state="missing",
            reason_code="python_package_not_found",
            message="python:web3 is not available",
            setup_hints=(_WEB3_SETUP_HINT,),
        )
    return ToolDependencyStatus(
        dependency_id="python:web3",
        state="ready",
        version=version,
        message="python:web3 is ready",
    )


WEB3_DEPENDENCY = ToolDependencyDecl(
    dependency_id="python:web3",
    probe=_probe_web3,
    preflight=_probe_web3,
    setup_hints=(_WEB3_SETUP_HINT,),
)


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="blockchain.inspect",
            args_model=InspectArgs,
            min_scope="READ_ONLY",
            handler=inspect_blockchain,
            dangerous=False,
            idempotent=True,
            tags=("blockchain", "read_only"),
            capabilities=("blockchain", "read_only"),
            dependencies=(WEB3_DEPENDENCY,),
        )
    )
    registry.register(
        ToolSpec(
            name="blockchain.prepare_transaction",
            args_model=PrepareArgs,
            min_scope="READ_ONLY",
            handler=prepare_transaction,
            dangerous=False,
            idempotent=True,
            tags=("blockchain", "transaction"),
            capabilities=("blockchain", "transaction"),
            dependencies=(WEB3_DEPENDENCY,),
        )
    )
    registry.register(
        ToolSpec(
            name="blockchain.send_transaction",
            args_model=SendTransactionArgs,
            min_scope="POWER_USER",
            handler=send_transaction,
            dangerous=True,
            idempotent=False,
            tags=("blockchain", "transaction", "financial"),
            capabilities=("blockchain", "transaction", "financial"),
            dependencies=(WEB3_DEPENDENCY,),
        )
    )


__all__ = [
    "BLOCKCHAIN_INSPECT_DESCRIPTION",
    "BLOCKCHAIN_PREPARE_DESCRIPTION",
    "BLOCKCHAIN_SEND_DESCRIPTION",
    "WEB3_DEPENDENCY",
    "register",
]
