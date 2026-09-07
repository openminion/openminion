from argparse import Namespace

from openminion.cli.commands import daemon as daemon_command
from openminion.cli.commands.service import _service_status_payload, run_service


def test_cron_service_status_uses_daemon_scheduler_readiness(monkeypatch) -> None:
    monkeypatch.setattr(
        daemon_command,
        "_build_daemon_status_payload",
        lambda *_args, **_kwargs: {
            "scheduler": {
                "state": "ready",
                "hosted_by": "daemon",
                "last_heartbeat_at": "2026-09-06T00:00:00+00:00",
            }
        },
    )

    payload = _service_status_payload(
        Namespace(config=None, home_root=None, data_root=None),
        service_id="cron",
    )

    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert payload["hosted_by"] == "daemon"


def test_cron_service_start_directs_operator_to_daemon(capsys) -> None:
    code = run_service(
        Namespace(
            service_command="start",
            service="cron",
            json=False,
        )
    )

    assert code == 1
    assert "openminion daemon start" in capsys.readouterr().out
