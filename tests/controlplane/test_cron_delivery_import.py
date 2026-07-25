from __future__ import annotations

import inspect

import openminion.modules.controlplane.runtime.cron_delivery as cron_delivery


def test_cron_delivery_import_surface_has_no_runtime_path_mutation() -> None:
    source = inspect.getsource(cron_delivery)

    assert cron_delivery.deliver_cron_result is not None
    assert "sys.path" not in source
    assert ".[cron]" not in source
