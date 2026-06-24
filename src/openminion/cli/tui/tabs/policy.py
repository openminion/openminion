from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.css.query import QueryError
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Button, Label, Static


_RISK_ICON = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}
_OUTCOME_ICON = {"allow": "✓", "deny": "✗", "pending": "⏳"}


class _DecisionRow(Static):
    def __init__(self, decision: dict) -> None:
        risk = decision.get("risk", "")
        icon = _RISK_ICON.get(risk, "·")
        tool = decision.get("tool", "?")
        reason = decision.get("reason", "") or "(no reason provided)"
        outcome = str(decision.get("outcome", "pending")).lower()
        render = Text()
        render.append(f"  {icon} {tool}\n", style="bold")
        render.append(f"    {reason} ", style="italic dim")
        render.append(f"[{outcome}]")
        super().__init__(
            render,
            classes=f"policy-row policy-risk-{risk.lower()}",
            id=f"dec-{decision.get('id', '')}",
        )
        self._decision = decision


class _GrantRow(Widget):
    def __init__(self, grant: dict) -> None:
        super().__init__(classes="grant-row", id=f"grant-{grant.get('id', '')}")
        self._grant = grant

    def compose(self) -> ComposeResult:
        scope = self._grant.get("scope", "?")
        ttl = self._grant.get("ttl", "")
        uses = f"{self._grant.get('uses_left', '?')}/{self._grant.get('max_uses', '?')} uses"
        with Horizontal(classes="grant-row-body"):
            yield Label(f"✓ {scope:<28} {uses}  {ttl}", classes="grant-copy")
            yield Button("Revoke", id=f"revoke-{self._grant.get('id', '')}")


class _HistoryRow(Static):
    def __init__(self, entry: dict) -> None:
        outcome = entry.get("outcome", "")
        icon = _OUTCOME_ICON.get(outcome, "·")
        tool = entry.get("tool", "?")
        ts = entry.get("ts", "")
        super().__init__(
            f"  {icon} {tool:<28} {outcome:<10} {ts}",
            classes=f"history-row history-{outcome}",
        )


class _ConfirmGrantRevokeModal(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, grant_id: str) -> None:
        super().__init__()
        self._grant_id = grant_id

    def compose(self) -> ComposeResult:
        with Vertical(id="grant-confirm-dialog"):
            yield Label(f"Revoke grant {self._grant_id}?", id="grant-confirm-title")
            with Horizontal(id="grant-confirm-buttons"):
                yield Button("Cancel", id="grant-confirm-cancel")
                yield Button("Revoke", id="grant-confirm-ok", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "grant-confirm-ok")

    def action_cancel(self) -> None:
        self.dismiss(False)


class PolicyTab(Widget):
    can_focus = True

    def __init__(self, provider=None) -> None:
        super().__init__(id="policy-tab")
        self._provider = provider
        self._pending: list[dict] = []
        self._grants: list[dict] = []
        self._history: list[dict] = []
        self._timer: Timer | None = None

    def compose(self) -> ComposeResult:
        if self._provider is None:
            yield Static(
                "No data — runtime provider not available.\n"
                "Start with a config to view policy decisions and grants.",
                classes="tab-empty-notice",
            )
            return
        with Horizontal(id="policy-body"):
            with ScrollableContainer(id="policy-left"):
                yield Label("PENDING DECISIONS", classes="sidebar-heading")
                if self._pending:
                    for d in self._pending:
                        yield _DecisionRow(d)
                    yield Label(
                        "  Approve/deny from Tasks tab (Ctrl+2)",
                        classes="dim-hint",
                    )
                else:
                    yield Label("No pending decisions", classes="dim-hint")

                yield Label("ACTIVE GRANTS", classes="sidebar-heading")
                if self._grants:
                    for g in self._grants:
                        yield _GrantRow(g)
                else:
                    yield Label("No active grants", classes="dim-hint")

            with ScrollableContainer(id="policy-right"):
                yield Label("RECENT DECISIONS", classes="sidebar-heading")
                if self._history:
                    for h in self._history:
                        yield _HistoryRow(h)
                else:
                    yield Label("No decision history", classes="dim-hint")

    async def on_mount(self) -> None:
        await self.refresh_from_provider()

    def on_show(self) -> None:
        if self._provider is not None and self._timer is None:
            self._timer = self.set_interval(5, self._refresh_tick)
        self.call_after_refresh(self._sync_layout_mode)

    def on_resize(self, event) -> None:
        del event
        self.call_after_refresh(self._sync_layout_mode)

    def on_hide(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _refresh_tick(self) -> None:
        self.run_worker(self.refresh_from_provider(), exclusive=True)

    async def action_refresh(self) -> None:
        await self.refresh_from_provider()

    async def refresh_from_provider(self) -> None:
        if self._provider is not None:
            self._pending = self._provider.list_pending_decisions()
            self._grants = self._provider.list_active_grants()
            self._history = self._provider.list_recent_decisions()
            await self.recompose()
            self._sync_layout_mode()

    async def _revoke_grant(self, grant_id: str) -> None:
        if self._provider is None:
            return
        revoke = getattr(self._provider, "revoke_grant", None)
        if not callable(revoke):
            return
        if revoke(grant_id):
            await self.refresh_from_provider()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("revoke-"):
            grant_id = button_id.removeprefix("revoke-").strip()
            self.app.push_screen(
                _ConfirmGrantRevokeModal(grant_id),
                lambda confirmed: (
                    self.run_worker(self._revoke_grant(grant_id), exclusive=True)
                    if confirmed
                    else None
                ),
            )
            event.stop()

    def _sync_layout_mode(self) -> None:
        try:
            body = self.query_one("#policy-body", Horizontal)
        except QueryError:
            return
        if self.app.size.width < 100:
            body.add_class("--stacked")
        else:
            body.remove_class("--stacked")
