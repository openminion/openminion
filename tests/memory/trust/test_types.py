from __future__ import annotations

import pytest

from openminion.modules.memory.contracts import ClaimKeyContract
from openminion.modules.memory.errors import InvalidArgumentError
from openminion.modules.memory.trust import TrustScore


def test_claim_key_contract_defaults_to_asserts() -> None:
    contract = ClaimKeyContract(claim_key="pref:lint")

    assert contract.claim_key == "pref:lint"
    assert contract.polarity == "asserts"


def test_trust_score_clamps_values_into_unit_interval() -> None:
    score = TrustScore(
        source_provenance=1.2,
        corroboration=-0.4,
        contradiction_penalty=0.3,
        rate_limit_pressure=3.4,
        score=2.0,
    )

    assert score.source_provenance == 1.0
    assert score.corroboration == 0.0
    assert score.contradiction_penalty == 0.3
    assert score.rate_limit_pressure == 1.0
    assert score.score == 1.0


def test_trust_score_rejects_negative_contradiction_penalty() -> None:
    with pytest.raises(InvalidArgumentError, match="non-negative"):
        TrustScore(
            source_provenance=0.5,
            corroboration=0.2,
            contradiction_penalty=-0.1,
            rate_limit_pressure=0.0,
            score=0.4,
        )
