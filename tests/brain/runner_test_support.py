from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable
from unittest.mock import MagicMock, patch

from pydantic import BaseModel

from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.meta import (
    BudgetAdjust,
    MetaDirective,
    MetaMetrics,
    MetaResult,
    MetaState,
)
from openminion.modules.brain.runner import RunnerOptions, BrainRunner
from openminion.modules.brain.schemas import (
    ActionResult,
    ActDecision,
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    BudgetCounters,
    Decision,
    DecisionAdapter,
    LLMProfiles,
    Plan,
    ReflectReport,
    RespondDecision,
    StepOutputEntry,
    ThinkCommand,
    ToolCommand,
    VerificationMode,
    WorkingState,
    build_intent_execution_states,
    build_sub_intent_id,
)


def build_seeded_act_decision(
    *,
    command: ToolCommand | Any,
    rationale: str = "",
    confidence: float = 1.0,
    reason_code: str = "test",
    act_profile: str = "general",
    execution_target: dict[str, Any] | None = None,
    **extra: Any,
) -> ActDecision:
    decision = ActDecision(
        confidence=confidence,
        reason_code=reason_code,
        act_profile=act_profile,
        execution_target=execution_target or {"kind": "local"},
        rationale=rationale,
        **extra,
    )
    decision._seeded_commands = [command]
    return decision


def _profile() -> AgentProfile:
    return AgentProfile(
        agent_id="router-agent",
        role="general",
        llm_profiles=LLMProfiles(
            decide_model="decide-default",
            plan_model="plan-default",
            act_model=None,
            reflect_model="reflect-default",
            summarize_model="summarize-default",
        ),
        budgets=AgentBudgets(
            max_ticks_per_user_turn=20,
            max_tool_calls=5,
            max_a2a_calls=5,
            max_total_llm_tokens=5000,
            max_elapsed_ms=120000,
        ),
        defaults=AgentDefaults(),
    )


__all__ = [
    "ActionResult",
    "ActDecision",
    "AgentBudgets",
    "AgentDefaults",
    "AgentProfile",
    "Any",
    "BaseModel",
    "BudgetAdjust",
    "BudgetCounters",
    "Decision",
    "DecisionAdapter",
    "LLMProfiles",
    "LocalA2AAdapter",
    "LocalContextAdapter",
    "LocalMemoryAdapter",
    "LocalPolicyAdapter",
    "LocalSessionStore",
    "LocalToolAdapter",
    "MagicMock",
    "MetaDirective",
    "MetaMetrics",
    "MetaResult",
    "MetaState",
    "Path",
    "Plan",
    "ReflectReport",
    "RespondDecision",
    "RunnerOptions",
    "SimpleNamespace",
    "BrainRunner",
    "StepOutputEntry",
    "ThinkCommand",
    "ToolCommand",
    "VerificationMode",
    "WorkingState",
    "build_seeded_act_decision",
    "_profile",
    "build_intent_execution_states",
    "build_sub_intent_id",
    "datetime",
    "patch",
    "tempfile",
    "unittest",
    "fake_session_api",
    "fake_logger",
    "fake_tool_api",
    "fake_context_builder",
    "fake_llm_client",
    "fake_context_service",
    "fake_context_pack",
    "fake_command_executor",
    "fake_bridge_llm_adapter",
    "fake_bridge_api",
    "fake_model_dump_message",
]


def fake_session_api() -> SimpleNamespace:
    return SimpleNamespace(
        list_events=lambda *_a, **_k: [],
        append_event=lambda *_a, **_k: None,
    )


def fake_logger() -> SimpleNamespace:
    return SimpleNamespace(emit=lambda *_a, **_k: None)


def fake_tool_api(tool_names: Iterable[str] = ()) -> SimpleNamespace:

    tools = {name: SimpleNamespace(name=name) for name in tuple(tool_names)}
    return SimpleNamespace(registry=SimpleNamespace(_tools=tools))


def fake_command_executor() -> SimpleNamespace:

    return SimpleNamespace(execute=lambda *_a, **_k: None)


def fake_context_builder(build_return: Any = None) -> SimpleNamespace:
    if build_return is None:
        build_return = {}
    return SimpleNamespace(build=lambda *_a, **_k: build_return)


def fake_bridge_llm_adapter(*, response: Any = None, side_effect: Any = None) -> Any:

    adapter = SimpleNamespace()
    adapter.client = MagicMock()
    if side_effect is not None:
        adapter.client.call.side_effect = side_effect
    else:
        adapter.client.call.return_value = response
    return adapter


def fake_bridge_api(
    *,
    return_values: dict[str, Any] | None = None,
    side_effects: dict[str, Any] | None = None,
    store: Any = None,
) -> Any:

    api = MagicMock()
    for name, value in (return_values or {}).items():
        getattr(api, name).return_value = value
    for name, value in (side_effects or {}).items():
        getattr(api, name).side_effect = value
    if store is not None:
        api.store = store
    return api


def fake_model_dump_message(payload: dict[str, Any]) -> Any:

    return SimpleNamespace(model_dump=lambda: payload)


def fake_llm_client(
    *,
    response: Any = None,
    responses: list[Any] | None = None,
) -> Any:
    client = MagicMock()
    if responses is not None:
        client.call.side_effect = list(responses)
    elif response is not None:
        client.call.return_value = response
    return client


def fake_context_service(
    *,
    pack: Any = None,
    packs: list[Any] | None = None,
) -> Any:
    svc = MagicMock()
    if packs is not None:
        svc.build_pack.side_effect = list(packs)
    elif pack is not None:
        svc.build_pack.return_value = pack
    return svc


def fake_context_pack(dump: dict[str, Any]) -> Any:
    pack = MagicMock()
    pack.model_dump.return_value = dump
    return pack
