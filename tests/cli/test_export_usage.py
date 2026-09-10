from __future__ import annotations

import json
from types import SimpleNamespace

from openminion.base.config import OpenMinionConfig, save_config
from openminion.cli.commands.export import run_export
from openminion.modules.storage import build_runtime_storage
from tests._csc_fixtures import _csc_install_default_agent


def test_jsonl_transcript_exposes_public_usage_without_parsing_metadata(
    tmp_path, capsys
) -> None:
    database_path = tmp_path / "state" / "openminion.db"
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(database_path)
    save_config(config, str(config_path))

    storage = build_runtime_storage(database_path)
    try:
        session = storage.sessions.resolve_session(
            agent_id="openminion",
            channel="console",
            target="focus",
            session_id="usage-session",
        )
        storage.sessions.append_message(
            session_id=session.id,
            role="outbound",
            body="reply",
            metadata={
                "run_stats_json": json.dumps(
                    {
                        "input_tokens": 12,
                        "output_tokens": 3,
                        "llm_calls": 1,
                    }
                ),
                "private": "not-a-public-contract",
            },
        )
    finally:
        storage.close()

    result = run_export(
        SimpleNamespace(
            config=str(config_path),
            session_id="usage-session",
            format="jsonl",
            output="",
            include_events=False,
        )
    )

    assert result == 0
    record = json.loads(capsys.readouterr().out)
    assert record["stats"]["input_tokens"] == 12
    assert record["stats"]["output_tokens"] == 3
    assert record["stats"]["llm_calls"] == 1
