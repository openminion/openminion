from __future__ import annotations

import pytest

from openminion.cli.tui.app import OpenMinionApp
from openminion.cli.tui.tabs.cron import CronTab, _CronDetail


@pytest.mark.asyncio
async def test_cron_click_hydrates_detail_and_preserves_selected_row_on_recompose() -> (
    None
):
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-cron")
        await pilot.pause()

        detail = app.screen.query_one(_CronDetail)
        assert detail.selected_job is None

        await pilot.click("#cron-daily-summary")
        await pilot.pause()

        assert detail.selected_job is not None
        assert detail.selected_job.get("id") == "daily-summary"
        assert len(app.screen.query("#cron-list .cron-item.selected")) == 1

        await app.screen.query_one(CronTab).recompose()
        await pilot.pause()

        refreshed_detail = app.screen.query_one(_CronDetail)
        assert refreshed_detail.selected_job is not None
        assert refreshed_detail.selected_job.get("id") == "daily-summary"
        assert len(app.screen.query("#cron-list .cron-item.selected")) == 1


@pytest.mark.asyncio
async def test_cron_toggle_button_updates_enabled_state() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-cron")
        await pilot.pause()

        await pilot.click("#cron-mem-refresh")
        await pilot.pause()

        detail = app.screen.query_one(_CronDetail)
        assert detail.selected_job is not None
        assert detail.selected_job.get("enabled") is False

        await pilot.click("#cron-toggle-enabled")
        await pilot.pause()

        refreshed_detail = app.screen.query_one(_CronDetail)
        assert refreshed_detail.selected_job is not None
        assert refreshed_detail.selected_job.get("enabled") is True
