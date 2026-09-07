from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.modules.identity.config import (
    IdentityCtlConfig,
    StorageConfig,
    from_base_config,
    load_config,
    resolve_default_render_budget,
)
from openminion.modules.identity.cli import _resolve_storage_db_path


CANONICAL_PURPOSES = {"decide", "plan", "act", "reflect", "summarize", "judge"}
EXPECTED_BUDGETS = {
    "decide": 160,
    "plan": 220,
    "act": 180,
    "reflect": 220,
    "summarize": 160,
    "judge": 170,
}


def test_storage_config_db_path_alias_updates_sqlite_path() -> None:
    cfg = StorageConfig(db_path="/tmp/custom-identity.db")
    assert cfg.sqlite_path == "/tmp/custom-identity.db"


def test_storage_config_sqlite_path_alias_updates_db_path() -> None:
    cfg = StorageConfig(sqlite_path="/tmp/custom-identity.db")
    assert cfg.db_path == "/tmp/custom-identity.db"


def test_module_cli_storage_path_honors_identity_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity_root = tmp_path / "identities"
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("OPENMINION_IDENTITY_ROOT", str(identity_root))
    monkeypatch.setenv("OPENMINION_IDENTITY_DB", "module.db")

    resolved = _resolve_storage_db_path(tmp_path / "missing.yaml", None)

    assert resolved == (identity_root / "module.db").resolve()


def test_module_config_relative_paths_anchor_under_data_root(tmp_path: Path) -> None:
    config_path = tmp_path / "identity.yaml"
    config_path.write_text(
        """
storage:
  db_path: state/module.db
profiles:
  bundle_root: identities
        """.strip(),
        encoding="utf-8",
    )
    data_root = tmp_path / "data"

    cfg = load_config(
        config_path,
        home_root=tmp_path,
        data_root=data_root,
        env={},
    )

    assert Path(cfg.storage.db_path) == (data_root / "state" / "module.db").resolve()
    assert (
        Path(cfg.storage.sqlite_path) == (data_root / "state" / "module.db").resolve()
    )
    assert Path(cfg.profiles.directory) == (data_root / "identities").resolve()
    assert Path(cfg.profiles.bundle_root) == (data_root / "identities").resolve()


@pytest.mark.parametrize("purpose", sorted(CANONICAL_PURPOSES))
def test_resolver_pins_canonical_purpose_budgets(purpose: str) -> None:
    cfg = IdentityCtlConfig()
    assert (
        resolve_default_render_budget(purpose, identity_cfg=cfg)
        == (EXPECTED_BUDGETS[purpose])
    )


@pytest.mark.parametrize(
    "alias, canonical",
    [
        ("validate", "judge"),
        ("verify", "judge"),
        ("validation", "judge"),
        ("decision", "decide"),
        ("summary", "summarize"),
        ("summarization", "summarize"),
        ("planning", "plan"),
        ("reflection", "reflect"),
        ("chat", "act"),
        ("respond_followup", "act"),
        ("reply", "act"),
    ],
)
def test_resolver_resolves_aliases_through_normalization(
    alias: str, canonical: str
) -> None:
    cfg = IdentityCtlConfig()
    assert (
        resolve_default_render_budget(alias, identity_cfg=cfg)
        == (EXPECTED_BUDGETS[canonical])
    )


def test_resolver_unknown_purpose_falls_back_to_act() -> None:
    cfg = IdentityCtlConfig()
    # Unknown purposes are not in the canonical set or alias map; renderer normalizes
    # unknown purposes to `act`, so the resolver returns the `act` budget.
    assert resolve_default_render_budget("unknown-purpose", identity_cfg=cfg) == 180


def test_default_budget_inventory_is_exactly_six_canonical_entries() -> None:
    cfg = IdentityCtlConfig()
    assert set(cfg.rendering.default_budgets) == CANONICAL_PURPOSES
    assert len(cfg.rendering.default_budgets) == 6


def test_resolver_uses_180_when_act_entry_missing() -> None:
    cfg = IdentityCtlConfig()
    cfg.rendering.default_budgets.pop("act", None)
    cfg.rendering.default_budgets.pop("decide", None)
    # `decide` is canonical so normalization does not redirect it; with the
    # entry missing the resolver falls back to `act`, which is also missing,
    # so the hard fallback of 180 applies.
    assert resolve_default_render_budget("decide", identity_cfg=cfg) == 180


def _make_base_config_with_legacy_root(tmp_path: Path) -> OpenMinionConfig:
    config = OpenMinionConfig()
    config.identity.root = str(tmp_path / "legacy")
    return config


def test_from_base_config_warns_on_construction_with_legacy_root(
    tmp_path: Path,
) -> None:
    config = _make_base_config_with_legacy_root(tmp_path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        from_base_config(
            base_config=config,
            home_root=tmp_path,
            data_root=tmp_path / "data",
        )
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecations, "expected DeprecationWarning for identity.root"
    assert any("identity.root is deprecated" in str(w.message) for w in deprecations), [
        str(w.message) for w in deprecations
    ]


def test_from_base_config_warns_after_post_construction_mutation(
    tmp_path: Path,
) -> None:
    config = OpenMinionConfig()
    # Mirror the mutation pattern in tests/test_status_command.py:728 and
    # tests/test_brain_bridge_parity.py:372.
    config.identity.root = str(tmp_path / "legacy.db")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        from_base_config(
            base_config=config,
            home_root=tmp_path,
            data_root=tmp_path / "data",
        )
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("identity.root is deprecated" in str(w.message) for w in deprecations)


def test_from_base_config_silent_when_legacy_root_unset(tmp_path: Path) -> None:
    config = OpenMinionConfig()
    config.identity.bundle_root = str(tmp_path / "bundle")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        from_base_config(
            base_config=config,
            home_root=tmp_path,
            data_root=tmp_path / "data",
        )
    deprecations = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "identity.root" in str(w.message)
    ]
    assert not deprecations, [str(w.message) for w in deprecations]
