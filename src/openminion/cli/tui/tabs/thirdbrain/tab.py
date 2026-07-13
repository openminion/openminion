from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.css.query import QueryError
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Static

from openminion.cli.presentation import copy_to_clipboard
from .changes import (
    ThirdBrainChangeSummary,
    ThirdBrainRefreshDeltaSummary,
    ThirdBrainRunSnapshot,
    build_refresh_delta_summary,
    build_run_snapshot,
    compare_run_snapshots,
    summarize_change_summary,
    summarize_refresh_delta_summary,
)
from .inspector import build_local_map, build_provider_comparison
from .rows import (
    dom_id,
    flatten_query_payloads,
    format_history_row,
    format_result_row,
    format_saved_view_row,
    format_selection_summary,
    format_status_row,
    open_path,
    resolve_local_path,
)
from .views import SavedThirdBrainView, read_saved_views, write_saved_views
from .widgets import SelectableRow


class ThirdBrainTab(Widget):
    can_focus = True

    BINDINGS = [("/", "focus_search", "Search"), ("r", "refresh", "Refresh")]

    def __init__(
        self,
        provider=None,
        *,
        working_dir: str = "",
        data_root: Path | None = None,
    ) -> None:
        super().__init__(id="third-brain-tab")
        self._provider = provider
        self._working_dir = str(working_dir or "").strip()
        self._data_root = (
            Path(str(data_root)).resolve(strict=False)
            if data_root is not None
            else None
        )
        self._query = ""
        self._path_target = ""
        self._mode = "query"
        self._inspector_mode = "details"
        self._statuses: list[dict[str, Any]] = []
        self._selected_providers: set[str] = set()
        self._results: list[dict[str, Any]] = []
        self._selected_result_key: str | None = None
        self._history: list[dict[str, Any]] = []
        self._traversal_rows: list[dict[str, Any]] = []
        self._last_refresh: list[dict[str, Any]] = []
        self._last_refresh_delta_summary: ThirdBrainRefreshDeltaSummary | None = None
        self._last_error = ""
        self._last_result_count = 0
        self._last_runs_by_mode: dict[str, ThirdBrainRunSnapshot] = {}
        self._last_change_summary: ThirdBrainChangeSummary | None = None
        self._saved_views: list[SavedThirdBrainView] = []
        self._selected_saved_view_id: str | None = None

    def compose(self) -> ComposeResult:
        if self._provider is None:
            yield Static(
                "No data — third-brain provider not available.\n"
                "Enable Graphify or PragmaGraph in OpenMinion config to browse static graph facts.",
                classes="tab-empty-notice",
            )
            return

        with Vertical(id="third-brain-shell"):
            with Horizontal(id="third-brain-toolbar"):
                yield Input(
                    value=self._query,
                    placeholder="Search code, docs, or artifacts…",
                    id="third-brain-query",
                )
                target_classes = "third-brain-target"
                if self._mode != "path":
                    target_classes += " --hidden"
                yield Input(
                    value=self._path_target,
                    placeholder="Target entity id for path mode",
                    id="third-brain-target",
                    classes=target_classes,
                )
                with Horizontal(id="third-brain-mode-strip"):
                    yield self._mode_button("query", "Search")
                    yield self._mode_button("neighborhood", "Neighbors")
                    yield self._mode_button("path", "Path")
                yield Button(self._run_label, id="third-brain-run", variant="primary")
                yield Button("Refresh", id="third-brain-refresh")
                yield Button("Save View", id="third-brain-save-view")
            with Horizontal(id="third-brain-provider-strip"):
                if self._statuses:
                    for status in self._statuses:
                        provider = str(status.get("provider", "") or "")
                        classes = "third-brain-provider-chip"
                        if provider in self._selected_providers:
                            classes += " --selected"
                        if not status.get("ok", False):
                            classes += " --warning"
                        yield Button(
                            provider, id=f"tb-provider-{provider}", classes=classes
                        )
                else:
                    yield Label("No active third-brain providers", classes="dim-hint")
            if self._last_error:
                yield Static(self._last_error, id="third-brain-error")
            with Horizontal(id="third-brain-body"):
                with ScrollableContainer(id="third-brain-left"):
                    yield Label("RESULTS", classes="sidebar-heading")
                    if self._results:
                        for row in self._results:
                            yield SelectableRow(
                                format_result_row(row),
                                row_key=f"tb-result-{row['key']}",
                                dom_id=dom_id("tb-result", row["key"]),
                                classes=self._row_classes(row["key"]),
                            )
                    else:
                        yield Label(
                            "Run a search to inspect third-brain facts.",
                            classes="dim-hint",
                        )
                    yield Label("SAVED VIEWS", classes="sidebar-heading")
                    if self._saved_views:
                        for saved_view in self._saved_views:
                            yield SelectableRow(
                                format_saved_view_row(saved_view),
                                row_key=f"tb-saved-{saved_view.view_id}",
                                dom_id=f"tb-saved-{saved_view.view_id}",
                                classes=self._saved_view_classes(saved_view.view_id),
                            )
                        yield Button(
                            "Delete selected view", id="third-brain-delete-view"
                        )
                    else:
                        yield Label("No saved views yet", classes="dim-hint")
                    yield Label("LAST CHANGE SET", classes="sidebar-heading")
                    if self._last_change_summary is not None:
                        yield Static(
                            summarize_change_summary(self._last_change_summary),
                            classes="third-brain-change-row",
                        )
                    else:
                        yield Label(
                            "Run the same mode more than once to inspect what changed.",
                            classes="dim-hint",
                        )
                    yield Label("PROVIDER REFRESH DELTA", classes="sidebar-heading")
                    if self._last_refresh_delta_summary is not None:
                        yield Static(
                            summarize_refresh_delta_summary(
                                self._last_refresh_delta_summary
                            ),
                            classes="third-brain-change-row",
                        )
                    else:
                        yield Label(
                            "Refresh providers to inspect snapshot-backed deltas.",
                            classes="dim-hint",
                        )
                    yield Label("QUERY HISTORY", classes="sidebar-heading")
                    if self._history:
                        for index, item in enumerate(self._history[:8]):
                            yield SelectableRow(
                                format_history_row(item),
                                row_key=f"tb-history-{index}",
                                dom_id=f"tb-history-{index}",
                                classes="third-brain-history-row",
                            )
                    else:
                        yield Label("No queries yet", classes="dim-hint")
                    yield Label("PROVIDERS", classes="sidebar-heading")
                    if self._statuses:
                        for status in self._statuses:
                            yield Static(
                                format_status_row(status),
                                classes="third-brain-status-row",
                            )
                    else:
                        yield Label("No provider status", classes="dim-hint")
                with ScrollableContainer(id="third-brain-right"):
                    selected = self._selected_result()
                    if selected is None:
                        yield Label(
                            "Select a result to inspect details, provenance, raw payload, and traversal.",
                            classes="dim-hint",
                        )
                    else:
                        yield Static(
                            format_selection_summary(selected),
                            id="third-brain-summary",
                        )
                        with Horizontal(id="third-brain-action-strip"):
                            yield Button("Copy JSON", id="third-brain-copy-json")
                            yield Button("Copy Snippet", id="third-brain-copy-snippet")
                            yield Button("Open Source", id="third-brain-open-source")
                            yield Button("Export", id="third-brain-export")
                        with Horizontal(id="third-brain-inspector-strip"):
                            yield self._inspector_button("details", "Details")
                            yield self._inspector_button("provenance", "Prov")
                            yield self._inspector_button("raw", "Raw")
                            yield self._inspector_button("context", "Ctx")
                            yield self._inspector_button("changes", "Changes")
                            yield self._inspector_button("refresh", "Refresh")
                            yield self._inspector_button("compare", "Compare")
                            yield self._inspector_button("map", "Map")
                            yield self._inspector_button("traversal", "Traverse")
                        yield Static(
                            self._inspector_text(selected),
                            id="third-brain-inspector-text",
                        )
                        if self._inspector_mode == "traversal" and self._traversal_rows:
                            for row in self._traversal_rows:
                                yield SelectableRow(
                                    format_result_row(row),
                                    row_key=f"tb-traverse-{row['key']}",
                                    dom_id=dom_id("tb-traverse", row["key"]),
                                    classes="third-brain-row third-brain-traverse-row",
                                )

    async def on_mount(self) -> None:
        await self._refresh_statuses()
        self._load_saved_views()
        await self.recompose()
        self._sync_layout_mode()

    def on_resize(self, event) -> None:
        del event
        self.call_after_refresh(self._sync_layout_mode)

    def _sync_layout_mode(self) -> None:
        try:
            body = self.query_one("#third-brain-body", Horizontal)
        except QueryError:
            return
        if self.app.size.width < 110:
            body.add_class("--stacked")
        else:
            body.remove_class("--stacked")

    def action_focus_search(self) -> None:
        self.query_one("#third-brain-query", Input).focus()

    def action_refresh(self) -> None:
        self.run_worker(self._refresh_all(), exclusive=True)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in {"third-brain-query", "third-brain-target"}:
            await self._run_current_mode()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "third-brain-run":
            await self._run_current_mode()
            event.stop()
            return
        if button_id == "third-brain-refresh":
            await self._refresh_all()
            event.stop()
            return
        if button_id == "third-brain-save-view":
            self._save_current_view()
            await self.recompose()
            self._sync_layout_mode()
            event.stop()
            return
        if button_id == "third-brain-delete-view":
            self._delete_selected_saved_view()
            await self.recompose()
            self._sync_layout_mode()
            event.stop()
            return
        if button_id.startswith("tb-provider-"):
            provider = button_id.removeprefix("tb-provider-")
            self._toggle_provider(provider)
            await self.recompose()
            self._sync_layout_mode()
            event.stop()
            return
        if button_id.startswith("tb-mode-"):
            self._mode = button_id.removeprefix("tb-mode-")
            await self.recompose()
            self._sync_layout_mode()
            event.stop()
            return
        if button_id.startswith("tb-inspector-"):
            self._inspector_mode = button_id.removeprefix("tb-inspector-")
            await self.recompose()
            self._sync_layout_mode()
            event.stop()
            return
        if button_id == "third-brain-copy-json":
            self._copy_text(
                json.dumps(self._selected_raw_payload(), indent=2, sort_keys=True)
            )
            event.stop()
            return
        if button_id == "third-brain-copy-snippet":
            selected = self._selected_result()
            self._copy_text(str((selected or {}).get("snippet", "") or ""))
            event.stop()
            return
        if button_id == "third-brain-open-source":
            self._open_selected_source()
            event.stop()
            return
        if button_id == "third-brain-export":
            self._export_selected_payload()
            event.stop()

    async def on_selectable_row_clicked(self, event: SelectableRow.Clicked) -> None:
        row_key = event.row_key
        if row_key.startswith("tb-result-"):
            self._selected_result_key = row_key.removeprefix("tb-result-")
            await self.recompose()
            self._sync_layout_mode()
            return
        if row_key.startswith("tb-traverse-"):
            self._selected_result_key = row_key.removeprefix("tb-traverse-")
            selected = self._selected_result()
            if selected is None:
                traverse_match = next(
                    (
                        row
                        for row in self._traversal_rows
                        if row["key"] == self._selected_result_key
                    ),
                    None,
                )
                if traverse_match is not None:
                    self._results.insert(0, traverse_match)
            await self.recompose()
            self._sync_layout_mode()
            return
        if row_key.startswith("tb-history-"):
            try:
                index = int(row_key.removeprefix("tb-history-"))
            except ValueError:
                return
            if 0 <= index < len(self._history):
                entry = self._history[index]
                self._mode = str(entry.get("mode", "query") or "query")
                self._query = str(entry.get("query", "") or "")
                self._path_target = str(entry.get("target", "") or "")
                self._selected_providers = set(entry.get("providers", []) or ())
                await self._run_current_mode()
            return
        if row_key.startswith("tb-saved-"):
            saved_view_id = row_key.removeprefix("tb-saved-")
            self._selected_saved_view_id = saved_view_id
            saved_view = self._saved_view_by_id(saved_view_id)
            if saved_view is not None:
                await self._run_saved_view(saved_view)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            focused = self.app.focused
            if focused is not None and getattr(focused, "id", None) in {
                "third-brain-query",
                "third-brain-target",
            }:
                focused.blur()
                event.stop()

    async def _refresh_all(self) -> None:
        await self._refresh_statuses()
        if self._provider is None:
            return
        refreshable = self._selected_provider_names(capability="refresh")
        if refreshable:
            try:
                self._last_refresh = self._provider.refresh(provider_names=refreshable)
                self._last_refresh_delta_summary = build_refresh_delta_summary(
                    self._last_refresh
                )
                self._last_error = ""
            except Exception as exc:
                self._last_error = f"Refresh failed: {exc}"
        await self.recompose()
        self._sync_layout_mode()

    async def _refresh_statuses(self) -> None:
        if self._provider is None:
            self._statuses = []
            self._selected_providers = set()
            return
        try:
            self._statuses = list(self._provider.list_provider_status())
            self._last_error = ""
        except Exception as exc:
            self._statuses = []
            self._last_error = f"Provider status failed: {exc}"
        self._ensure_selected_providers()

    def _ensure_selected_providers(self) -> None:
        available = {str(item.get("provider", "") or "") for item in self._statuses}
        self._selected_providers = {
            name for name in self._selected_providers if name in available
        }
        if not self._selected_providers:
            self._selected_providers = {name for name in available if name}

    def _toggle_provider(self, provider: str) -> None:
        provider = str(provider or "").strip()
        if not provider:
            return
        if provider in self._selected_providers and len(self._selected_providers) > 1:
            self._selected_providers.remove(provider)
        else:
            self._selected_providers.add(provider)

    async def _run_current_mode(self) -> None:
        if self._provider is None:
            return
        query = self._input_value("#third-brain-query")
        self._query = query
        self._path_target = self._input_value("#third-brain-target")
        try:
            if self._mode == "query":
                await self._run_query(query)
            elif self._mode == "neighborhood":
                await self._run_neighborhood()
            else:
                await self._run_path()
        except Exception as exc:
            self._last_error = f"Third-brain query failed: {exc}"
            await self.recompose()
            self._sync_layout_mode()

    async def _run_saved_view(self, saved_view: SavedThirdBrainView) -> None:
        if self._provider is None:
            return
        self._mode = saved_view.mode
        self._query = saved_view.query
        self._path_target = saved_view.target
        self._selected_providers = set(saved_view.providers)
        self._ensure_selected_providers()
        try:
            if saved_view.mode == "query":
                await self._run_query(saved_view.query)
                return
            providers = self._selected_provider_names(capability=saved_view.mode)
            if not providers:
                self._last_error = (
                    f"Selected providers do not advertise {saved_view.mode}."
                )
                await self.recompose()
                return
            if not saved_view.source_entity_id:
                self._last_error = "Saved view is missing a source entity id."
                await self.recompose()
                return
            if saved_view.mode == "neighborhood":
                payloads = self._provider.neighborhood(
                    saved_view.source_entity_id,
                    provider_names=providers,
                )
                self._apply_traversal_payloads(payloads, mode="neighborhood", target="")
                return
            if not saved_view.target:
                self._last_error = "Saved path view is missing a target entity id."
                await self.recompose()
                return
            payloads = self._provider.path(
                saved_view.source_entity_id,
                saved_view.target,
                provider_names=providers,
            )
            self._apply_traversal_payloads(
                payloads,
                mode="path",
                target=saved_view.target,
            )
        except Exception as exc:
            self._last_error = f"Saved view failed: {exc}"
            await self.recompose()
            self._sync_layout_mode()

    async def _run_query(self, query: str) -> None:
        if not query:
            self._last_error = "Enter a query to search the third brain."
            await self.recompose()
            return
        payloads = self._provider.search(
            query,
            provider_names=self._selected_provider_names(),
        )
        self._apply_query_payloads(payloads, mode="query", query=query)

    async def _run_neighborhood(self) -> None:
        selected = self._selected_result()
        if selected is None:
            self._last_error = "Select a result before loading neighbors."
            await self.recompose()
            return
        providers = self._selected_provider_names(capability="neighborhood")
        if not providers:
            self._last_error = "Selected providers do not advertise neighborhood."
            await self.recompose()
            return
        payloads = self._provider.neighborhood(
            selected["node_or_edge_id"],
            provider_names=providers,
        )
        self._apply_traversal_payloads(payloads, mode="neighborhood", target="")

    async def _run_path(self) -> None:
        selected = self._selected_result()
        if selected is None:
            self._last_error = "Select a source result before loading a path."
            await self.recompose()
            return
        if not self._path_target:
            self._last_error = "Enter a target entity id for path mode."
            await self.recompose()
            return
        providers = self._selected_provider_names(capability="path")
        if not providers:
            self._last_error = "Selected providers do not advertise path traversal."
            await self.recompose()
            return
        payloads = self._provider.path(
            selected["node_or_edge_id"],
            self._path_target,
            provider_names=providers,
        )
        self._apply_traversal_payloads(payloads, mode="path", target=self._path_target)

    def _apply_query_payloads(
        self,
        payloads: list[dict[str, Any]],
        *,
        mode: str,
        query: str,
    ) -> None:
        self._last_error = ""
        self._traversal_rows = []
        self._results = flatten_query_payloads(payloads)
        self._record_change_summary(
            payloads,
            rows=self._results,
            mode=mode,
            query=query,
            target="",
        )
        self._last_result_count = len(self._results)
        self._selected_result_key = self._results[0]["key"] if self._results else None
        self._history.insert(
            0,
            {
                "mode": mode,
                "query": query,
                "target": "",
                "providers": sorted(self._selected_providers),
                "count": self._last_result_count,
                "ts": datetime.now().strftime("%H:%M:%S"),
            },
        )
        self._history = self._history[:8]
        self.run_worker(self.recompose(), exclusive=True)

    def _apply_traversal_payloads(
        self,
        payloads: list[dict[str, Any]],
        *,
        mode: str,
        target: str,
    ) -> None:
        self._last_error = ""
        self._traversal_rows = flatten_query_payloads(payloads)
        self._record_change_summary(
            payloads,
            rows=self._traversal_rows,
            mode=mode,
            query=self._query,
            target=target,
        )
        self._selected_result_key = (
            self._traversal_rows[0]["key"] if self._traversal_rows else None
        )
        self._inspector_mode = "traversal"
        self._history.insert(
            0,
            {
                "mode": mode,
                "query": self._query,
                "target": target,
                "providers": sorted(self._selected_providers),
                "count": len(self._traversal_rows),
                "ts": datetime.now().strftime("%H:%M:%S"),
            },
        )
        self._history = self._history[:8]
        self.run_worker(self.recompose(), exclusive=True)

    def _selected_result(self) -> dict[str, Any] | None:
        if not self._selected_result_key:
            return None
        for row in self._results:
            if row["key"] == self._selected_result_key:
                return row
        for row in self._traversal_rows:
            if row["key"] == self._selected_result_key:
                return row
        return None

    def _inspector_text(self, selected: dict[str, Any]) -> str:
        if self._inspector_mode == "details":
            payload = {
                "provider": selected["provider"],
                "layer": selected["layer"],
                "node_or_edge_id": selected["node_or_edge_id"],
                "path": selected["path"],
                "line": selected.get("line"),
                "snippet": selected.get("snippet"),
                "metadata": selected.get("metadata", {}),
                "item_count": len(selected.get("raw_envelope", {}).get("items", [])),
                "omitted_count": len(
                    selected.get("raw_envelope", {}).get("omitted", [])
                ),
            }
            return json.dumps(payload, indent=2, sort_keys=True)
        if self._inspector_mode == "provenance":
            payload = {
                "provider": selected["provider"],
                "graph_id": selected.get("graph_id", ""),
                "tags": selected.get("tags", []),
                "source_ref": selected.get("raw_item", {}).get("source_ref", {}),
                "diagnostics": selected.get("diagnostics", {}),
                "omitted": selected.get("raw_envelope", {}).get("omitted", []),
            }
            return json.dumps(payload, indent=2, sort_keys=True)
        if self._inspector_mode == "raw":
            return json.dumps(self._selected_raw_payload(), indent=2, sort_keys=True)
        if self._inspector_mode == "context":
            return json.dumps(
                self._agent_context_preview(selected), indent=2, sort_keys=True
            )
        if self._inspector_mode == "changes":
            if self._last_change_summary is None:
                return (
                    "No change set yet. Run the same mode more than once to inspect "
                    "run-to-run differences."
                )
            return json.dumps(
                self._last_change_summary.to_dict(),
                indent=2,
                sort_keys=True,
            )
        if self._inspector_mode == "refresh":
            if self._last_refresh_delta_summary is None:
                return "No provider refresh delta yet. Press Refresh to load provider-backed refresh facts."
            return json.dumps(
                self._last_refresh_delta_summary.to_dict(),
                indent=2,
                sort_keys=True,
            )
        if self._inspector_mode == "compare":
            return json.dumps(
                build_provider_comparison(
                    selected=selected,
                    rows=self._active_rows_for_selected(),
                    provider_names=tuple(sorted(self._selected_providers)),
                ),
                indent=2,
                sort_keys=True,
            )
        if self._inspector_mode == "map":
            return json.dumps(
                build_local_map(
                    selected=selected,
                    traversal_rows=list(self._traversal_rows),
                    mode=self._mode,
                    target=self._path_target,
                ),
                indent=2,
                sort_keys=True,
            )
        if not self._traversal_rows:
            return "No traversal loaded yet. Choose Neighbors or Path and run it from the selected result."
        return json.dumps(
            {
                "mode": self._mode,
                "rows": [
                    {
                        "provider": row["provider"],
                        "node_or_edge_id": row["node_or_edge_id"],
                        "path": row["path"],
                    }
                    for row in self._traversal_rows
                ],
            },
            indent=2,
            sort_keys=True,
        )

    def _selected_raw_payload(self) -> dict[str, Any]:
        selected = self._selected_result()
        if selected is None:
            return {}
        return dict(selected.get("raw_envelope", {}) or {})

    def _active_rows_for_selected(self) -> list[dict[str, Any]]:
        selected = self._selected_result()
        if selected is None:
            return []
        if any(row["key"] == selected["key"] for row in self._traversal_rows):
            return list(self._traversal_rows)
        return list(self._results)

    def _agent_context_preview(self, selected: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider": selected["provider"],
            "layer": selected["layer"],
            "items": [dict(selected.get("raw_item", {}) or {})],
            "omitted": list(selected.get("raw_envelope", {}).get("omitted", []) or []),
            "diagnostics": dict(selected.get("diagnostics", {}) or {}),
        }

    def _row_classes(self, key: str) -> str:
        classes = "third-brain-row"
        if key == self._selected_result_key:
            classes += " selected"
        return classes

    def _saved_view_classes(self, view_id: str) -> str:
        classes = "third-brain-saved-row"
        if view_id == self._selected_saved_view_id:
            classes += " selected"
        return classes

    def _selected_provider_names(self, capability: str | None = None) -> list[str]:
        names = []
        for status in self._statuses:
            provider = str(status.get("provider", "") or "")
            if provider not in self._selected_providers:
                continue
            capabilities = set(status.get("capabilities", []) or [])
            if capability is not None and capability not in capabilities:
                continue
            names.append(provider)
        return names

    def _mode_button(self, mode: str, label: str) -> Button:
        classes = "third-brain-mode-btn"
        if mode == self._mode:
            classes += " --selected"
        if mode == "neighborhood" and not self._selected_provider_names("neighborhood"):
            classes += " --disabled"
        if mode == "path" and not self._selected_provider_names("path"):
            classes += " --disabled"
        return Button(label, id=f"tb-mode-{mode}", classes=classes)

    def _inspector_button(self, mode: str, label: str) -> Button:
        classes = "third-brain-inspector-btn"
        if mode == self._inspector_mode:
            classes += " --selected"
        return Button(label, id=f"tb-inspector-{mode}", classes=classes)

    @property
    def _run_label(self) -> str:
        return {
            "query": "Search",
            "neighborhood": "Load neighbors",
            "path": "Load path",
        }.get(self._mode, "Run")

    def _input_value(self, selector: str) -> str:
        try:
            return str(self.query_one(selector, Input).value or "").strip()
        except QueryError:
            return ""

    def _record_change_summary(
        self,
        payloads: list[dict[str, Any]],
        *,
        rows: list[dict[str, Any]],
        mode: str,
        query: str,
        target: str,
    ) -> None:
        current = build_run_snapshot(
            payloads=payloads,
            rows=rows,
            mode=mode,
            query=query,
            target=target,
            providers=tuple(sorted(self._selected_providers)),
        )
        previous = self._last_runs_by_mode.get(mode)
        self._last_change_summary = compare_run_snapshots(previous, current)
        self._last_runs_by_mode[mode] = current

    def _load_saved_views(self) -> None:
        if self._data_root is None:
            self._saved_views = []
            self._selected_saved_view_id = None
            return
        self._saved_views = read_saved_views(self._data_root)
        if self._selected_saved_view_id and self._saved_view_by_id(
            self._selected_saved_view_id
        ):
            return
        self._selected_saved_view_id = (
            self._saved_views[0].view_id if self._saved_views else None
        )

    def _save_current_view(self) -> None:
        if self._data_root is None:
            self.app.notify(
                "Saved views are unavailable because data_root is missing.",
                severity="warning",
                timeout=3,
            )
            return
        saved_view = self._build_saved_view()
        if saved_view is None:
            return
        self._saved_views.insert(0, saved_view)
        self._saved_views = self._saved_views[:12]
        self._selected_saved_view_id = saved_view.view_id
        write_saved_views(self._data_root, self._saved_views)
        self.app.notify(f"Saved view: {saved_view.name}", timeout=2)

    def _delete_selected_saved_view(self) -> None:
        if self._data_root is None or self._selected_saved_view_id is None:
            self.app.notify("No saved view selected.", severity="warning", timeout=3)
            return
        before = len(self._saved_views)
        self._saved_views = [
            view
            for view in self._saved_views
            if view.view_id != self._selected_saved_view_id
        ]
        if len(self._saved_views) == before:
            self.app.notify("No saved view selected.", severity="warning", timeout=3)
            return
        self._selected_saved_view_id = (
            self._saved_views[0].view_id if self._saved_views else None
        )
        write_saved_views(self._data_root, self._saved_views)
        self.app.notify("Deleted saved view.", timeout=2)

    def _build_saved_view(self) -> SavedThirdBrainView | None:
        providers = tuple(sorted(self._selected_providers))
        source_entity_id = ""
        if self._mode in {"neighborhood", "path"}:
            selected = self._selected_result()
            if selected is None:
                self.app.notify(
                    "Select a result before saving a traversal view.",
                    severity="warning",
                    timeout=3,
                )
                return None
            source_entity_id = str(selected.get("node_or_edge_id", "") or "").strip()
            if not source_entity_id:
                self.app.notify(
                    "Traversal view is missing a source entity id.",
                    severity="warning",
                    timeout=3,
                )
                return None
        if self._mode == "query" and not self._query:
            self.app.notify(
                "Run a search before saving a query view.",
                severity="warning",
                timeout=3,
            )
            return None
        created_at = datetime.now().isoformat(timespec="seconds")
        return SavedThirdBrainView(
            view_id=uuid4().hex,
            name=self._saved_view_name(),
            mode=self._mode,
            query=self._query,
            target=self._path_target,
            providers=providers,
            source_entity_id=source_entity_id,
            created_at=created_at,
        )

    def _saved_view_name(self) -> str:
        label = self._query or self._path_target or "Saved view"
        if self._mode == "neighborhood":
            selected = self._selected_result()
            source_id = str((selected or {}).get("node_or_edge_id", "") or "").strip()
            if source_id:
                label = source_id
        if self._mode == "path":
            selected = self._selected_result()
            source_id = str((selected or {}).get("node_or_edge_id", "") or "").strip()
            if source_id and self._path_target:
                label = f"{source_id} -> {self._path_target}"
        return f"{self._mode}: {label}"[:80]

    def _saved_view_by_id(self, view_id: str) -> SavedThirdBrainView | None:
        for saved_view in self._saved_views:
            if saved_view.view_id == view_id:
                return saved_view
        return None

    def _copy_text(self, text: str) -> None:
        if not text:
            self.app.notify("Nothing to copy.", severity="warning", timeout=3)
            return
        from . import copy_to_clipboard as copy_hook

        if copy_hook(text):
            self.app.notify("Copied to clipboard.", timeout=2)
            return
        self.app.notify(
            "No clipboard backend available on this machine.",
            severity="warning",
            timeout=3,
        )

    def _open_selected_source(self) -> None:
        selected = self._selected_result()
        if selected is None:
            self.app.notify("Select a result first.", severity="warning", timeout=3)
            return
        raw_path = str(selected.get("path", "") or "").strip()
        path = resolve_local_path(raw_path, working_dir=self._working_dir)
        if path is None or not path.exists():
            self.app.notify(
                "Selected result does not point to a local source path.",
                severity="warning",
                timeout=3,
            )
            return
        from . import open_path as open_hook

        if open_hook(path):
            self.app.notify(f"Opened {path}", timeout=2)
            return
        self.app.notify(
            f"Could not open {path}",
            severity="warning",
            timeout=3,
        )

    def _export_selected_payload(self) -> None:
        selected = self._selected_result()
        if selected is None:
            self.app.notify("Select a result first.", severity="warning", timeout=3)
            return
        export_dir = Path(tempfile.gettempdir()) / "openminion-third-brain-exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        target = (
            export_dir
            / f"{selected['provider']}-{selected['node_or_edge_id'].replace(':', '_')}.json"
        )
        target.write_text(
            json.dumps(self._selected_raw_payload(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        self.app.notify(f"Exported {target}", timeout=3)


__all__ = ["ThirdBrainTab", "copy_to_clipboard", "open_path"]
