from __future__ import annotations


from openminion.modules.brain.bootstrap.route_catalog import (
    available_routes,
    decision_route_descriptions,
    get_route_descriptor,
)
from openminion.modules.brain.loop.strategies.coding import CodingMode
from openminion.modules.brain.loop.strategies.coding.contracts import (
    CODING_V1_ALLOWED_TOOLS,
)
from openminion.modules.brain.execution.workflow import WorkflowMode


def test_coding_mode_is_not_registered_in_default_registry() -> None:
    assert get_route_descriptor("coding") is None
    assert available_routes() == ["act", "respond"]


def test_coding_mode_handler_is_directly_instantiable() -> None:
    handler = CodingMode()
    assert isinstance(handler, WorkflowMode)
    assert isinstance(handler, CodingMode)


def test_coding_mode_class_contract_remains_stable() -> None:
    assert CodingMode.mode_name == "act_profile_coding"
    assert CodingMode.has_prepare is True
    assert CodingMode.has_validate is False
    assert CodingMode.has_resume is True
    assert CodingMode.priority_hint == 58
    assert CodingMode.default_config.get("max_depth") == 1
    assert CodingMode.decision_payload_fields == {}


def test_public_decision_descriptions_keep_coding_guidance_under_act() -> None:
    from tests.brain.runner_test_support import _profile as base_profile

    descriptions = decision_route_descriptions(base_profile())

    assert "coding" not in descriptions
    assert "act" in descriptions
    assert "coding" in descriptions["act"].lower()


def test_coding_mode_v1_allowlist_is_frozen_set() -> None:
    assert isinstance(CODING_V1_ALLOWED_TOOLS, frozenset)
    assert len(CODING_V1_ALLOWED_TOOLS) == 15
