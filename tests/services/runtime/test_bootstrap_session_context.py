from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from openminion.services.runtime.bootstrap import build_session_context_service


def _config() -> SimpleNamespace:
    runtime = SimpleNamespace(
        session_archive_root_path="",
        session_keep_recent_messages=20,
        session_max_compact_per_turn=100,
        session_summary_max_chars=8000,
        session_archive_enabled=True,
        session_archive_ref_limit=3,
        session_context_token_budget=256,
        session_context_chars_per_token=3.5,
        session_summary_enrichment_enabled=False,
    )
    return SimpleNamespace(runtime=runtime)


def test_build_session_context_service_passes_retrieve_ctl(tmp_path: Path) -> None:
    retrieve_ctl = object()
    sessions = mock.MagicMock()
    logger = logging.getLogger("test.bootstrap.session_context")
    with mock.patch(
        "openminion.services.runtime.bootstrap.SessionContextService"
    ) as mocked_service:
        build_session_context_service(
            config=_config(),
            sessions=sessions,
            logger=logger,
            config_path=tmp_path / "config.json",
            storage_path=tmp_path / "state" / "openminion.db",
            memory_root=tmp_path / "memory",
            data_root=tmp_path / "data",
            retrieve_ctl=retrieve_ctl,
        )
    assert mocked_service.call_args is not None
    assert mocked_service.call_args.kwargs["retrieve_ctl"] is retrieve_ctl


def test_build_session_context_service_defaults_retrieve_ctl_none(
    tmp_path: Path,
) -> None:
    sessions = mock.MagicMock()
    logger = logging.getLogger("test.bootstrap.session_context.default")
    with mock.patch(
        "openminion.services.runtime.bootstrap.SessionContextService"
    ) as mocked_service:
        build_session_context_service(
            config=_config(),
            sessions=sessions,
            logger=logger,
            config_path=tmp_path / "config.json",
            storage_path=tmp_path / "state" / "openminion.db",
            memory_root=tmp_path / "memory",
            data_root=tmp_path / "data",
        )
    assert mocked_service.call_args is not None
    assert mocked_service.call_args.kwargs["retrieve_ctl"] is None


def test_build_session_context_service_forwards_token_budget_settings(
    tmp_path: Path,
) -> None:
    sessions = mock.MagicMock()
    logger = logging.getLogger("test.bootstrap.session_context.budget")
    with mock.patch(
        "openminion.services.runtime.bootstrap.SessionContextService"
    ) as mocked_service:
        build_session_context_service(
            config=_config(),
            sessions=sessions,
            logger=logger,
            config_path=tmp_path / "config.json",
            storage_path=tmp_path / "state" / "openminion.db",
            memory_root=tmp_path / "memory",
            data_root=tmp_path / "data",
        )
    assert mocked_service.call_args is not None
    assert mocked_service.call_args.kwargs["token_budget"] == 256
    assert mocked_service.call_args.kwargs["chars_per_token"] == 3.5
    assert mocked_service.call_args.kwargs["summary_enrichment_enabled"] is False
