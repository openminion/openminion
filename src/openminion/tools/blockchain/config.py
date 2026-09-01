from __future__ import annotations

from typing import Any

from openminion.base.config.runtime.tools import BlockchainToolRuntimeConfig
from openminion.modules.tool import resolve_runtime_tool_config


def resolve_blockchain_config(context: Any | None) -> BlockchainToolRuntimeConfig:
    return resolve_runtime_tool_config(context).blockchain or BlockchainToolRuntimeConfig()


__all__ = ["resolve_blockchain_config"]
