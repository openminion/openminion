from __future__ import annotations

import re

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.css.query import QueryError
from textual.message import Message
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Button, Input, Label, LoadingIndicator, Static

from ..widgets import EmptyStatePulse


class _AgentRow(Widget):
    """Clickable agent list item."""

    can_focus = True

    class Clicked(Message):
        def __init__(self, agent_id: str) -> None:
            super().__init__()
            self.agent_id = agent_id

    def __init__(self, agent: dict, selected: bool = False) -> None:
        aid = agent.get("id", "?")
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "-", str(aid))
        super().__init__(
            classes="agent-row",
            id=f"agent-row-{safe_id}" if safe_id else None,
        )
        self._agent = agent
        self.set_selected(selected)

    def compose(self) -> ComposeResult:
        aid = str(self._agent.get("id", "?") or "?")
        display = str(self._agent.get("display_name", aid) or aid)
        hot = "● Active" if self._agent.get("is_hot") else "◌ Standby"
        provider = str(self._agent.get("provider", "") or "—")
        with Horizontal(classes="agent-row-body"):
            yield Label(hot, classes="agent-row-hot")
            yield Label(display, classes="agent-row-name")
            yield Label(provider, classes="agent-row-provider")

    @property
    def agent_id(self) -> str:
        return str(self._agent.get("id", ""))

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        if selected:
            self.add_class("selected")
        else:
            self.remove_class("selected")

    def on_click(self) -> None:
        self.post_message(self.Clicked(self.agent_id))


class _ProfileRow(Widget):
    def __init__(self, key: str, value: str) -> None:
        super().__init__(classes="profile-section-row")
        self._key = key
        self._value = value

    def compose(self) -> ComposeResult:
        yield Label(self._key, classes="profile-section-key")
        yield Label(self._value, classes="profile-section-value")


class _ProfileSection(Widget):
    """Collapsible profile section (Role, Personality, Risk, etc.)."""

    def __init__(self, title: str, rows: list[tuple[str, str]]) -> None:
        super().__init__(classes="profile-section")
        self._title = title
        self._rows = rows

    def compose(self) -> ComposeResult:
        yield Label(self._title, classes="profile-section-title")
        for key, value in self._rows:
            yield _ProfileRow(str(key), str(value))


class _ToolList(Static):
    """Tool list with allowed/blocked indicators."""

    def __init__(self, tools: list[dict]) -> None:
        super().__init__(classes="profile-section")
        self._tools = tools

    def compose(self) -> ComposeResult:
        allowed = sum(1 for t in self._tools if t.get("allowed"))
        blocked = len(self._tools) - allowed
        yield Label(
            f"TOOLS ({allowed} available, {blocked} blocked)",
            classes="profile-section-title",
        )
        for tool in self._tools:
            icon = "✓" if tool.get("allowed") else "✗"
            cls = "tool-allowed" if tool.get("allowed") else "tool-blocked"
            yield Label(f"  {icon} {tool.get('name', '?')}", classes=cls)


class _NewAgentModal(ModalScreen[tuple[str, str] | None]):
    """Modal to create a new agent profile."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="new-agent-modal"):
            yield Label("Create New Agent Profile", classes="modal-title")
            yield Label("Agent ID (lowercase, no spaces):")
            yield Input(placeholder="my-agent", id="new-agent-id")
            yield Label("Display Name:")
            yield Input(placeholder="My Agent", id="new-agent-name")
            with Horizontal(id="new-agent-buttons"):
                yield Button("Create", id="new-agent-create", variant="primary")
                yield Button("Cancel", id="new-agent-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "new-agent-create":
            aid = self.query_one("#new-agent-id", Input).value.strip()
            name = self.query_one("#new-agent-name", Input).value.strip()
            if aid:
                self.dismiss((aid, name))
            return
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        aid = self.query_one("#new-agent-id", Input).value.strip()
        name = self.query_one("#new-agent-name", Input).value.strip()
        if aid:
            self.dismiss((aid, name))

    def action_cancel(self) -> None:
        self.dismiss(None)


class _ConfirmDeleteModal(ModalScreen[bool]):
    """Confirmation modal for deleting an agent profile."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, agent_id: str) -> None:
        super().__init__()
        self._agent_id = agent_id

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-delete-modal"):
            yield Label(
                f"Delete profile for '{self._agent_id}'?",
                classes="modal-title",
            )
            yield Label("This cannot be undone.", classes="dim-hint")
            with Horizontal(id="confirm-delete-buttons"):
                yield Button("Delete", id="confirm-delete-yes", variant="error")
                yield Button("Cancel", id="confirm-delete-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-delete-yes")

    def action_cancel(self) -> None:
        self.dismiss(False)


class _EditProfileModal(ModalScreen[dict | None]):
    """Modal form for editing key profile fields."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, profile: dict) -> None:
        super().__init__()
        self._profile = profile

    def compose(self) -> ComposeResult:
        p = self._profile
        role = p.get("role", {})
        personality = p.get("personality", {})
        risk = p.get("risk", {})
        tp = p.get("tool_posture", {})
        with ScrollableContainer(id="edit-profile-modal"):
            yield Label("Edit Agent Profile", classes="modal-title")

            yield Label("Display Name:")
            yield Input(
                value=p.get("display_name", ""),
                id="edit-display-name",
            )
            yield Label("Mission:")
            yield Input(
                value=role.get("mission", ""),
                id="edit-mission",
            )
            yield Label("Tone:")
            yield Input(
                value=personality.get("tone", ""),
                id="edit-tone",
            )
            yield Label("Verbosity (terse / normal / detailed):")
            yield Input(
                value=personality.get("verbosity", "normal"),
                id="edit-verbosity",
            )
            yield Label("Risk Level (low / medium / high):")
            yield Input(
                value=risk.get("risk_level", "medium"),
                id="edit-risk-level",
            )
            yield Label("Tool Use (allowed / restricted / read_only):")
            yield Input(
                value=tp.get("tool_use", "allowed"),
                id="edit-tool-use",
            )
            yield Label("Blocked Patterns (comma-separated):")
            yield Input(
                value=", ".join(tp.get("blocked_patterns", [])),
                id="edit-blocked-patterns",
            )

            with Horizontal(id="edit-profile-buttons"):
                yield Button("Save", id="edit-save", variant="primary")
                yield Button("Cancel", id="edit-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "edit-save":
            self._save()
            return
        self.dismiss(None)

    def _save(self) -> None:
        p = dict(self._profile)
        p["display_name"] = self.query_one("#edit-display-name", Input).value.strip()

        role = dict(p.get("role", {}))
        role["mission"] = self.query_one("#edit-mission", Input).value.strip()
        p["role"] = role

        personality = dict(p.get("personality", {}))
        personality["tone"] = self.query_one("#edit-tone", Input).value.strip()
        verbosity = self.query_one("#edit-verbosity", Input).value.strip()
        if verbosity in ("terse", "normal", "detailed"):
            personality["verbosity"] = verbosity
        p["personality"] = personality

        risk = dict(p.get("risk", {}))
        risk_level = self.query_one("#edit-risk-level", Input).value.strip()
        if risk_level in ("low", "medium", "high"):
            risk["risk_level"] = risk_level
        p["risk"] = risk

        tp = dict(p.get("tool_posture", {}))
        tool_use = self.query_one("#edit-tool-use", Input).value.strip()
        if tool_use in ("allowed", "restricted", "read_only"):
            tp["tool_use"] = tool_use
        blocked_raw = self.query_one("#edit-blocked-patterns", Input).value.strip()
        tp["blocked_patterns"] = [
            b.strip() for b in blocked_raw.split(",") if b.strip()
        ]
        p["tool_posture"] = tp

        self.dismiss(p)

    def action_cancel(self) -> None:
        self.dismiss(None)


class _IdentityPreviewModal(ModalScreen[None]):
    """Read-only modal that shows a rendered identity preview."""

    BINDINGS = [("escape", "close_modal", "Close")]

    def __init__(self, *, agent_id: str, preview_text: str) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._preview_text = preview_text

    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="identity-preview-modal"):
            yield Label(
                f"Identity Preview: {self._agent_id}",
                classes="modal-title",
            )
            yield Static(self._preview_text, id="identity-preview-text")
            yield Label("Press Esc to close.", classes="dim-hint")

    def action_close_modal(self) -> None:
        self.dismiss(None)


class AgentsTab(Widget):
    can_focus = True

    BINDINGS = [
        ("n", "new_agent", "New"),
        ("e", "edit_agent", "Edit"),
        ("d", "delete_agent", "Delete"),
        ("p", "preview_identity", "Preview"),
        ("r", "refresh", "Refresh"),
    ]

    class SwitchRequested(Message):
        def __init__(self, agent_id: str) -> None:
            super().__init__()
            self.agent_id = agent_id

    def __init__(self, provider=None) -> None:
        super().__init__(id="agents-tab")
        self._provider = provider
        self._agents: list[dict] = []
        self._selected_agent_id: str | None = None
        self._detail: dict = {}
        self._tools: list[dict] = []
        self._timer: Timer | None = None
        self._loading = False

    def _iter_agent_list_panel(self) -> ComposeResult:
        if self._loading:
            yield LoadingIndicator(classes="tab-loading-indicator")
        if self._agents:
            for a in self._agents:
                yield _AgentRow(
                    a,
                    selected=(a.get("id") == self._selected_agent_id),
                )
        else:
            yield EmptyStatePulse(classes="empty-state-pulse")
            yield Label(
                "No agent profiles found.\n"
                "Press `n` to create one, or run `openminion setup`\n"
                "for guided configuration.",
                classes="dim-hint",
            )

    @staticmethod
    def _build_agent_header_rows(detail: dict, profile: dict) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = [
            ("Agent ID", detail.get("agent_id", "—")),
            ("Display", profile.get("display_name", "—")),
            ("Provider", detail.get("provider", "—")),
            ("Revision", str(profile.get("profile_revision", "—"))),
            ("Status", "hot (loaded)" if detail.get("is_hot") else "standby"),
        ]
        if detail.get("runtime_mode"):
            rows.append(("Runtime", detail.get("runtime_mode", "")))
        if detail.get("channel"):
            rows.append(("Channel", detail.get("channel", "")))
        return rows

    @staticmethod
    def _build_agent_role_rows(role: dict) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = [("Mission", role.get("mission", "—"))]
        domain = role.get("domain", [])
        if domain:
            rows.append(("Domain", ", ".join(domain)))
        responsibilities = role.get("responsibilities", [])
        if responsibilities:
            rows.append(("Duties", str(len(responsibilities)) + " items"))
        constraints = role.get("hard_constraints", [])
        if constraints:
            rows.append(("Constraints", str(len(constraints)) + " rules"))
        return rows

    @staticmethod
    def _build_agent_personality_rows(personality: dict) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = [
            ("Tone", personality.get("tone", "—")),
            ("Verbosity", personality.get("verbosity", "normal")),
        ]
        styles = personality.get("interaction_style", [])
        if styles:
            rows.append(("Style", ", ".join(styles)))
        return rows

    @staticmethod
    def _build_agent_risk_rows(risk: dict) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = [("Level", risk.get("risk_level", "—"))]
        confirm = risk.get("confirm_before", [])
        if confirm:
            rows.append(("Confirm", ", ".join(confirm)))
        return rows

    @staticmethod
    def _build_agent_tool_posture_rows(tp: dict) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = [("Mode", tp.get("tool_use", "allowed"))]
        blocked = tp.get("blocked_patterns", [])
        if blocked:
            rows.append(("Blocked", ", ".join(blocked)))
        return rows

    def _iter_agent_detail_sections(self) -> ComposeResult:
        detail = self._detail
        profile = detail.get("profile", {})

        yield Label("AGENT PROFILE", classes="sidebar-heading")
        yield _ProfileSection("INFO", self._build_agent_header_rows(detail, profile))

        role = profile.get("role", {})
        if role:
            yield _ProfileSection("ROLE", self._build_agent_role_rows(role))

        personality = profile.get("personality", {})
        if personality:
            yield _ProfileSection(
                "PERSONALITY", self._build_agent_personality_rows(personality)
            )

        risk = profile.get("risk", {})
        if risk:
            yield _ProfileSection("RISK", self._build_agent_risk_rows(risk))

        tp = profile.get("tool_posture", {})
        if tp:
            yield _ProfileSection(
                "TOOL POSTURE", self._build_agent_tool_posture_rows(tp)
            )

        if self._tools:
            yield _ToolList(self._tools)

        with Horizontal(classes="agents-actions"):
            yield Button(
                "Switch to this Agent",
                id="agents-switch-btn",
                variant="primary",
            )
            yield Button("Edit Profile", id="agents-edit-btn")
            yield Button("Delete", id="agents-delete-btn", variant="error")

    def compose(self) -> ComposeResult:
        if self._provider is None:
            yield Static(
                "No data — runtime provider not available.\n"
                "Start with a config to manage agent profiles.",
                classes="tab-empty-notice",
            )
            return

        with Horizontal(id="agents-body"):
            with Vertical(id="agents-list-panel"):
                with ScrollableContainer(id="agents-list"):
                    yield from self._iter_agent_list_panel()
                yield Button(
                    "+ New Agent", id="agents-new-btn", classes="agents-new-btn"
                )

            with ScrollableContainer(id="agents-detail"):
                if self._loading:
                    yield LoadingIndicator(classes="tab-loading-indicator")
                if not self._selected_agent_id:
                    yield Label(
                        "Select an agent to view its profile",
                        classes="dim-hint",
                    )
                    return
                yield from self._iter_agent_detail_sections()

    async def on_mount(self) -> None:
        if self._provider is not None:
            self._agents = self._provider.list_agents()
            await self.recompose()
            self._sync_layout_mode()

    def on_show(self) -> None:
        if self._provider is not None and self._timer is None:
            self._timer = self.set_interval(10, self._refresh_tick)

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
        self.run_worker(self._async_refresh(), exclusive=True)

    async def action_refresh(self) -> None:
        await self._async_refresh()

    async def _async_refresh(self) -> None:
        if self._provider is None:
            return
        self._loading = True
        await self.recompose()
        self._agents = self._provider.list_agents()
        if self._selected_agent_id:
            self._detail = self._provider.get_agent_detail(self._selected_agent_id)
            self._tools = self._provider.get_agent_tools(self._selected_agent_id)
        self._loading = False
        await self.recompose()
        self._sync_layout_mode()

    async def on__agent_row_clicked(self, event: _AgentRow.Clicked) -> None:
        self._selected_agent_id = event.agent_id
        if self._provider is not None:
            self._detail = self._provider.get_agent_detail(event.agent_id)
            self._tools = self._provider.get_agent_tools(event.agent_id)
        await self.recompose()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "agents-new-btn":
            self.action_new_agent()
        elif btn_id == "agents-switch-btn" and self._selected_agent_id:
            self.post_message(self.SwitchRequested(self._selected_agent_id))
        elif btn_id == "agents-edit-btn":
            self.action_edit_agent()
        elif btn_id == "agents-delete-btn":
            self.action_delete_agent()

    def action_new_agent(self) -> None:
        self.app.push_screen(_NewAgentModal(), self._on_new_agent)

    def action_edit_agent(self) -> None:
        if not self._selected_agent_id:
            self._notify_no_selection("edit")
            return
        profile = self._detail.get("profile", {})
        if not profile:
            self._notify_no_selection("edit")
            return
        self.app.push_screen(_EditProfileModal(profile), self._on_edit)

    def action_delete_agent(self) -> None:
        if not self._selected_agent_id:
            self._notify_no_selection("delete")
            return
        self.app.push_screen(
            _ConfirmDeleteModal(self._selected_agent_id), self._on_delete
        )

    def action_preview_identity(self) -> None:
        if not self._selected_agent_id:
            self._notify_no_selection("preview")
            return
        preview = self._render_identity_preview(self._selected_agent_id)
        self.app.push_screen(
            _IdentityPreviewModal(
                agent_id=self._selected_agent_id,
                preview_text=preview,
            )
        )

    def _notify_no_selection(self, action: str) -> None:
        key = {"delete": "d", "preview": "p", "edit": "e"}.get(action, action[0])
        message = f"Select an agent first, then press `{key}` to {action}."
        try:
            self.app.notify(message, severity="warning", timeout=3)
        except AttributeError:
            pass

    def _render_identity_preview(self, agent_id: str) -> str:
        if self._provider is not None:
            render_preview = getattr(self._provider, "render_identity_preview", None)
            if callable(render_preview):
                try:
                    preview = str(render_preview(agent_id)).strip()
                    if preview:
                        return preview
                except Exception:
                    pass
        profile = self._detail.get("profile", {})
        role = profile.get("role", {})
        personality = profile.get("personality", {})
        risk = profile.get("risk", {})
        tool_posture = profile.get("tool_posture", {})
        lines = [
            f"Agent: {agent_id}",
            f"Display: {profile.get('display_name', agent_id)}",
        ]
        mission = str(role.get("mission", "") or "").strip()
        if mission:
            lines.extend(["", "Mission", mission])
        tone = str(personality.get("tone", "") or "").strip()
        verbosity = str(personality.get("verbosity", "") or "").strip()
        if tone or verbosity:
            lines.extend(
                [
                    "",
                    "Personality",
                    f"Tone: {tone or '—'}",
                    f"Verbosity: {verbosity or '—'}",
                ]
            )
        risk_level = str(risk.get("risk_level", "") or "").strip()
        tool_use = str(tool_posture.get("tool_use", "") or "").strip()
        if risk_level or tool_use:
            lines.extend(
                [
                    "",
                    "Runtime posture",
                    f"Risk: {risk_level or '—'}",
                    f"Tool use: {tool_use or '—'}",
                ]
            )
        return "\n".join(lines).strip() or "Identity preview unavailable."

    def _on_new_agent(self, result: tuple[str, str] | None) -> None:
        if result is None or self._provider is None:
            return
        agent_id, display_name = result
        self._provider.create_default_profile(agent_id, display_name)
        self._agents = self._provider.list_agents()
        self._selected_agent_id = agent_id
        self._detail = self._provider.get_agent_detail(agent_id)
        self._tools = self._provider.get_agent_tools(agent_id)
        self.app.call_later(self.recompose)

    def _on_edit(self, result: dict | None) -> None:
        if result is None or self._provider is None or not self._selected_agent_id:
            return
        self._provider.upsert_profile(result)
        self._detail = self._provider.get_agent_detail(self._selected_agent_id)
        self._tools = self._provider.get_agent_tools(self._selected_agent_id)
        self._agents = self._provider.list_agents()
        self.app.call_later(self.recompose)

    def _on_delete(self, confirmed: bool) -> None:
        if not confirmed or self._provider is None:
            return
        self._provider.delete_profile(self._selected_agent_id)
        self._selected_agent_id = None
        self._detail = {}
        self._tools = []
        self._agents = self._provider.list_agents()
        self.app.call_later(self.recompose)

    def _sync_layout_mode(self) -> None:
        try:
            body = self.query_one("#agents-body", Horizontal)
        except QueryError:
            return
        if self.app.size.width < 100:
            body.add_class("--stacked")
        else:
            body.remove_class("--stacked")
