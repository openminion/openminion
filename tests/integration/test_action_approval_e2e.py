from __future__ import annotations

from openminion.modules.brain.runtime.approval import (
    ActionApprovalConfig,
    ApprovalCriteria,
    ApprovalCriteriaRegistry,
    ApprovalVerdict,
    LLMActionApprovalVerifier,
    default_criteria_registry,
    gate_destructive_action,
)


class _StubClient:
    def __init__(self, decision: str, rationale: str = ""):
        self.decision = decision
        self.rationale = rationale
        self.last_prompt = None

    def call(self, *, prompt, timeout_seconds):
        self.last_prompt = prompt
        return {"decision": self.decision, "rationale": self.rationale or self.decision}


class _RaisingTimeoutClient:
    def call(self, *, prompt, timeout_seconds):
        raise TimeoutError("model timeout")


class _RaisingErrorClient:
    def call(self, *, prompt, timeout_seconds):
        raise RuntimeError("model boom")


def _criteria_for_git_reset_hard():
    registry = default_criteria_registry()
    return registry.get("git", "reset_hard")


# --- VGD-01 protocol + ApprovalVerdict ---


def test_approval_verdict_is_frozen():
    verdict = ApprovalVerdict(decision="approve", rationale="ok")
    import pytest

    with pytest.raises(Exception):
        verdict.decision = "reject"  # type: ignore[misc]


def test_approval_criteria_is_frozen_and_carries_text():
    criteria = ApprovalCriteria(
        tool_id="git", action="reset_hard", criteria_text="rules"
    )
    assert criteria.tool_id == "git"
    assert criteria.criteria_text == "rules"


# --- VGD-04 criteria registry ---


def test_default_criteria_registry_loads_all_four_ngt04_criteria():
    registry = default_criteria_registry()
    for action in ("reset_hard", "branch_force_delete", "stash_drop", "stash_clear"):
        criteria = registry.get("git", action)
        assert criteria is not None
        assert len(criteria.criteria_text) > 0


def test_registry_returns_none_for_unknown_action():
    registry = ApprovalCriteriaRegistry()
    assert registry.get("git", "totally_made_up") is None


# --- VGD-02 LLM verifier impl ---


def test_llm_returns_typed_approve_verdict():
    verifier = LLMActionApprovalVerifier(client=_StubClient("approve"))
    verdict = verifier.verify(
        action={"tool_id": "git", "args": {}},
        state={},
        criteria=_criteria_for_git_reset_hard(),
    )
    assert verdict.decision == "approve"
    assert verdict.model == "claude-haiku-3.5"


def test_llm_returns_typed_reject_verdict():
    verifier = LLMActionApprovalVerifier(
        client=_StubClient("reject", rationale="no rationale given")
    )
    verdict = verifier.verify(
        action={}, state={}, criteria=_criteria_for_git_reset_hard()
    )
    assert verdict.decision == "reject"
    assert verdict.rationale == "no rationale given"


def test_llm_returns_typed_escalate_verdict():
    verifier = LLMActionApprovalVerifier(
        client=_StubClient("escalate", rationale="affects main")
    )
    verdict = verifier.verify(
        action={}, state={}, criteria=_criteria_for_git_reset_hard()
    )
    assert verdict.decision == "escalate"


def test_llm_hard_timeout_escalates_by_default():
    verifier = LLMActionApprovalVerifier(client=_RaisingTimeoutClient())
    verdict = verifier.verify(
        action={}, state={}, criteria=_criteria_for_git_reset_hard()
    )
    assert verdict.decision == "escalate"
    assert verdict.rationale == "verifier_timeout_escalate"


def test_llm_timeout_rejects_when_configured():
    verifier = LLMActionApprovalVerifier(
        client=_RaisingTimeoutClient(), escalate_on_timeout=False
    )
    verdict = verifier.verify(
        action={}, state={}, criteria=_criteria_for_git_reset_hard()
    )
    assert verdict.decision == "reject"


def test_llm_unknown_error_escalates():
    verifier = LLMActionApprovalVerifier(client=_RaisingErrorClient())
    verdict = verifier.verify(
        action={}, state={}, criteria=_criteria_for_git_reset_hard()
    )
    assert verdict.decision == "escalate"
    assert "verifier_error" in verdict.rationale


def test_llm_normalizes_invalid_decision_to_escalate():
    verifier = LLMActionApprovalVerifier(client=_StubClient("yes-please"))
    verdict = verifier.verify(
        action={}, state={}, criteria=_criteria_for_git_reset_hard()
    )
    assert verdict.decision == "escalate"


# --- VGD-03 + VGD-05 gate helper + config ---


def test_gate_disabled_short_circuits_to_approve():

    class _UnusedVerifier:
        def verify(self, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("verifier called when gate disabled")

    verdict = gate_destructive_action(
        tool_id="git",
        action="reset_hard",
        action_args={"target": "HEAD"},
        state={},
        verifier=_UnusedVerifier(),
        config=ActionApprovalConfig(enabled=False),
    )
    assert verdict.decision == "approve"
    assert verdict.rationale == "action_approval_verifier_disabled"


def test_gate_with_missing_criteria_escalates():
    verifier = LLMActionApprovalVerifier(client=_StubClient("approve"))
    registry = ApprovalCriteriaRegistry()
    verdict = gate_destructive_action(
        tool_id="git",
        action="unknown_action",
        action_args={},
        state={},
        verifier=verifier,
        registry=registry,
        config=ActionApprovalConfig(enabled=True),
    )
    assert verdict.decision == "escalate"
    assert verdict.rationale == "missing_approval_criteria"


# --- VGD-06 three-branch integration ---


def test_three_branch_gate_flow_against_stub_verifier_approve():
    verifier = LLMActionApprovalVerifier(client=_StubClient("approve"))
    verdict = gate_destructive_action(
        tool_id="git",
        action="reset_hard",
        action_args={"target": "HEAD~1"},
        state={"session_id": "s1"},
        verifier=verifier,
        config=ActionApprovalConfig(enabled=True),
    )
    assert verdict.decision == "approve"


def test_three_branch_gate_flow_against_stub_verifier_reject():
    verifier = LLMActionApprovalVerifier(
        client=_StubClient("reject", rationale="uncommitted_work_loss")
    )
    verdict = gate_destructive_action(
        tool_id="git",
        action="reset_hard",
        action_args={"target": "HEAD~5"},
        state={},
        verifier=verifier,
        config=ActionApprovalConfig(enabled=True),
    )
    assert verdict.decision == "reject"
    # Plugins consume `details["verifier_rationale"]` from this.
    assert verdict.rationale == "uncommitted_work_loss"


def test_three_branch_gate_flow_against_stub_verifier_escalate():
    verifier = LLMActionApprovalVerifier(
        client=_StubClient("escalate", rationale="affects_default_branch")
    )
    verdict = gate_destructive_action(
        tool_id="git",
        action="reset_hard",
        action_args={"target": "main"},
        state={},
        verifier=verifier,
        config=ActionApprovalConfig(enabled=True),
    )
    assert verdict.decision == "escalate"
    # Plugin would fall through to existing pending_approval_decision path.
