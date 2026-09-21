from __future__ import annotations

from tests.e2e.runners.run_collaborative_team_session_e2e import main


def test_collaborative_team_session_provider_free_e2e() -> None:
    assert main() == 0
