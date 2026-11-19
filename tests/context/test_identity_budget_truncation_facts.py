"""The identity budget outcome reaches the context manifest as counts, never as text (#811)."""

from __future__ import annotations

from openminion.modules.context.schemas import BuildPackRequest, IdentitySnippet
from tests.context.test_context_budget_tier import (
    _BudgetTierArtifactClient,
    _BudgetTierMemoryClient,
    _BudgetTierSessionClient,
)
from openminion.modules.context.service import ContextCtlService

LONG = " ".join(f"Rule {i:02d}: keep this." for i in range(1, 121)) + " TAIL-SENTINEL"


class _Identity:
    contract_version = "v1"

    def __init__(self, text: str, sections: dict[str, str] | None = None) -> None:
        self._text, self._sections = text, sections

    def render(self, *, agent_id, purpose, max_tokens, provider_pref=None):
        del purpose, max_tokens, provider_pref
        return IdentitySnippet(
            agent_id=agent_id,
            profile_version="p:v1",
            render_version="r:v1",
            text=self._text,
            sections=self._sections,
        )


def _pack(text: str, sections: dict[str, str] | None = None):
    service = ContextCtlService(
        identityctl=_Identity(text, sections),
        sessctl=_BudgetTierSessionClient(),
        memctl=_BudgetTierMemoryClient(),
        artifactctl=_BudgetTierArtifactClient(),
    )
    return service.build_pack(
        BuildPackRequest(
            session_id="s-811", agent_id="a-811", purpose="decide", query="hello"
        )
    )


def _prompt(pack) -> str:
    return "\n".join(str(m.content) for m in pack.messages)


def test_short_identity_is_unchanged_and_not_marked() -> None:
    pack = _pack("Answer in one sentence.")
    ident = pack.context_manifest.identity
    assert ident.budget_truncated is False
    assert 0 < ident.budget_used_tokens <= ident.budget_cap_tokens
    assert "Answer in one sentence." in _prompt(pack)


def test_long_identity_records_truncation_and_drops_the_tail() -> None:
    pack = _pack(LONG)
    ident = pack.context_manifest.identity
    assert ident.budget_truncated is True
    assert (
        ident.budget_used_tokens <= ident.budget_cap_tokens + 4
    )  # the marker may add a few tokens
    prompt = _prompt(pack)
    assert "Rule 01" in prompt and "TAIL-SENTINEL" not in prompt


def test_manifest_carries_counts_not_text() -> None:
    dumped = _pack(LONG).context_manifest.identity.model_dump()
    assert set(dumped) >= {
        "budget_truncated",
        "budget_cap_tokens",
        "budget_used_tokens",
    }
    assert "Rule" not in str(dumped) and "TAIL-SENTINEL" not in str(dumped)


def test_neighbouring_identity_sections_report_the_cut() -> None:
    pack = _pack(
        "persona + instructions",
        sections={"persona": "Calm and brief.", "instructions": LONG},
    )
    assert pack.context_manifest.identity.budget_truncated is True
    assert "TAIL-SENTINEL" not in _prompt(pack)
