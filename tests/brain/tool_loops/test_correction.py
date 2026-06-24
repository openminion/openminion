from __future__ import annotations

import json

import pytest

from openminion.modules.brain.loop.tools.correction import (
    CorrectionHistory,
    CorrectionPlan,
    CorrectionRecord,
)


class TestCorrectionPlan:
    def test_valid_retry_same(self):
        plan = CorrectionPlan(
            diagnosis="File not found, retrying",
            correction_type="retry_same",
            confidence=0.8,
        )
        assert plan.correction_type == "retry_same"
        assert plan.corrected_args is None

    def test_valid_retry_different(self):
        plan = CorrectionPlan(
            diagnosis="Wrong path, trying alternate",
            correction_type="retry_different",
            corrected_args={"path": "/new/path"},
            confidence=0.7,
        )
        assert plan.corrected_args == {"path": "/new/path"}

    def test_valid_replan(self):
        plan = CorrectionPlan(
            diagnosis="Approach failed",
            correction_type="replan",
            replan_hint="Try using grep instead of find",
            confidence=0.6,
        )
        assert plan.replan_hint is not None

    def test_valid_ask_user(self):
        plan = CorrectionPlan(
            diagnosis="Cannot determine correct action",
            correction_type="ask_user",
            confidence=0.3,
        )
        assert plan.correction_type == "ask_user"

    def test_valid_accept_partial(self):
        plan = CorrectionPlan(
            diagnosis="Partial results are sufficient",
            correction_type="accept_partial",
            confidence=0.5,
        )
        assert plan.correction_type == "accept_partial"

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(Exception):
            CorrectionPlan(
                diagnosis="test",
                correction_type="retry_same",
                confidence=1.5,
            )
        with pytest.raises(Exception):
            CorrectionPlan(
                diagnosis="test",
                correction_type="retry_same",
                confidence=-0.1,
            )

    def test_retry_different_requires_corrected_args(self):
        with pytest.raises(Exception):
            CorrectionPlan(
                diagnosis="Wrong path",
                correction_type="retry_different",
                confidence=0.7,
            )

    def test_replan_requires_hint(self):
        with pytest.raises(Exception):
            CorrectionPlan(
                diagnosis="Need new approach",
                correction_type="replan",
                confidence=0.6,
            )

    def test_extra_field_rejected(self):
        with pytest.raises(Exception):
            CorrectionPlan(
                diagnosis="test",
                correction_type="retry_same",
                confidence=0.5,
                extra_field="not allowed",
            )

    def test_json_round_trip(self):
        plan = CorrectionPlan(
            diagnosis="File missing",
            correction_type="retry_different",
            corrected_args={"path": "/alt"},
            confidence=0.75,
        )
        data = json.loads(plan.model_dump_json())
        restored = CorrectionPlan.model_validate(data)
        assert restored == plan


class TestCorrectionHistory:
    def test_empty_history(self):
        h = CorrectionHistory()
        assert len(h) == 0
        assert h.last_n(5) == []

    def test_append_and_length(self):
        h = CorrectionHistory()
        h.append(
            CorrectionRecord(
                iteration_index=0,
                correction_type="retry_same",
                diagnosis_summary="retry",
                applied=True,
            )
        )
        assert len(h) == 1

    def test_last_n_caps(self):
        h = CorrectionHistory()
        for i in range(10):
            h.append(
                CorrectionRecord(
                    iteration_index=i,
                    correction_type="retry_same",
                    diagnosis_summary=f"attempt {i}",
                    applied=True,
                )
            )
        last5 = h.last_n(5)
        assert len(last5) == 5
        assert last5[0].iteration_index == 5
