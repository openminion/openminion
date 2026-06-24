from __future__ import annotations

from unittest import mock

import pytest

from openminion.modules.controlplane.runtime.gate import (
    ControlPlaneLegacyBlockedError,
    TELEGRAM_INGRESS_REQUIRED_MODULES,
    assert_controlplane_lane,
)


def test_assertion_passes_with_real_module_dependencies() -> None:
    assert_controlplane_lane(
        ingress="telegram_webhook",
        required_modules=TELEGRAM_INGRESS_REQUIRED_MODULES,
    )


def test_assertion_raises_on_missing_module() -> None:
    bogus = (
        "openminion.modules.controlplane.runtime.dispatcher",
        "openminion.modules.does_not_exist_xyz",
    )
    with pytest.raises(ControlPlaneLegacyBlockedError) as excinfo:
        assert_controlplane_lane(ingress="telegram_webhook", required_modules=bogus)
    message = str(excinfo.value)
    assert "controlplane[telegram_webhook]" in message
    assert "legacy_blocked" in message
    assert "openminion.modules.does_not_exist_xyz" in message


def test_assertion_with_empty_required_modules_is_noop() -> None:
    assert_controlplane_lane(ingress="telegram_webhook", required_modules=())


def test_default_required_modules_constant_is_non_empty() -> None:
    assert len(TELEGRAM_INGRESS_REQUIRED_MODULES) > 0
    assert all(
        module.startswith("openminion.modules.controlplane")
        for module in TELEGRAM_INGRESS_REQUIRED_MODULES
    )


@pytest.mark.parametrize(
    ("module_name", "expected_ingress"),
    [
        ("polling", 'ingress="telegram_polling"'),
        ("webhook", 'ingress="telegram_webhook"'),
    ],
)
def test_channel_runners_include_lane_gate(
    module_name: str, expected_ingress: str
) -> None:
    from openminion.modules.controlplane.channels.telegram import polling, webhook

    modules = {"polling": polling, "webhook": webhook}
    with open(modules[module_name].__file__, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert "assert_controlplane_lane" in text
    assert expected_ingress in text


@pytest.mark.parametrize(
    ("runner_factory", "method_name", "message"),
    [
        (
            "webhook",
            "handle_webhook_update",
            "controlplane[telegram_webhook]: legacy_blocked: test",
        ),
        (
            "polling",
            "start",
            "controlplane[telegram_polling]: legacy_blocked: test",
        ),
    ],
)
def test_channel_runners_raise_on_blocked_lane(
    runner_factory: str, method_name: str, message: str
) -> None:
    with mock.patch(
        "openminion.modules.controlplane.runtime.gate.assert_controlplane_lane",
        side_effect=ControlPlaneLegacyBlockedError(message),
    ):
        from openminion.modules.controlplane.channels.telegram.polling import (
            TelegramPollingRunner,
        )
        from openminion.modules.controlplane.channels.telegram.webhook import (
            TelegramWebhookRunner,
        )

        runner_cls = {
            "webhook": TelegramWebhookRunner,
            "polling": TelegramPollingRunner,
        }[runner_factory]
        runner = runner_cls.__new__(runner_cls)
        with pytest.raises(ControlPlaneLegacyBlockedError):
            if method_name == "handle_webhook_update":
                runner.handle_webhook_update({}, secret_token=None)
            else:
                runner.start()
