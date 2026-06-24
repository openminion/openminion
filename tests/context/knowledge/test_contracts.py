from __future__ import annotations

from openminion.modules.context.knowledge.contracts import (
    KNOWLEDGE_GRAPH_CONTRACT_VERSION,
)
from openminion.modules.context.knowledge.interfaces import (
    KNOWLEDGE_GRAPH_INTERFACE_VERSION,
)


def test_contract_version_is_v1():
    assert KNOWLEDGE_GRAPH_CONTRACT_VERSION == "v1"


def test_interface_version_matches_contract_version():
    assert KNOWLEDGE_GRAPH_INTERFACE_VERSION == KNOWLEDGE_GRAPH_CONTRACT_VERSION
