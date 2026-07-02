from __future__ import annotations

from datetime import date

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.css.query import QueryError
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Button, Label, Static

from ..widgets import EmptyStatePulse

_STATUS_ICON = {
    "PENDING": ("◌ Pending", "dim"),
    "ACTIVE": ("● Active", "task-active"),
    "WAITING": ("⏸ Waiting", "task-waiting"),
    "DONE": ("✓ Done", "task-done"),
    "CANCELED": ("✗ Canceled", "task-canceled"),
}

_STEP_ICON = {
    "PENDING": "○",
    "ACTIVE": "▶",
    "DONE": "✓",
    "FAILED": "✗",
    "BLOCKED": "⚠",
}


class _TaskItem(Static):
    class Clicked(Message):
        def __init__(self, task_id: str) -> None:
            super().__init__()
            self.task_id = task_id

    def __init__(self, task: dict, selected: bool = False) -> None:
        status = task.get("status", "PENDING")
        status_text, css = _STATUS_ICON.get(status, ("? Unknown", ""))
        label = task.get("title", task.get("id", "?"))
        due = str(task.get("due_at", "")).strip()
        due_state = _due_state(due if due else None)
        due_class = f"task-due-{due_state}" if due_state else ""
        render = Text(f"  {status_text}  {label}")
        if due:
            due_style = {
                "overdue": "red",
                "today": "yellow",
                "future": "dim",
            }.get(due_state, "dim")
            render.append(f"  {due}", style=due_style)
        super().__init__(
            render,
            classes=f"task-item {css} {due_class}".strip(),
            id=f"task-{task.get('id', '')}",
        )
        self._task_payload = task
        self.set_selected(selected)

    @property
    def task_id(self) -> str:
        return str(self._task_payload.get("id", ""))

    def set_selected(self, selected: bool) -> None:
        if selected:
            self.add_class("selected")
        else:
            self.remove_class("selected")

    def on_click(self) -> None:
        self.post_message(self.Clicked(self.task_id))


class _TaskList(ScrollableContainer):
    def __init__(
        self,
        tasks: list[dict],
        *,
        selected_task_id: str | None = None,
        status_filter: str = "all",
    ) -> None:
        super().__init__(id="task-list")
        self._tasks = tasks
        self._selected_task_id = selected_task_id
        self._status_filter = status_filter

    def compose(self) -> ComposeResult:
        by_status: dict[str, list[dict]] = {}
        for t in self._tasks:
            if not _task_matches_filter(t, self._status_filter):
                continue
            by_status.setdefault(t.get("status", "PENDING"), []).append(t)
        for status in ("ACTIVE", "WAITING", "PENDING", "DONE", "CANCELED"):
            items = by_status.get(status, [])
            if not items:
                continue
            yield Label(f"{status} ({len(items)})", classes="sidebar-heading")
            for t in items:
                yield _TaskItem(t, selected=(t.get("id") == self._selected_task_id))


class _StepRow(Static):
    def __init__(self, step: dict) -> None:
        icon = _STEP_ICON.get(step.get("status", "PENDING"), "?")
        idx = step.get("order_index", "")
        title = step.get("title", "?")
        status = step.get("status", "")
        super().__init__(
            f"  {icon} {idx}. {title:<40} {status}",
            classes=f"step-row step-{status.lower()}",
        )


class _PendingAction(Widget):
    class DecisionMade(Message):
        def __init__(self, decision_id: str, outcome: str) -> None:
            super().__init__()
            self.decision_id = decision_id
            self.outcome = outcome

    BINDINGS = [
        ("a", "approve", "Approve"),
        ("d", "deny", "Deny"),
    ]
    can_focus = True

    def __init__(self, action: dict) -> None:
        super().__init__(
            classes="pending-action",
            id=f"pending-{action.get('decision_id', '')}",
        )
        self._reason = str(action.get("reason") or "(no reason)")
        self._decision_id = str(action.get("decision_id", "")).strip()

    def compose(self) -> ComposeResult:
        yield Static(
            f"⚠ NEEDS APPROVAL  {self._decision_id}\n  {self._reason}",
            classes="pending-action-copy",
        )
        with Horizontal(classes="pending-action-buttons"):
            yield Button("Approve", id=f"{self._decision_id}-approve")
            yield Button("Deny", id=f"{self._decision_id}-deny", variant="error")

    def on_click(self) -> None:
        self.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == f"{self._decision_id}-approve":
            self._emit_decision("allow")
            event.stop()
        elif event.button.id == f"{self._decision_id}-deny":
            self._emit_decision("deny")
            event.stop()

    def action_approve(self) -> None:
        self._emit_decision("allow")

    def action_deny(self) -> None:
        self._emit_decision("deny")

    def _emit_decision(self, outcome: str) -> None:
        if self._decision_id:
            self.post_message(self.DecisionMade(self._decision_id, outcome))


class PolicyUpdateNeeded(Message):
    def __init__(self, decision_id: str, outcome: str) -> None:
        super().__init__()
        self.decision_id = decision_id
        self.outcome = outcome


class _TaskDetail(ScrollableContainer):
    selected_task: reactive[dict | None] = reactive(None, recompose=True)

    def __init__(self, selected_task: dict | None = None, **kwargs) -> None:
        super().__init__(id="task-detail", **kwargs)
        self.selected_task = selected_task

    def compose(self) -> ComposeResult:
        task = self.selected_task
        if task is None:
            yield Label("Select a task from the list", classes="dim-hint")
            return

        yield Label(task.get("title", ""), classes="task-detail-title")
        if task.get("description"):
            yield Static(task["description"], classes="task-detail-desc")
        yield Label(
            f"Status: {task.get('status', '')}  "
            + (f"Due: {task['due_at']}" if task.get("due_at") else ""),
            classes="task-detail-meta",
        )

        steps = task.get("steps", [])
        if steps:
            yield Label("Plan steps:", classes="sidebar-heading")
            for step in steps:
                yield _StepRow(step)

        pending = task.get("pending_actions", [])
        for action in pending:
            yield _PendingAction(action)


class TasksTab(Widget):
    can_focus = True

    def __init__(self, provider=None) -> None:
        super().__init__(id="tasks-tab")
        self._provider = provider
        self._tasks: list[dict] = []
        self._selected_task_id: str | None = None
        self._status_filter = "all"
        self._timer: Timer | None = None

    def compose(self) -> ComposeResult:
        if self._provider is None:
            yield EmptyStatePulse(classes="empty-state-pulse")
            yield Static(
                "No data — runtime provider not available.\n"
                "Start with a config to see pending tasks and decisions.",
                classes="tab-empty-notice",
            )
            return
        with Horizontal(id="tasks-body"):
            with Vertical(id="task-list-panel"):
                with Horizontal(id="task-filter-bar"):
                    for filter_name, label in (
                        ("active", "Active"),
                        ("done", "Done"),
                        ("all", "All"),
                    ):
                        classes = "task-filter-btn"
                        if filter_name == self._status_filter:
                            classes += " --selected"
                        yield Button(
                            label,
                            id=f"task-filter-{filter_name}",
                            classes=classes,
                        )
                yield _TaskList(
                    self._tasks,
                    selected_task_id=self._selected_task_id,
                    status_filter=self._status_filter,
                )
            yield _TaskDetail(selected_task=self._task_for_id(self._selected_task_id))

    async def on_mount(self) -> None:
        if self._provider is not None:
            self._tasks = self._provider.list_tasks()
            await self.recompose()
            self._sync_selected_task()
            self._sync_layout_mode()

    def on_show(self) -> None:
        if self._provider is not None and self._timer is None:
            self._timer = self.set_interval(5, self._refresh_tick)

    def on_resize(self, event) -> None:
        del event
        self.call_after_refresh(self._sync_layout_mode)

    def on_hide(self) -> None:
        self._stop_timer()

    def on_unmount(self) -> None:
        self._stop_timer()

    def _sync_layout_mode(self) -> None:
        try:
            body = self.query_one("#tasks-body", Horizontal)
        except QueryError:
            return
        if self.app.size.width < 100:
            body.add_class("--stacked")
        else:
            body.remove_class("--stacked")

    def _refresh_tick(self) -> None:
        self.run_worker(self._async_refresh(), exclusive=True)

    async def action_refresh(self) -> None:
        await self._async_refresh()

    async def _async_refresh(self) -> None:
        if self._provider is None:
            return
        self._tasks = self._provider.list_tasks()
        await self.recompose()
        self._sync_selected_task()

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def on__task_item_clicked(self, event: _TaskItem.Clicked) -> None:
        self._selected_task_id = event.task_id
        self._sync_selected_task()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("task-filter-"):
            return
        self._status_filter = button_id.removeprefix("task-filter-")
        if not _task_matches_filter(
            self._task_for_id(self._selected_task_id), self._status_filter
        ):
            self._selected_task_id = None
        await self.recompose()
        self._sync_selected_task()
        event.stop()

    async def on__pending_action_decision_made(
        self, event: _PendingAction.DecisionMade
    ) -> None:
        resolved = False
        if self._provider is not None and hasattr(self._provider, "resolve_action"):
            resolved = bool(
                self._provider.resolve_action(event.decision_id, event.outcome)
            )

        if resolved and self._provider is not None:
            self._tasks = self._provider.list_tasks()
            await self.recompose()
            self._sync_selected_task()
            self.post_message(PolicyUpdateNeeded(event.decision_id, event.outcome))

    def _task_for_id(self, task_id: str | None) -> dict | None:
        if not task_id:
            return None
        for task in self._tasks:
            if str(task.get("id", "")) == task_id:
                return task
        return None

    def _sync_selected_task(self) -> None:
        selected_task = self._task_for_id(self._selected_task_id)
        if selected_task is None:
            self._selected_task_id = None

        for task_item in self.query(_TaskItem):
            task_item.set_selected(task_item.task_id == self._selected_task_id)

        detail = self.query_one(_TaskDetail)
        detail.selected_task = selected_task


def _due_state(due_at: str | None) -> str | None:
    if not due_at:
        return None
    try:
        due_date = date.fromisoformat(due_at)
    except ValueError:
        return None
    today = date.today()
    if due_date < today:
        return "overdue"
    if due_date == today:
        return "today"
    return "future"


def _task_matches_filter(task: dict | None, status_filter: str) -> bool:
    if task is None:
        return False
    normalized = str(status_filter or "all").strip().lower()
    status = str(task.get("status", "")).strip().upper()
    if normalized == "active":
        return status in {"ACTIVE", "WAITING", "PENDING"}
    if normalized == "done":
        return status in {"DONE", "CANCELED"}
    return True
