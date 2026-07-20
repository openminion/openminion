from __future__ import annotations

from pathlib import Path

from scripts.validate.controlplane_delivery import find_violations as find_sync_deliver
from scripts.validate.webhook_secret import find_violations as find_webhook_secret


def test_sync_deliver_validator_allows_named_fallback(tmp_path: Path) -> None:
    path = tmp_path / "telegram" / "polling.py"
    path.parent.mkdir()
    path.write_text(
        "class Runner:\n"
        "    def _deliver_sync_fallback(self, payload, envelope):\n"
        "        self.deliver(payload, envelope)\n",
        encoding="utf-8",
    )

    assert find_sync_deliver(tmp_path) == []


def test_sync_deliver_validator_rejects_inline_delivery(tmp_path: Path) -> None:
    path = tmp_path / "telegram" / "webhook.py"
    path.parent.mkdir()
    path.write_text(
        "class Runner:\n"
        "    def handle(self, payload, envelope):\n"
        "        self.deliver(payload, envelope)\n",
        encoding="utf-8",
    )

    violations = find_sync_deliver(tmp_path)

    assert len(violations) == 1
    assert "synchronous self.deliver" in violations[0]


def test_webhook_secret_validator_allows_config_error_assertion(tmp_path: Path) -> None:
    path = tmp_path / "test_webhook.py"
    path.write_text(
        "with pytest.raises(ConfigError):\n"
        "    WebhookConfig(enabled=True, " + "secret=None)\n",
        encoding="utf-8",
    )

    assert find_webhook_secret((tmp_path,)) == []


def test_webhook_secret_validator_rejects_enabled_missing_secret(tmp_path: Path) -> None:
    path = tmp_path / "webhook.py"
    bad_config = "WebhookConfig(enabled=True, " + "secret='')\n"
    path.write_text(bad_config, encoding="utf-8")

    violations = find_webhook_secret((tmp_path,))

    assert len(violations) == 1
    assert "enabled webhook without required secret" in violations[0]
