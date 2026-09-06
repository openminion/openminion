from __future__ import annotations

from importlib import metadata

from openminion.modules.tool.contracts.dependencies import (
    ToolDependencyDecl,
    ToolDependencyProbeContext,
    ToolDependencySetupHint,
    ToolDependencyStatus,
)
from openminion.modules.tool.registry import ToolRegistry, ToolSpec

from .debug import debug_blockchain
from .debug_schemas import DebugArgs
from .runtime import inspect_blockchain, prepare_transaction, send_transaction
from .schemas import InspectArgs, PrepareArgs, SendTransactionArgs

BLOCKCHAIN_INSPECT_DESCRIPTION = (
    "Use for read-only contract functions supplied as a function ABI and arguments, "
    "including quotes and state. Also reads balance, bytecode, transaction, or "
    "receipt, one fact per call. When asked to verify a receipt, use action receipt "
    "even if a prior send returned receipt status. Use blockchain.debug only for raw "
    "calldata, reverts, or event decoding. Never signs or sends."
)
BLOCKCHAIN_DEBUG_DESCRIPTION = (
    "Simulate EVM calls and decode calldata, revert data, or events from one "
    "transaction receipt on the configured blockchain. Read-only; never signs or "
    "sends. Not for receipt status; use blockchain.inspect with action receipt."
)
BLOCKCHAIN_PREPARE_DESCRIPTION = (
    "Use when asked to prepare but not send an unsigned transaction or contract "
    "write. Accepts typed transaction fields or a function ABI and arguments, "
    "simulates the write, and returns a digest. Not for read-only quotes; never sends."
)
BLOCKCHAIN_SEND_DESCRIPTION = (
    "Use when asked to send a previously prepared transaction. Requires exact "
    "one-time operator approval, then revalidates, signs, and broadcasts once. "
    "Copy transaction, call_context, and preparation_digest exactly from the "
    "structured prepare result without rebuilding them from prose or changing "
    "JSON value types."
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
            name="blockchain.debug",
            args_model=DebugArgs,
            min_scope="READ_ONLY",
            handler=debug_blockchain,
            dangerous=False,
            idempotent=True,
            tags=("blockchain", "read_only", "debug"),
            capabilities=("blockchain", "read_only", "debug"),
            dependencies=(WEB3_DEPENDENCY,),
        )
    )
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
    "BLOCKCHAIN_DEBUG_DESCRIPTION",
    "BLOCKCHAIN_INSPECT_DESCRIPTION",
    "BLOCKCHAIN_PREPARE_DESCRIPTION",
    "BLOCKCHAIN_SEND_DESCRIPTION",
    "WEB3_DEPENDENCY",
    "register",
]
