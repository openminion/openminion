from __future__ import annotations

import pytest

from openminion.cli.tui.app import OpenMinionApp
from openminion.cli.tui.tabs.tasks import TasksTab, _TaskDetail


@pytest.mark.asyncio
async def test_tasks_click_hydrates_detail_and_preserves_selected_row_on_recompose() -> (
    None
):
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-tasks")
        await pilot.pause()

        detail = app.screen.query_one(_TaskDetail)
        assert detail.selected_task is None

        await pilot.click("#task-task-001")
        await pilot.pause()

        assert detail.selected_task is not None
        assert detail.selected_task.get("id") == "task-001"
        assert len(app.screen.query("#task-list .task-item.selected")) == 1
        assert app.screen.query_one("#task-task-003").has_class("task-due-overdue")

        await app.screen.query_one(TasksTab).recompose()
        await pilot.pause()

        refreshed_detail = app.screen.query_one(_TaskDetail)
        assert refreshed_detail.selected_task is not None
        assert refreshed_detail.selected_task.get("id") == "task-001"
        assert len(app.screen.query("#task-list .task-item.selected")) == 1


@pytest.mark.asyncio
async def test_tasks_filter_buttons_and_inline_approve_button() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-tasks")
        await pilot.pause()

        await pilot.click("#task-filter-done")
        await pilot.pause()

        done_rows = app.screen.query("#task-list .task-item")
        assert len(done_rows) >= 1
        assert app.screen.query_one("#task-task-005")
        assert not app.screen.query("#task-task-001")

        await pilot.click("#task-filter-all")
        await pilot.pause()
        await pilot.click("#task-task-003")
        await pilot.pause()

        await pilot.click("#dec-001-approve")
        await pilot.pause()

        assert len(app.screen.query("#task-detail .pending-action")) == 0
