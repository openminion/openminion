from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer
from textual.css.query import QueryError
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Button, Label, Static


class SQLiteCronProvider:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def _store(self):
        from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore

        return SQLiteSessionStore(self._db_path)

    def list_jobs(self) -> list[dict]:
        store = self._store()
        raw_jobs = store.list_cron_jobs()
        result = []
        for j in raw_jobs:
            schedule = j.get("schedule") or j.get("schedule_json") or {}
            if isinstance(schedule, dict):
                kind = schedule.get("kind", "")
                expr = (
                    schedule.get("expr")
                    or (
                        f"every {schedule['every_ms']}ms"
                        if "every_ms" in schedule
                        else None
                    )
                    or schedule.get("at", "")
                    or kind
                )
            else:
                expr = str(schedule)

            misfire = j.get("misfire_policy") or "skip"
            if isinstance(misfire, dict):
                misfire = misfire.get("kind", "skip")

            runs = store.list_cron_runs(job_id=str(j["job_id"]), limit=5)
            recent_runs = [_map_run(r) for r in runs]

            result.append(
                {
                    "id": j["job_id"],
                    "expr": expr,
                    "enabled": bool(j.get("enabled", True)),
                    "next_due": j.get("next_due_at") or "—",
                    "misfire_policy": misfire,
                    "recent_runs": recent_runs,
                }
            )
        return result


def _map_run(run: dict) -> dict:
    state_map = {
        "finished": "success",
        "failed": "failed",
        "timed_out": "timeout",
        "running": "running",
    }
    raw_state = str(run.get("state", ""))
    state = state_map.get(raw_state, raw_state)
    at = str(run.get("due_at") or run.get("started_at") or "")
    started = run.get("started_at")
    finished = run.get("finished_at")
    duration = ""
    if started and finished:
        try:
            from datetime import datetime

            def _parse(s: str):
                return datetime.fromisoformat(s.replace("Z", "+00:00"))

            delta = _parse(finished) - _parse(started)
            secs = int(delta.total_seconds())
            duration = f"{secs}s"
        except (TypeError, ValueError):
            pass
    return {"state": state, "at": at[:19], "duration": duration}


class _CronJobItem(Static):
    class Clicked(Message):
        def __init__(self, job_id: str) -> None:
            super().__init__()
            self.job_id = job_id

    def __init__(self, job: dict, selected: bool = False) -> None:
        enabled = job.get("enabled", True)
        dot = "●" if enabled else "○"
        name = job.get("id", "?")
        expr = job.get("expr", "")
        super().__init__(
            f"  {dot} {name}\n    {expr}",
            classes=f"cron-item {'cron-enabled' if enabled else 'cron-disabled'}",
            id=f"cron-{job.get('id', '')}",
        )
        self._job = job
        self._selected = False
        self.set_selected(selected)

    @property
    def job_id(self) -> str:
        return str(self._job.get("id", ""))

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        if selected:
            self.add_class("selected")
        else:
            self.remove_class("selected")

    def on_click(self) -> None:
        self.post_message(self.Clicked(self.job_id))


class _RunRow(Static):
    _STATE_LABEL = {
        "success": "✓ ok",
        "failed": "✗ fail",
        "timeout": "✗ timeout",
        "running": "▶ running",
    }

    def __init__(self, run: dict) -> None:
        at = run.get("at", "")
        state = run.get("state", "")
        duration = run.get("duration", "")
        state_label = self._STATE_LABEL.get(state, state or "?")
        super().__init__(
            f"  {state_label:<12} {at:<22} {duration}",
            classes=f"run-row run-{state}",
        )


class _CronDetail(ScrollableContainer):
    selected_job: reactive[dict | None] = reactive(None, recompose=True)

    def __init__(self, job: dict | None = None) -> None:
        super().__init__(id="cron-detail")
        self.selected_job = job

    def compose(self) -> ComposeResult:
        job = self.selected_job
        if job is None:
            yield Label("Select a job from the list", classes="dim-hint")
            return

        yield Label(job.get("id", ""), classes="task-detail-title")
        yield Label(f"Schedule:  {job.get('expr', '')}", classes="task-detail-meta")
        yield Label(
            f"Next due:  {job.get('next_due', '—')}", classes="task-detail-meta"
        )
        yield Label(
            f"Misfire:   {job.get('misfire_policy', 'skip')}",
            classes="task-detail-meta",
        )
        yield Label(
            f"Status:    {'enabled' if job.get('enabled') else 'disabled'}",
            classes="task-detail-meta",
        )
        yield Button(
            "Disable job" if job.get("enabled") else "Enable job",
            id="cron-toggle-enabled",
            classes="cron-toggle-btn",
        )

        runs = job.get("recent_runs", [])
        if runs:
            yield Label("Recent runs:", classes="sidebar-heading")
            for run in runs:
                yield _RunRow(run)


class CronTab(Widget):
    can_focus = True

    BINDINGS = [
        ("e", "toggle_enabled", "Toggle"),
    ]

    def __init__(self, provider=None) -> None:
        super().__init__(id="cron-tab")
        self._provider = provider
        self._jobs: list[dict] = []
        self._selected_job_id: str | None = None
        self._timer: Timer | None = None

    def compose(self) -> ComposeResult:
        if self._provider is None:
            yield Static(
                "No data — runtime provider not available.\n"
                "Start with a config to see scheduled jobs.",
                classes="tab-empty-notice",
            )
            return
        with Horizontal(id="cron-body"):
            with ScrollableContainer(id="cron-list"):
                if self._jobs:
                    for job in self._jobs:
                        yield _CronJobItem(
                            job, selected=(job.get("id") == self._selected_job_id)
                        )
                else:
                    yield Label("No cron jobs", classes="dim-hint")
            yield _CronDetail(job=self._job_for_id(self._selected_job_id))

    async def on_mount(self) -> None:
        if self._provider is not None:
            self._jobs = self._provider.list_jobs()
            await self.recompose()
            self._sync_selected_job()
            self._sync_layout_mode()

    def on_show(self) -> None:
        if self._provider is not None and self._timer is None:
            self._timer = self.set_interval(10, self._refresh_tick)

    def on_resize(self, event) -> None:
        del event
        self.call_after_refresh(self._sync_layout_mode)

    def _sync_layout_mode(self) -> None:
        try:
            body = self.query_one("#cron-body", Horizontal)
        except QueryError:
            return
        if self.app.size.width < 100:
            body.add_class("--stacked")
        else:
            body.remove_class("--stacked")

    def on_hide(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _refresh_tick(self) -> None:
        self.run_worker(self._async_refresh(), exclusive=True)

    async def action_refresh(self) -> None:
        await self._async_refresh()

    async def _async_refresh(self) -> None:
        if self._provider is None:
            return
        self._jobs = self._provider.list_jobs()
        await self.recompose()
        self._sync_selected_job()

    def on__cron_job_item_clicked(self, event: _CronJobItem.Clicked) -> None:
        self._selected_job_id = event.job_id
        self._sync_selected_job()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "cron-toggle-enabled":
            return
        if await self._toggle_selected_job():
            event.stop()

    async def action_toggle_enabled(self) -> None:
        await self._toggle_selected_job()

    async def _toggle_selected_job(self) -> bool:
        selected_job = self._job_for_id(self._selected_job_id)
        if selected_job is None or self._provider is None:
            return False
        toggle = getattr(self._provider, "toggle_job_enabled", None)
        if not callable(toggle):
            return False
        next_enabled = not bool(selected_job.get("enabled"))
        if not bool(toggle(str(selected_job.get("id", "")), next_enabled)):
            return False
        self._jobs = self._provider.list_jobs()
        await self.recompose()
        self._sync_selected_job()
        return True

    def _job_for_id(self, job_id: str | None) -> dict | None:
        if not job_id:
            return None
        for job in self._jobs:
            if str(job.get("id", "")) == job_id:
                return job
        return None

    def _sync_selected_job(self) -> None:
        selected_job = self._job_for_id(self._selected_job_id)
        if selected_job is None:
            self._selected_job_id = None

        for cron_item in self.query(_CronJobItem):
            cron_item.set_selected(cron_item.job_id == self._selected_job_id)

        detail = self.query_one(_CronDetail)
        detail.selected_job = selected_job
