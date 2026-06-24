from __future__ import annotations

import pytest
from typing import get_args
from pydantic import ValidationError


# ── AGF-01 ───────────────────────────────────────────────────────────────────


def test_declared_goal_in_memory_type() -> None:
    from openminion.modules.memory.models import MemoryType

    assert "declared_goal" in get_args(MemoryType)


# ── AGF-02 ───────────────────────────────────────────────────────────────────


def test_goal_declaration_validates_required_fields() -> None:
    from openminion.modules.brain.schemas import GoalDeclaration

    g = GoalDeclaration(
        goal="Monitor deployment health",
        trigger="Recent failure pattern",
        priority="medium",
        action_type="watch",
    )
    assert g.goal == "Monitor deployment health"
    assert g.trigger == "Recent failure pattern"
    assert g.priority == "medium"
    assert g.action_type == "watch"
    assert g.suggested_schedule is None


def test_goal_declaration_rejects_empty_goal() -> None:
    from openminion.modules.brain.schemas import GoalDeclaration

    with pytest.raises(ValidationError):
        GoalDeclaration(
            goal="",
            trigger="x",
            priority="medium",
            action_type="watch",
        )


def test_goal_declaration_rejects_empty_trigger() -> None:
    from openminion.modules.brain.schemas import GoalDeclaration

    with pytest.raises(ValidationError):
        GoalDeclaration(
            goal="x",
            trigger="",
            priority="medium",
            action_type="watch",
        )


def test_goal_declaration_rejects_extra_fields() -> None:
    from openminion.modules.brain.schemas import GoalDeclaration

    with pytest.raises(ValidationError):
        GoalDeclaration(
            goal="x",
            trigger="y",
            priority="medium",
            action_type="watch",
            unknown_field="boom",
        )


def test_decision_default_goal_declaration_is_none() -> None:
    from openminion.modules.brain.schemas import Decision

    d = Decision(route="respond", respond_kind="answer", confidence=0.5, answer="ok")
    assert d.goal_declaration is None


def test_decision_carries_goal_declaration_when_set() -> None:
    from openminion.modules.brain.schemas import Decision, GoalDeclaration

    g = GoalDeclaration(
        goal="X",
        trigger="Y",
        priority="low",
        action_type="suggest",
    )
    d = Decision(
        route="respond",
        respond_kind="answer",
        confidence=0.5,
        answer="ok",
        goal_declaration=g,
    )
    assert d.goal_declaration is g


def test_engine_extractor_pulls_typed_payload() -> None:
    from openminion.modules.brain.loop.tools.engine import (
        _goal_declaration_payload,
    )

    class _R:
        goal_declaration = {
            "goal": "x",
            "trigger": "y",
            "priority": "low",
            "action_type": "none",
        }

    extracted = _goal_declaration_payload(_R())
    assert extracted is not None
    assert extracted.goal == "x"
    assert extracted.action_type == "none"


def test_engine_extractor_returns_none_on_absence() -> None:
    from openminion.modules.brain.loop.tools.engine import (
        _goal_declaration_payload,
    )

    class _Empty:
        pass

    assert _goal_declaration_payload(_Empty()) is None


def test_engine_extractor_returns_none_on_invalid_payload() -> None:
    from openminion.modules.brain.loop.tools.engine import (
        _goal_declaration_payload,
    )

    class _Bad:
        goal_declaration = {"goal": "x"}  # missing trigger

    assert _goal_declaration_payload(_Bad()) is None


def test_outcome_dataclass_has_goal_declaration_field() -> None:
    from openminion.modules.brain.loop.tools.contracts import (
        AdaptiveToolLoopOutcome,
    )

    assert "goal_declaration" in AdaptiveToolLoopOutcome.__dataclass_fields__


def test_stage_declared_goal_callable() -> None:
    from openminion.modules.brain.runtime.memory import stage_declared_goal

    assert callable(stage_declared_goal)


def test_runtime_py_has_no_new_goal_declaration_regex() -> None:
    import re as _re
    from pathlib import Path

    runtime_py = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "openminion"
        / "modules"
        / "brain"
        / "loop"
        / "tools"
        / "runtime.py"
    )
    text = runtime_py.read_text(encoding="utf-8")
    # Named regex constant is forbidden.
    assert "_GOAL_DECLARATION_RE" not in text
    # Any `re.compile(...)` whose pattern body contains
    # `<goal_declaration>` (tag-shaped) is forbidden.
    bad_patterns = _re.findall(r"re\.compile\([^)]*goal_declaration[^)]*\)", text)
    assert not bad_patterns, (
        f"trailer regex compile call leaked into runtime.py: {bad_patterns}"
    )


# ── AGF-03 ───────────────────────────────────────────────────────────────────


def test_session_start_recall_includes_declared_goal() -> None:
    from openminion.modules.brain.adapters.context.bridges.memory import (
        _SESSION_START_RECALL_TYPES,
    )

    assert "declared_goal" in _SESSION_START_RECALL_TYPES


def test_retrieval_memory_type_tags_includes_declared_goal() -> None:
    from openminion.modules.retrieve.runtime.retrieval import _MEMORY_TYPE_TAGS

    assert "declared_goal" in _MEMORY_TYPE_TAGS


def test_retrieval_candidate_type_classifies_declared_goal() -> None:
    from openminion.modules.retrieve.runtime.retrieval import _candidate_type

    assert _candidate_type(["declared_goal", "priority:medium"]) == "declared_goal"
    assert _candidate_type(["unrelated_tag"]) == "fact"


# ── AGF-04 ───────────────────────────────────────────────────────────────────


def _minimal_profile_kwargs() -> dict:
    from openminion.modules.brain.schemas.agent import AgentBudgets, LLMProfiles

    return dict(
        agent_id="a",
        llm_profiles=LLMProfiles(
            decide_model="m",
            plan_model="m",
            act_model="m",
            reflect_model="m",
            summarize_model="m",
        ),
        budgets=AgentBudgets(
            max_ticks_per_user_turn=10,
            max_tool_calls=10,
            max_a2a_calls=0,
            max_total_llm_tokens=1000,
            max_elapsed_ms=10000,
        ),
    )


def test_agent_profile_default_goal_execution_policy_is_suggest() -> None:
    from openminion.modules.brain.schemas.agent import AgentProfile

    profile = AgentProfile(**_minimal_profile_kwargs())
    assert profile.goal_execution_policy == "suggest"


@pytest.mark.parametrize("policy", ["suggest", "auto_safe", "auto_full"])
def test_agent_profile_accepts_all_policy_literals(policy: str) -> None:
    from openminion.modules.brain.schemas.agent import AgentProfile

    profile = AgentProfile(**_minimal_profile_kwargs(), goal_execution_policy=policy)
    assert profile.goal_execution_policy == policy


def test_agent_profile_rejects_invalid_policy() -> None:
    from openminion.modules.brain.schemas.agent import AgentProfile

    with pytest.raises(ValidationError):
        AgentProfile(**_minimal_profile_kwargs(), goal_execution_policy="bogus")


# ── AGF-05 ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "policy,action,exp_allowed,exp_user,exp_reason",
    [
        # suggest blocks all
        ("suggest", "watch", False, True, "policy_suggest"),
        ("suggest", "task", False, True, "policy_suggest"),
        ("suggest", "suggest", False, True, "policy_suggest"),
        # auto_safe allows watch/task only
        ("auto_safe", "watch", True, False, "policy_auto_safe_watch_task"),
        ("auto_safe", "task", True, False, "policy_auto_safe_watch_task"),
        ("auto_safe", "suggest", False, True, "policy_auto_safe_non_watch_task"),
        # auto_full allows everything except action_type=none
        ("auto_full", "watch", True, False, "policy_auto_full"),
        ("auto_full", "task", True, False, "policy_auto_full"),
        ("auto_full", "suggest", True, False, "policy_auto_full"),
        # action_type=none always blocks regardless of policy
        ("auto_full", "none", False, False, "action_type_none"),
        ("auto_safe", "none", False, False, "action_type_none"),
        ("suggest", "none", False, False, "action_type_none"),
        # Bounded fallback for malformed input
        ("bogus", "watch", False, True, "policy_unknown_default_safe"),
        (None, "watch", False, True, "policy_suggest"),
        ("", "watch", False, True, "policy_suggest"),
    ],
)
def test_authorize_goal_action_matrix(
    policy: str,
    action: str,
    exp_allowed: bool,
    exp_user: bool,
    exp_reason: str,
) -> None:
    from openminion.modules.brain.runtime.goal.policy import authorize_goal_action

    result = authorize_goal_action(profile_policy=policy, action_type=action)
    assert result.allowed is exp_allowed
    assert result.requires_user_confirm is exp_user
    assert result.reason == exp_reason
    if exp_allowed and not exp_user:
        assert result.risk_tier == "silent"
    elif exp_user:
        assert result.risk_tier == "approve"
    else:
        assert result.risk_tier == "halt"


def test_render_policy_string_per_literal() -> None:
    from openminion.modules.brain.runtime.goal.policy import (
        render_goal_execution_policy,
    )

    class _P:
        def __init__(self, p: str | None) -> None:
            self.goal_execution_policy = p

    suggest = render_goal_execution_policy(_P("suggest"))
    assert "suggest" in suggest
    assert "ask the user" in suggest

    safe = render_goal_execution_policy(_P("auto_safe"))
    assert "auto_safe" in safe
    assert "read-only" in safe.lower()

    full = render_goal_execution_policy(_P("auto_full"))
    assert "auto_full" in full
    assert "WBW" in full


def test_render_policy_degrades_when_profile_absent() -> None:
    from openminion.modules.brain.runtime.goal.policy import (
        render_goal_execution_policy,
    )

    class _NoField:
        pass

    assert render_goal_execution_policy(None) == ""
    assert render_goal_execution_policy(_NoField()) == ""


# ── End-to-end seam: LLMResponse → normalizer → engine extractor ─────────────


def test_llm_response_carries_goal_declaration_field() -> None:
    from openminion.modules.llm.schemas import LLMResponse

    resp = LLMResponse(
        ok=True,
        provider="echo",
        model="m",
        goal_declaration={
            "goal": "X",
            "trigger": "Y",
            "priority": "low",
            "action_type": "watch",
        },
    )
    assert resp.goal_declaration is not None
    assert resp.goal_declaration["action_type"] == "watch"


def test_normalize_goal_declaration_response_round_trip() -> None:
    from openminion.modules.brain.loop.tools.runtime import (
        TYPED_SIGNAL_SOURCE_STRUCTURED_FIELD,
        _normalize_goal_declaration_response,
    )
    from openminion.modules.llm.schemas import LLMResponse

    resp = LLMResponse(
        ok=True,
        provider="echo",
        model="m",
        goal_declaration={
            "goal": "X",
            "trigger": "Y",
            "priority": "medium",
            "action_type": "task",
        },
    )
    normalized = _normalize_goal_declaration_response(resp)
    assert normalized.goal_declaration["goal"] == "X"
    assert normalized.goal_declaration["action_type"] == "task"
    # Typed-signal-source telemetry is stamped.
    sources = (normalized.telemetry or {}).get("typed_signal_sources") or {}
    assert sources.get("goal_declaration") == TYPED_SIGNAL_SOURCE_STRUCTURED_FIELD


def test_normalize_goal_declaration_response_passthrough_on_invalid() -> None:
    from openminion.modules.brain.loop.tools.runtime import (
        _normalize_goal_declaration_response,
    )
    from openminion.modules.llm.schemas import LLMResponse

    resp = LLMResponse(
        ok=True,
        provider="echo",
        model="m",
        goal_declaration={"goal": "x"},  # missing required `trigger`
    )
    normalized = _normalize_goal_declaration_response(resp)
    # Untouched — invalid payload returns as-is.
    assert normalized.goal_declaration == {"goal": "x"}
    # No source stamp.
    sources = (normalized.telemetry or {}).get("typed_signal_sources") or {}
    assert "goal_declaration" not in sources


def test_normalize_goal_declaration_response_passthrough_on_absence() -> None:
    from openminion.modules.brain.loop.tools.runtime import (
        _normalize_goal_declaration_response,
    )
    from openminion.modules.llm.schemas import LLMResponse

    resp = LLMResponse(ok=True, provider="echo", model="m")
    normalized = _normalize_goal_declaration_response(resp)
    assert normalized.goal_declaration is None


def test_normalize_goal_declaration_has_no_text_trailer_fallback() -> None:
    from openminion.modules.brain.loop.tools.runtime import (
        _normalize_goal_declaration_response,
    )
    from openminion.modules.llm.schemas import LLMResponse

    resp = LLMResponse(
        ok=True,
        provider="echo",
        model="m",
        output_text=(
            '<goal_declaration>{"goal":"X","trigger":"Y","'
            'priority":"low","action_type":"none"}</goal_declaration>'
        ),
    )
    normalized = _normalize_goal_declaration_response(resp)
    # Output text untouched (no trailer stripping).
    assert "<goal_declaration>" in normalized.output_text
    # Field still None (no trailer-to-field promotion).
    assert normalized.goal_declaration is None


# ── Live wiring: normalizer is in the runtime pipeline ───────────────────────


def test_normalize_goal_declaration_wired_into_runtime_pipeline() -> None:
    from pathlib import Path

    runtime_py = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "openminion"
        / "modules"
        / "brain"
        / "loop"
        / "tools"
        / "runtime.py"
    )
    text = runtime_py.read_text(encoding="utf-8")
    invocation_count = text.count("_normalize_goal_declaration_response(response)")
    assert invocation_count >= 2, (
        f"goal_declaration normalizer must be wired into both "
        f"`.call()` and `.complete()` response chains in runtime.py; "
        f"found {invocation_count} invocations"
    )


# ── Live wiring: prompt-context injection ────────────────────────────────────


def test_loop_setup_imports_render_goal_execution_policy() -> None:
    from pathlib import Path

    setup_py = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "openminion"
        / "modules"
        / "brain"
        / "loop"
        / "tools"
        / "iteration"
        / "setup.py"
    )
    text = setup_py.read_text(encoding="utf-8")
    assert "render_goal_execution_policy" in text, (
        "iteration/setup.py must import + invoke render_goal_execution_policy "
        "so the prompt context shows the model its execution policy"
    )
    # Goal-declaration guidance string must also be inserted
    # (parallel to _META_RULE_PREFERENCE_GUIDANCE pattern at line ~1411).
    assert "_GOAL_DECLARATION_GUIDANCE" in text


# ── Live wiring: adaptive loop calls authorize_goal_action ───────────────────


def test_adaptive_finalization_invokes_authorize_goal_action() -> None:
    from pathlib import Path

    finalization_py = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "openminion"
        / "modules"
        / "brain"
        / "loop"
        / "adaptive"
        / "finalization.py"
    )
    text = finalization_py.read_text(encoding="utf-8")
    assert "authorize_goal_action" in text, (
        "loop/adaptive/finalization.py must invoke authorize_goal_action so the "
        "operator policy resolves a verdict at goal-staging time"
    )
    # And the verdict must reach telemetry via known keys.
    assert "goal_declaration.policy_verdict" in text
    assert "goal_declaration.policy_allowed" in text


# agent_profile propagation through tool-execution context.


def test_runtime_context_accepts_agent_profile_field() -> None:
    import dataclasses
    from openminion.modules.tool.runtime import RuntimeContext

    fields = {f.name: f for f in dataclasses.fields(RuntimeContext)}
    assert "agent_profile" in fields, (
        "RuntimeContext must expose `agent_profile` (AGFAG-01) so "
        "action-creation surfaces can read goal_execution_policy"
    )
    assert fields["agent_profile"].default is None


def test_tool_adapter_constructor_accepts_agent_profile() -> None:
    import inspect
    from openminion.modules.brain.adapters.tool import ToolAdapter

    params = inspect.signature(ToolAdapter.__init__).parameters
    assert "agent_profile" in params, (
        "ToolAdapter must accept `agent_profile` (AGFAG-04 propagation)"
    )
    assert params["agent_profile"].default is None


def test_tool_adapter_threads_agent_profile_into_runtime_context(tmp_path) -> None:
    from pathlib import Path
    from openminion.modules.brain.adapters.tool import ToolAdapter

    sentinel = {"goal_execution_policy": "auto_safe", "name": "agf-test"}
    adapter = ToolAdapter(
        workspace_root=Path(tmp_path),
        agent_profile=sentinel,
    )
    # Stash is the pre-condition for the build site to forward the
    # profile into RuntimeContext (verified by source-shape check
    # below).
    assert adapter.agent_profile is sentinel


def test_tool_adapter_runtime_context_build_passes_agent_profile() -> None:
    from pathlib import Path

    runtime_py = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "openminion"
        / "modules"
        / "brain"
        / "adapters"
        / "tool"
        / "runtime.py"
    )
    text = runtime_py.read_text(encoding="utf-8")
    assert "agent_profile=self.agent_profile" in text, (
        "ToolAdapter.run must thread self.agent_profile into "
        "RuntimeContext (AGFAG-04). Without this the field stays "
        "None at every handler invocation and watch/cron gates "
        "(AGFAG-02 / AGFAG-03) never fire."
    )


def test_factory_create_tool_adapter_forwards_agent_profile() -> None:
    import inspect
    from openminion.modules.brain.adapters.factory.tool import create_tool_adapter

    params = inspect.signature(create_tool_adapter).parameters
    assert "agent_profile" in params


def test_service_create_tool_api_forwards_agent_profile() -> None:
    import inspect
    from openminion.services.brain.factory.adapter import create_tool_api

    params = inspect.signature(create_tool_api).parameters
    assert "agent_profile" in params


def test_bootstrap_passes_default_profile_into_create_tool_api() -> None:
    from pathlib import Path

    bootstrap_py = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "openminion"
        / "services"
        / "runtime"
        / "bootstrap.py"
    )
    text = bootstrap_py.read_text(encoding="utf-8")
    assert "agent_profile=default_profile" in text, (
        "bootstrap.py must pass agent_profile=default_profile into "
        "create_tool_api (AGFAG-04). Without this the runner's "
        "profile never reaches tool-execution context."
    )


def test_brain_cli_passes_profile_into_create_tool_adapter() -> None:
    from pathlib import Path

    cli_py = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "openminion"
        / "modules"
        / "brain"
        / "cli.py"
    )
    text = cli_py.read_text(encoding="utf-8")
    assert "agent_profile=profile" in text, (
        "brain/cli.py must pass agent_profile=profile into "
        "create_tool_adapter (AGFAG-04)."
    )
