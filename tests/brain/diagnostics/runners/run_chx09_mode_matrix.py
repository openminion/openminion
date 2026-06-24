#!/usr/bin/env python3
# ruff: noqa: E402
from __future__ import annotations

import tempfile
from pathlib import Path
import sys

OPENMINION_ROOT = Path(__file__).resolve().parents[4]
OPENMINION_SRC = OPENMINION_ROOT / "src"
if str(OPENMINION_SRC) not in sys.path:
    sys.path.insert(0, str(OPENMINION_SRC))

from openminion.modules.brain.adapters.a2a import LocalA2AAdapter  # noqa: E402
from openminion.modules.brain.adapters.context import (  # noqa: E402
    LocalContextAdapter,
)
from openminion.modules.brain.adapters.llm import LocalLLMAdapter  # noqa: E402
from openminion.modules.brain.adapters.memory import (  # noqa: E402
    LocalMemoryAdapter,
)
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.runner import (
    RunnerOptions,
    BrainRunner,
)
from openminion.modules.brain.schemas import (  # noqa: E402
    AgentBudgets,
    AgentProfile,
    BrainMode,
    BudgetCounters,
    ClarifyPolicy,
    LLMProfiles,
    WorkingState,
)


def _profile() -> AgentProfile:
    return AgentProfile(
        agent_id="mode-matrix",
        llm_profiles=LLMProfiles(
            decide_model="mock",
            plan_model="mock",
            reflect_model="mock",
            summarize_model="mock",
        ),
        budgets=AgentBudgets(
            max_ticks_per_user_turn=20,
            max_tool_calls=10,
            max_a2a_calls=2,
            max_total_llm_tokens=5000,
            max_elapsed_ms=60000,
        ),
    )


def _seed_state(session_id: str, mode: BrainMode) -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="mode-matrix",
        budgets_remaining=BudgetCounters(
            ticks=20, tool_calls=10, a2a_calls=2, tokens=5000, time_ms=60000
        ),
        mode=mode,
        policy=ClarifyPolicy.ASK_IF_AMBIGUOUS,
        trace_id=f"trace-{session_id}",
    )


def main() -> None:
    ambiguous = "what's weather today?"
    risky = 'tool rm {"path":"/tmp/demo"}'
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = LocalSessionStore(root / "sessions")
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=LocalContextAdapter(session_store=session),
            llm_api=LocalLLMAdapter(),
            tool_api=LocalToolAdapter(),
            a2a_api=LocalA2AAdapter(),
            memory_api=LocalMemoryAdapter(root / "memory"),
            policy_api=LocalPolicyAdapter(),
            options=RunnerOptions(metactl_enabled=False),
        )

        for mode in [
            BrainMode.COMMAND,
            BrainMode.GUIDED,
            BrainMode.AUTONOMOUS,
            BrainMode.BATCH,
        ]:
            amb_sid = f"amb-{mode.value}"
            session.put_working_state(
                amb_sid, state_inline=_seed_state(amb_sid, mode).model_dump(mode="json")
            )
            amb = runner.run(
                session_id=amb_sid, user_input=ambiguous, trace_id=f"trace-{amb_sid}"
            )

            risk_sid = f"risk-{mode.value}"
            session.put_working_state(
                risk_sid,
                state_inline=_seed_state(risk_sid, mode).model_dump(mode="json"),
            )
            risk = runner.run(
                session_id=risk_sid, user_input=risky, trace_id=f"trace-{risk_sid}"
            )

            print(
                f"{mode.value}|ambiguous|status={amb.status}|message={str(amb.message or '').strip()[:120]}"
            )
            print(
                f"{mode.value}|risky|status={risk.status}|message={str(risk.message or '').strip()[:120]}"
            )
