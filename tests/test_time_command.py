from __future__ import annotations

import json
from argparse import Namespace
from unittest import mock

import pytest

from openminion.cli.commands import time as time_command
from openminion.cli.commands.time import run_time
from tests.test_api_v1_runtime import _write_echo_config


def test_now_dispatches_to_time_now_tool() -> None:
    args = Namespace(
        time_command="now",
        timezone="Asia/Tokyo",
        session="time-session",
        config=None,
    )
    with mock.patch.object(time_command, "run_tools", return_value=0) as mocked:
        code = run_time(args)
    assert code == 0
    proxy = mocked.call_args.args[0]
    assert proxy.tools_command == "run"
    assert proxy.tool == "time.now"
    assert proxy.session == "time-session"
    assert json.loads(proxy.json_payload) == {"timezone": "Asia/Tokyo"}


def test_diff_signed_sets_abs_false() -> None:
    args = Namespace(
        time_command="diff",
        a="2026-03-11T08:00:00Z",
        b="2026-03-11T07:00:00Z",
        unit="hours",
        signed=True,
        session="time-session",
        config=None,
    )
    with mock.patch.object(time_command, "run_tools", return_value=0) as mocked:
        code = run_time(args)
    assert code == 0
    proxy = mocked.call_args.args[0]
    assert proxy.tool == "time.diff"
    assert json.loads(proxy.json_payload) == {
        "a": "2026-03-11T08:00:00Z",
        "b": "2026-03-11T07:00:00Z",
        "unit": "hours",
        "abs": False,
    }


def test_next_cron_dispatches_expected_payload() -> None:
    args = Namespace(
        time_command="next-cron",
        cron="0 9 * * 1-5",
        timezone="America/Los_Angeles",
        from_iso="2026-03-11T00:00:00Z",
        count=5,
        session="time-session",
        config=None,
    )
    with mock.patch.object(time_command, "run_tools", return_value=0) as mocked:
        code = run_time(args)
    assert code == 0
    proxy = mocked.call_args.args[0]
    assert proxy.tool == "time.next_cron"
    assert json.loads(proxy.json_payload) == {
        "cron": "0 9 * * 1-5",
        "timezone": "America/Los_Angeles",
        "from_iso": "2026-03-11T00:00:00Z",
        "count": 5,
    }


def test_unknown_subcommand_raises() -> None:
    with pytest.raises(RuntimeError, match="Unknown time command"):
        run_time(Namespace(time_command="unknown"))


_INPROC_CASES = (
    ("now", {}, "time.now"),
    ("in-zone", {"timezone": "Europe/Helsinki"}, "time.in_zone"),
    (
        "convert",
        {"iso": "2026-03-11T08:00:00Z", "timezone": "Europe/Helsinki"},
        "time.convert",
    ),
    ("parse-iso", {"iso": "2026-03-11T08:00:00Z"}, "time.parse_iso"),
    (
        "diff",
        {
            "a": "2026-03-11T08:00:00Z",
            "b": "2026-03-11T07:00:00Z",
            "unit": "hours",
            "signed": False,
        },
        "time.diff",
    ),
    (
        "format",
        {
            "iso": "2026-03-11T08:00:00Z",
            "timezone": "Europe/Helsinki",
            "format": "date",
        },
        "time.format",
    ),
    (
        "start-of-day",
        {"iso": "2026-03-11T08:00:00Z", "timezone": "Europe/Helsinki"},
        "time.start_of_day",
    ),
    (
        "end-of-day",
        {"iso": "2026-03-11T08:00:00Z", "timezone": "Europe/Helsinki"},
        "time.end_of_day",
    ),
    (
        "next-cron",
        {
            "cron": "0 9 * * 1-5",
            "timezone": "Europe/Helsinki",
            "from_iso": "2026-03-11T00:00:00Z",
            "count": 2,
        },
        "time.next_cron",
    ),
)


@pytest.mark.parametrize(
    ("action", "fields", "expected_tool"),
    _INPROC_CASES,
    ids=[row[0] for row in _INPROC_CASES],
)
def test_each_time_subcommand_executes_inproc(
    tmp_path, monkeypatch, capsys, action, fields, expected_tool
) -> None:
    config_path = _write_echo_config(tmp_path)
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))
    args = Namespace(
        time_command=action,
        session="time-direct",
        config=str(config_path),
        home_root=None,
        data_root=None,
        runtime_source="inproc",
        **fields,
    )

    assert run_time(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["runtime_source"] == "inproc"
    assert payload["tool"]["name"] == expected_tool
    assert "error_code" not in payload["tool"]["data"]
