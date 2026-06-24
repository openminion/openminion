from __future__ import annotations

from openminion.modules.brain import (
    BudgetAdjust,
    MetaDirective,
    MetaMetrics,
    MetaResult,
    MetaState,
    VerificationMode,
)
from openminion.modules.brain import meta as brain_meta
from openminion.modules.brain.meta.schemas import (
    BudgetAdjust as CanonicalBudgetAdjust,
    MetaDirective as CanonicalMetaDirective,
    MetaMetrics as CanonicalMetaMetrics,
    MetaResult as CanonicalMetaResult,
    MetaState as CanonicalMetaState,
    VerificationMode as CanonicalVerificationMode,
)


def test_brain_meta_shim_exports_match_canonical_schema_types() -> None:
    assert MetaMetrics is CanonicalMetaMetrics
    assert MetaDirective is CanonicalMetaDirective
    assert MetaResult is CanonicalMetaResult
    assert MetaState is CanonicalMetaState
    assert VerificationMode is CanonicalVerificationMode
    assert BudgetAdjust is CanonicalBudgetAdjust


def test_brain_meta_module_no_longer_exports_legacy_aliases() -> None:
    assert not hasattr(brain_meta, "LegacyMetaMetrics")
    assert not hasattr(brain_meta, "LegacyMetaDirective")
    assert not hasattr(brain_meta, "LegacyMetaResult")
    assert not hasattr(brain_meta, "LegacyMetaState")
    assert not hasattr(brain_meta, "LegacyVerificationMode")
    assert not hasattr(brain_meta, "LegacyBudgetAdjust")
