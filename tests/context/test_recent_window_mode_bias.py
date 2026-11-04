"""Regression tests for issue #482: recent-window mode bias applied twice.

The mode adjustment (respond halving / act -120) is owned by
``_apply_mode_budget_bias`` and must NOT be re-applied during recent-window
trimming. These tests exercise real pack assembly (``ContextCtlService.build_pack``)
with nonempty recent history so retention and ordering are observed end to end.
"""

from __future__ import annotations

from openminion.modules.context.schemas import (
    BuildConstraints,
    BuildPackRequest,
    ContextBudgets,
    IdentitySnippet,
    SessionSlice,
    SessionTurn,
)
from openminion.modules.context.service import ContextCtlService


class _IdentityClient:
    contract_version = "v1"

    def render(
        self,
        *,
        agent_id: str,
        purpose: str,
        max_tokens: int,
        provider_pref: str | None = None,
        query_text: str | None = None,
    ) -> IdentitySnippet:
        del purpose, max_tokens, provider_pref, query_text
        return IdentitySnippet(
            agent_id=agent_id,
            profile_version="prof:v1",
            render_version="rend:v1",
            text=f"Identity for {agent_id}",
        )


class _SessionClient:
    contract_version = "v1"

    def __init__(self, turns: list[SessionTurn]) -> None:
        self._turns = list(turns)

    def get_slice(self, *, session_id, purpose, limits) -> SessionSlice:
        del purpose, limits
        return SessionSlice(
            session_id=session_id,
            slice_version="slice:v1",
            summary_short="summary",
            recent_turns=list(self._turns),
        )


class _MemoryClient:
    contract_version = "v1"

    def query_facts(self, *, session_id, agent_id, query, limit, mode_name=None):
        del session_id, agent_id, query, limit, mode_name
        return []

    def query_memory_cards(self, *, session_id, agent_id, query, limit, mode_name=None):
        del session_id, agent_id, query, limit, mode_name
        return []

    def recall_session_start_memory(
        self, *, session_id, agent_id, query, turn_index, limit, mode_name=None
    ):
        del session_id, agent_id, query, turn_index, limit, mode_name
        return []

    def recall_mid_session_memory(self, **kwargs):
        del kwargs
        return []

    def recall_recent_session_artifacts(self, **kwargs):
        del kwargs
        return []

    def get_procedure(self, *, procedure_id):
        del procedure_id
        return None


class _ArtifactClient:
    contract_version = "v1"

    def query_digests(self, *, session_id, agent_id, query, limit):
        del session_id, agent_id, query, limit
        return []


def _service(turns: list[SessionTurn]) -> ContextCtlService:
    return ContextCtlService(
        identityctl=_IdentityClient(),
        sessctl=_SessionClient(turns),
        memctl=_MemoryClient(),
        artifactctl=_ArtifactClient(),
    )


def _pad(text: str, target_chars: int = 200) -> str:
    # 200 chars -> 50 tokens under the 4:1 token heuristic.
    return text + "x" * max(0, target_chars - len(text))


def _assistant_turn(idx: int) -> SessionTurn:
    return SessionTurn(
        turn_id=f"turn-{idx}",
        role="assistant",
        content=_pad(f"assistant-{idx:02d}:"),
    )


def _assistant_turns(count: int) -> list[SessionTurn]:
    return [_assistant_turn(idx) for idx in range(count)]


def _override(
    *, recent_turn_tokens: int, total_max_tokens: int = 2000
) -> ContextBudgets:
    return ContextBudgets(
        total_max_tokens=total_max_tokens,
        identity_tokens=160,
        summary_tokens=300,
        recent_turn_tokens=recent_turn_tokens,
        facts_tokens=150,
        memory_tokens=150,
        skills_tokens=120,
        artifact_tokens=50,
        instructions_tokens=80,
    )


def _recent_window_messages(pack) -> list[str]:
    return [
        message.content
        for message in pack.messages
        if message.meta.get("block_kind") == "recent_window"
    ]


def test_respond_mode_recent_turn_bias_applied_once() -> None:
    # 12 turns x 50 tokens = 600 tokens. A single respond bias leaves a 700-token
    # budget, so every turn is retained; a double bias (350) would drop five.
    pack = _service(_assistant_turns(12)).build_pack(
        BuildPackRequest(
            session_id="sess-respond",
            agent_id="agent-respond",
            purpose="decide",
            mode_name="respond",
            query="continue",
            budgets_override=_override(recent_turn_tokens=1400),
        )
    )
    recent = _recent_window_messages(pack)
    assert len(recent) == 12
    assert pack.token_budget_report.buckets["recent_window"].cap_tokens == 700


def test_act_mode_recent_turn_bias_applied_once() -> None:
    # 10 turns x 50 tokens = 500 tokens. A single act bias leaves a 530 budget, so
    # every turn is retained; a double bias (410) would drop two turns.
    pack = _service(_assistant_turns(10)).build_pack(
        BuildPackRequest(
            session_id="sess-act",
            agent_id="agent-act",
            purpose="act",
            mode_name="act",
            query="continue",
        )
    )
    recent = _recent_window_messages(pack)
    assert len(recent) == 10
    assert pack.token_budget_report.buckets["recent_window"].cap_tokens == 530


def test_respond_short_tier_bias_applied_once() -> None:
    # decide recent 1400 -> respond halving -> 700 -> short tier halving -> 350.
    # 7 turns x 50 tokens = 350, so every turn is retained; re-applying the mode
    # bias during trimming (175) would drop four turns.
    pack = _service(_assistant_turns(7)).build_pack(
        BuildPackRequest(
            session_id="sess-respond-short",
            agent_id="agent-respond-short",
            purpose="decide",
            mode_name="respond",
            query="continue",
            budgets_override=_override(recent_turn_tokens=1400),
            constraints=BuildConstraints(context_budget_tier="short"),
        )
    )
    recent = _recent_window_messages(pack)
    assert len(recent) == 7
    assert pack.token_budget_report.buckets["recent_window"].cap_tokens == 350


def test_act_full_tier_bias_applied_once() -> None:
    # act recent 650 -> act -120 -> 530 -> full tier x1.5 -> 795.
    # 15 turns x 50 tokens = 750, so every turn is retained; re-applying the mode
    # bias during trimming (675) would drop two turns.
    pack = _service(_assistant_turns(15)).build_pack(
        BuildPackRequest(
            session_id="sess-act-full",
            agent_id="agent-act-full",
            purpose="act",
            mode_name="act",
            query="continue",
            constraints=BuildConstraints(context_budget_tier="full"),
        )
    )
    recent = _recent_window_messages(pack)
    assert len(recent) == 15
    assert pack.token_budget_report.buckets["recent_window"].cap_tokens == 795


def test_protected_recent_user_turn_is_retained() -> None:
    # decide protects the latest clarify exchange: the last assistant turn and the
    # user turn immediately before it. A 120-token budget keeps that 100-token pair
    # while dropping the two older (unprotected) turns.
    turns = [
        SessionTurn(turn_id="old-user", role="user", content=_pad("old-user:")),
        SessionTurn(
            turn_id="old-assistant", role="assistant", content=_pad("old-assistant:")
        ),
        SessionTurn(turn_id="recent-user", role="user", content=_pad("recent-user:")),
        SessionTurn(
            turn_id="recent-assistant",
            role="assistant",
            content=_pad("recent-assistant:"),
        ),
    ]
    pack = _service(turns).build_pack(
        BuildPackRequest(
            session_id="sess-protected",
            agent_id="agent-protected",
            purpose="decide",
            query="continue",
            budgets_override=_override(recent_turn_tokens=120),
        )
    )
    recent = _recent_window_messages(pack)
    assert len(recent) == 2
    assert any("recent-user:" in content for content in recent)
    assert any("recent-assistant:" in content for content in recent)
    assert not any("old-user:" in content for content in recent)
    assert not any("old-assistant:" in content for content in recent)
    user_index = next(
        idx for idx, content in enumerate(recent) if "recent-user:" in content
    )
    assistant_index = next(
        idx for idx, content in enumerate(recent) if "recent-assistant:" in content
    )
    assert user_index < assistant_index


def test_total_cap_trimming_still_applies() -> None:
    # A large recent-window budget keeps every turn, but a tight total cap still
    # drops the oldest (unprotected) turns while retaining the latest assistant
    # turn, which decide protects.
    pack = _service(_assistant_turns(10)).build_pack(
        BuildPackRequest(
            session_id="sess-total-cap",
            agent_id="agent-total-cap",
            purpose="decide",
            query="continue",
            budgets_override=_override(recent_turn_tokens=1400, total_max_tokens=300),
        )
    )
    recent = _recent_window_messages(pack)
    assert len(recent) < 10
    assert any("assistant-09:" in content for content in recent)
    assert not any("assistant-00:" in content for content in recent)
    decision_log = pack.token_budget_report.decision_log
    assert decision_log is not None
    recent_window_over_budget = [
        action
        for action in decision_log.actions
        if action.bucket == "recent_window" and action.reason_code == "over_budget"
    ]
    assert recent_window_over_budget
