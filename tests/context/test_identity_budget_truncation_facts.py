"""The context-budget stage's identity outcome reaches the manifest and the telemetry event as counts,
never as text, and a manifest that predates the fields still emits no identity facts (#811)."""

from __future__ import annotations

from openminion.modules.context.schemas import BuildPackRequest, IdentitySnippet
from tests.context.test_context_budget_tier import (
    _BudgetTierArtifactClient,
    _BudgetTierMemoryClient,
    _BudgetTierSessionClient,
)
from openminion.modules.context.service import ContextCtlService
from openminion.modules.context.telemetry import emit_pack_manifest_event

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


class _CaptureSession:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit_canonical_event(self, **kwargs):
        self.events.append(dict(kwargs))
        return "evt-811"


def _event_payload(pack) -> dict:
    sessctl = _CaptureSession()
    emit_pack_manifest_event(
        sessctl=sessctl,
        session_id="s-811",
        agent_id="a-811",
        pack=pack,
        cache_hit=False,
    )
    assert len(sessctl.events) == 1
    return sessctl.events[0]["payload"]


def test_event_reports_the_long_identity_cut_as_counts_only() -> None:
    pack = _pack(LONG)
    payload = _event_payload(pack)
    ident = pack.context_manifest.identity
    assert payload["identity_budget"] == {
        "truncated": True,
        "cap_tokens": ident.budget_cap_tokens,
        "used_tokens": ident.budget_used_tokens,
    }
    assert (
        payload["identity_budget"]["cap_tokens"] == 160
    )  # the decide purpose's identity budget
    assert payload["identity_budget"]["used_tokens"] > 0
    # the rest of the payload is unchanged in shape, and no identity text is in it
    assert {"pack_version", "total_used_tokens", "total_cap_tokens", "warnings"} <= set(
        payload
    )
    assert "Rule 01" not in str(payload) and "TAIL-SENTINEL" not in str(payload)


def test_event_reports_a_short_identity_as_measured_and_untruncated() -> None:
    payload = _event_payload(_pack("Answer in one sentence."))
    budget = payload["identity_budget"]
    assert budget["truncated"] is False
    assert 0 < budget["used_tokens"] <= budget["cap_tokens"]


def test_event_from_an_old_shaped_manifest_carries_no_identity_facts() -> None:
    pack = _pack(LONG)
    old_identity = pack.context_manifest.identity.model_copy(
        update={
            "budget_truncated": None,
            "budget_cap_tokens": None,
            "budget_used_tokens": None,
        }
    )
    old = pack.model_copy(
        update={
            "context_manifest": pack.context_manifest.model_copy(
                update={"identity": old_identity}
            )
        }
    )
    payload = _event_payload(old)
    assert payload["identity_budget"] == {}  # absence, not a measured false/0/0
    assert payload["total_cap_tokens"] == _event_payload(pack)["total_cap_tokens"]


def test_manifest_built_without_the_fields_leaves_them_unset() -> None:
    from openminion.modules.context.schemas.manifest import IdentityManifest

    ident = IdentityManifest(agent_id="a", profile_version="p", render_version="r")
    assert (
        ident.budget_truncated,
        ident.budget_cap_tokens,
        ident.budget_used_tokens,
    ) == (None, None, None)
