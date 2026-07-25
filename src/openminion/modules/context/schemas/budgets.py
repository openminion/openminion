# ruff: noqa: F403,F405
from .common import *


class ContextBudgets(BaseModel):
    total_max_tokens: int = Field(ge=1)
    identity_tokens: int = Field(ge=1)
    summary_tokens: int = Field(ge=1)
    conversation_summary_tokens: int = Field(default=0, ge=0)
    active_plan_tokens: int = Field(default=0, ge=0)
    task_digest_tokens: int = Field(default=0, ge=0)
    trailer_feedback_tokens: int = Field(default=0, ge=0)
    recent_turn_tokens: int = Field(ge=1)
    facts_tokens: int = Field(ge=0)
    memory_tokens: int = Field(ge=0)
    skills_tokens: int = Field(ge=0)
    artifact_tokens: int = Field(ge=0)
    instructions_tokens: int = Field(ge=1)


_CONTEXT_BUDGET_PRESETS: dict[str, ContextBudgets] = {
    "decide": ContextBudgets(
        total_max_tokens=2200,
        identity_tokens=160,
        summary_tokens=300,
        conversation_summary_tokens=300,
        active_plan_tokens=400,
        task_digest_tokens=240,
        trailer_feedback_tokens=200,
        recent_turn_tokens=1400,
        facts_tokens=150,
        memory_tokens=150,
        skills_tokens=120,
        artifact_tokens=50,
        instructions_tokens=80,
    ),
    "plan": ContextBudgets(
        total_max_tokens=3500,
        identity_tokens=220,
        summary_tokens=350,
        conversation_summary_tokens=0,
        active_plan_tokens=0,
        task_digest_tokens=0,
        recent_turn_tokens=1000,
        facts_tokens=250,
        memory_tokens=700,
        skills_tokens=250,
        artifact_tokens=600,
        instructions_tokens=130,
    ),
    "act": ContextBudgets(
        total_max_tokens=1800,
        identity_tokens=180,
        summary_tokens=250,
        conversation_summary_tokens=0,
        active_plan_tokens=0,
        task_digest_tokens=0,
        recent_turn_tokens=650,
        facts_tokens=150,
        memory_tokens=250,
        skills_tokens=250,
        artifact_tokens=200,
        instructions_tokens=120,
    ),
    "reflect": ContextBudgets(
        total_max_tokens=2800,
        identity_tokens=220,
        summary_tokens=300,
        conversation_summary_tokens=0,
        active_plan_tokens=0,
        task_digest_tokens=0,
        recent_turn_tokens=700,
        facts_tokens=200,
        memory_tokens=900,
        skills_tokens=80,
        artifact_tokens=400,
        instructions_tokens=120,
    ),
    "judge": ContextBudgets(
        total_max_tokens=3000,
        identity_tokens=200,
        summary_tokens=250,
        conversation_summary_tokens=0,
        active_plan_tokens=0,
        task_digest_tokens=0,
        recent_turn_tokens=600,
        facts_tokens=500,
        memory_tokens=400,
        skills_tokens=80,
        artifact_tokens=700,
        instructions_tokens=120,
    ),
    "validate": ContextBudgets(
        total_max_tokens=3000,
        identity_tokens=200,
        summary_tokens=250,
        conversation_summary_tokens=0,
        active_plan_tokens=0,
        task_digest_tokens=0,
        recent_turn_tokens=600,
        facts_tokens=500,
        memory_tokens=400,
        skills_tokens=80,
        artifact_tokens=700,
        instructions_tokens=120,
    ),
    "summarize": ContextBudgets(
        total_max_tokens=2200,
        identity_tokens=180,
        summary_tokens=300,
        conversation_summary_tokens=0,
        active_plan_tokens=0,
        task_digest_tokens=0,
        recent_turn_tokens=750,
        facts_tokens=220,
        memory_tokens=420,
        skills_tokens=60,
        artifact_tokens=180,
        instructions_tokens=90,
    ),
    "chat": ContextBudgets(
        total_max_tokens=1600,
        identity_tokens=150,
        summary_tokens=220,
        conversation_summary_tokens=0,
        active_plan_tokens=0,
        task_digest_tokens=0,
        recent_turn_tokens=700,
        facts_tokens=120,
        memory_tokens=200,
        skills_tokens=40,
        artifact_tokens=70,
        instructions_tokens=100,
    ),
}


def default_budgets_for(purpose: Purpose) -> ContextBudgets:
    return _CONTEXT_BUDGET_PRESETS[purpose].model_copy(deep=True)


def decide_budget_for_turn_depth(turn_count: int) -> ContextBudgets:
    """Return decide budgets keyed only by canonical session turn depth."""
    safe_count = max(0, int(turn_count))
    if safe_count <= 2:
        return ContextBudgets(
            total_max_tokens=1500,
            identity_tokens=160,
            summary_tokens=300,
            conversation_summary_tokens=0,
            active_plan_tokens=400,
            task_digest_tokens=240,
            recent_turn_tokens=1000,
            facts_tokens=150,
            memory_tokens=150,
            skills_tokens=120,
            artifact_tokens=50,
            instructions_tokens=80,
        )
    if safe_count <= 5:
        return default_budgets_for("decide")
    if safe_count <= 10:
        return ContextBudgets(
            total_max_tokens=2800,
            identity_tokens=160,
            summary_tokens=300,
            conversation_summary_tokens=500,
            active_plan_tokens=400,
            task_digest_tokens=240,
            recent_turn_tokens=1600,
            facts_tokens=150,
            memory_tokens=150,
            skills_tokens=120,
            artifact_tokens=50,
            instructions_tokens=80,
        )
    return ContextBudgets(
        total_max_tokens=3200,
        identity_tokens=160,
        summary_tokens=300,
        conversation_summary_tokens=800,
        active_plan_tokens=400,
        task_digest_tokens=240,
        recent_turn_tokens=1600,
        facts_tokens=150,
        memory_tokens=150,
        skills_tokens=120,
        artifact_tokens=50,
        instructions_tokens=80,
    )


BUCKET_TOKEN_FRACTIONS: dict[str, float] = {
    "static_prefix": 0.15,
    "mission_snapshot": 0.10,
    "summaries": 0.12,
    "conversation_summary": 0.12,
    "active_plan": 0.12,
    "task_digest": 0.08,
    "self_awareness": 0.05,
    "recent_window": 0.30,
    "retrieval": 0.15,
    "evidence_refs": 0.12,
    "turn_input": 0.06,
}


def bucket_caps_for(budgets: ContextBudgets) -> Dict[str, int]:
    total = budgets.total_max_tokens
    return {
        "static_prefix": budgets.identity_tokens + budgets.instructions_tokens,
        "mission_snapshot": max(
            64, int(total * BUCKET_TOKEN_FRACTIONS["mission_snapshot"])
        ),
        "summaries": budgets.summary_tokens,
        "conversation_summary": budgets.conversation_summary_tokens,
        "active_plan": budgets.active_plan_tokens,
        "task_digest": budgets.task_digest_tokens,
        "self_awareness": max(
            64, int(total * BUCKET_TOKEN_FRACTIONS["self_awareness"])
        ),
        "recent_window": budgets.recent_turn_tokens,
        "memory": budgets.memory_tokens,
        "retrieval": budgets.facts_tokens
        + budgets.memory_tokens
        + budgets.skills_tokens,
        "evidence_refs": budgets.artifact_tokens,
        "turn_input": max(64, int(total * BUCKET_TOKEN_FRACTIONS["turn_input"])),
    }
