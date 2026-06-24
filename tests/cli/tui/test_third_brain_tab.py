from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION, ProviderBundle
from openminion.cli.tui.app import OpenMinionApp
from openminion.cli.tui.tabs.thirdbrain import ThirdBrainTab
from openminion.cli.tui.tabs.thirdbrain import saved_views_path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SERVICE_PATH = str(
    _REPO_ROOT
    / "openminion"
    / "src"
    / "openminion"
    / "modules"
    / "context"
    / "knowledge"
    / "service.py"
)


class _SpyThirdBrainProvider:
    contract_version = CLI_INTERFACE_VERSION

    def __init__(
        self,
        *,
        provider_name: str = "spygraph",
        capabilities: list[str] | None = None,
    ) -> None:
        self._provider_name = provider_name
        self._capabilities = capabilities or ["query", "neighborhood", "refresh"]
        self.search_calls = 0
        self.neighborhood_calls = 0
        self.refresh_calls = 0

    def list_provider_status(self) -> list[dict]:
        return [
            {
                "provider": self._provider_name,
                "layer": "provider",
                "ok": True,
                "detail": "ready",
                "tags": ["code_graph"],
                "capabilities": list(self._capabilities),
                "diagnostics": {"graph_id": f"{self._provider_name}-v1"},
            }
        ]

    def search(
        self,
        query: str,
        *,
        provider_names: list[str] | None = None,
        max_results: int = 20,
    ) -> list[dict]:
        del provider_names, max_results
        self.search_calls += 1
        normalized = str(query or "").strip().lower()
        if normalized == "boundary":
            node_or_edge_id = "doc:spy.boundary"
            snippet = "boundary note"
            line = 211
            omitted = [
                {
                    "provider": self._provider_name,
                    "node_or_edge_id": "symbol:spy.omitted",
                    "reason": "budget",
                    "details": {"limit": 1},
                }
            ]
        else:
            node_or_edge_id = "symbol:spy.result"
            snippet = f"match for {query}"
            line = 101
            omitted = []
        return [
            {
                "provider": self._provider_name,
                "layer": "provider",
                "tags": ["code_graph"],
                "items": [
                    {
                        "provider": self._provider_name,
                        "source_graph_id": f"{self._provider_name}-v1",
                        "node_or_edge_id": node_or_edge_id,
                        "source_ref": {
                            "path": _SERVICE_PATH,
                            "line": line,
                            "page": None,
                            "span": None,
                        },
                        "snippet": snippet,
                        "score": 0.9,
                        "metadata": {"kind": "symbol"},
                    }
                ],
                "paths": [],
                "omitted": omitted,
                "diagnostics": {"graph_id": f"{self._provider_name}-v1"},
            }
        ]

    def neighborhood(
        self,
        entity_id: str,
        *,
        provider_names: list[str] | None = None,
        depth: int = 1,
        max_results: int = 20,
    ) -> list[dict]:
        del provider_names, depth, max_results
        self.neighborhood_calls += 1
        return [
            {
                "provider": self._provider_name,
                "layer": "provider",
                "tags": ["code_graph"],
                "items": [
                    {
                        "provider": self._provider_name,
                        "source_graph_id": f"{self._provider_name}-v1",
                        "node_or_edge_id": entity_id,
                        "source_ref": {
                            "path": _SERVICE_PATH,
                            "line": 101,
                            "page": None,
                            "span": None,
                        },
                        "snippet": "selected node",
                        "score": 1.0,
                        "metadata": {"kind": "symbol"},
                    },
                    {
                        "provider": self._provider_name,
                        "source_graph_id": f"{self._provider_name}-v1",
                        "node_or_edge_id": "symbol:spy.neighbor",
                        "source_ref": {
                            "path": _SERVICE_PATH,
                            "line": 152,
                            "page": None,
                            "span": None,
                        },
                        "snippet": "neighbor node",
                        "score": 0.82,
                        "metadata": {"kind": "symbol"},
                    },
                ],
                "paths": [],
                "omitted": [],
                "diagnostics": {"graph_id": f"{self._provider_name}-v1"},
            }
        ]

    def path(self, *args, **kwargs) -> list[dict]:
        raise AssertionError("path should not be called in this test")

    def refresh(
        self,
        *,
        provider_names: list[str] | None = None,
        full: bool = False,
    ) -> list[dict]:
        del provider_names, full
        self.refresh_calls += 1
        return [
            {
                "provider": self._provider_name,
                "layer": "provider",
                "ok": True,
                "refreshed_at": "now",
                "counts": {"items": 2},
                "diagnostics": {"mode": "manual"},
            }
        ]


class _CoenabledThirdBrainProvider:
    contract_version = CLI_INTERFACE_VERSION

    def list_provider_status(self) -> list[dict]:
        return [
            {
                "provider": "graphify",
                "layer": "provider",
                "ok": True,
                "detail": "ready",
                "tags": ["code_graph"],
                "capabilities": ["query", "neighborhood", "refresh"],
                "diagnostics": {"graph_id": "graphify-v1"},
            },
            {
                "provider": "pragmagraph",
                "layer": "provider",
                "ok": True,
                "detail": "ready",
                "tags": ["code_graph"],
                "capabilities": ["query", "neighborhood", "refresh"],
                "diagnostics": {"graph_id": "pragmagraph-v1"},
            },
        ]

    def search(
        self,
        query: str,
        *,
        provider_names: list[str] | None = None,
        max_results: int = 20,
    ) -> list[dict]:
        del query, max_results
        names = provider_names or ["graphify", "pragmagraph"]
        payloads: list[dict] = []
        for provider_name in names:
            payloads.append(
                {
                    "provider": provider_name,
                    "layer": "provider",
                    "tags": ["code_graph"],
                    "items": [
                        {
                            "provider": provider_name,
                            "source_graph_id": f"{provider_name}-v1",
                            "node_or_edge_id": "symbol:shared.service",
                            "source_ref": {
                                "path": _SERVICE_PATH,
                                "line": 144,
                                "page": None,
                                "span": None,
                            },
                            "snippet": f"{provider_name} shared result",
                            "score": 0.91,
                            "metadata": {"kind": "symbol"},
                        }
                    ],
                    "paths": [],
                    "omitted": [],
                    "diagnostics": {"graph_id": f"{provider_name}-v1"},
                }
            )
        return payloads

    def neighborhood(self, *args, **kwargs) -> list[dict]:
        raise AssertionError("neighborhood should not be called in this test")

    def path(self, *args, **kwargs) -> list[dict]:
        raise AssertionError("path should not be called in this test")

    def refresh(self, *args, **kwargs) -> list[dict]:
        del args, kwargs
        return []


class _BridgeRuntime:
    contract_version = CLI_INTERFACE_VERSION

    def __init__(self, *, data_root: Path) -> None:
        self._agent_id = "bridge-agent"
        self._session_id = "sess-third-brain"
        self._transport = "gateway"
        self._working_dir = str(_REPO_ROOT)
        self._rt = SimpleNamespace(
            config=SimpleNamespace(
                runtime=SimpleNamespace(process_mode="single-process")
            ),
            config_path="/tmp/openminion-test.json",
            data_root=str(data_root),
        )

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def transport(self) -> str:
        return self._transport

    @property
    def working_dir(self) -> str:
        return self._working_dir

    def get_current_history(self) -> list[object]:
        return []

    def list_sessions(self) -> list[object]:
        return []

    def list_agents(self) -> list[object]:
        return []

    def list_tools(self) -> list[tuple[str, bool]]:
        return []

    def switch_session(self, session_id: str) -> list[object]:
        self._session_id = session_id
        return []

    def switch_agent(self, agent_id: str) -> None:
        self._agent_id = agent_id

    def new_session(self) -> str:
        self._session_id = "sess-third-brain-2"
        return self._session_id


@pytest.mark.asyncio
async def test_third_brain_tab_search_and_context_preview() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-third-brain")
        await pilot.pause()

        query = app.screen.query_one("#third-brain-query")
        query.value = "KnowledgeGraphService"
        await pilot.click("#third-brain-run")
        await pilot.pause()
        await pilot.pause()

        rows = app.screen.query("#third-brain-left .third-brain-row")
        assert len(rows) >= 1

        first_row = rows.first()
        assert first_row.id is not None
        await pilot.click(f"#{first_row.id}")
        await pilot.pause()
        app.screen.query_one("#tb-inspector-context").press()
        await pilot.pause()

        inspector = app.screen.query_one("#third-brain-inspector-text")
        rendered = str(inspector.render())
        assert "provider" in rendered
        assert "items" in rendered
        assert "pragmagraph" in rendered


@pytest.mark.asyncio
async def test_third_brain_tab_supports_neighborhood_and_history() -> None:
    spy = _SpyThirdBrainProvider()
    app = OpenMinionApp(providers=ProviderBundle(provider=spy))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-third-brain")
        await pilot.pause()

        query = app.screen.query_one("#third-brain-query")
        query.value = "service"
        await pilot.click("#third-brain-run")
        await pilot.pause()
        await pilot.pause()

        assert spy.search_calls == 1

        await pilot.click("#tb-mode-neighborhood")
        await pilot.pause()
        await pilot.click("#third-brain-run")
        await pilot.pause()
        await pilot.pause()

        assert spy.neighborhood_calls == 1
        history_rows = app.screen.query("#third-brain-left .third-brain-history-row")
        assert len(history_rows) >= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["graphify", "pragmagraph"])
async def test_third_brain_tab_uses_same_ui_contract_for_single_provider_postures(
    provider_name: str,
) -> None:
    app = OpenMinionApp(
        providers=ProviderBundle(
            provider=_SpyThirdBrainProvider(provider_name=provider_name)
        )
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-third-brain")
        await pilot.pause()

        query = app.screen.query_one("#third-brain-query")
        query.value = "service"
        await pilot.click("#third-brain-run")
        await pilot.pause()
        await pilot.pause()

        rows = app.screen.query("#third-brain-left .third-brain-row")
        assert len(rows) == 1
        assert provider_name in str(rows.first().render())


@pytest.mark.asyncio
async def test_third_brain_tab_reports_missing_path_capability() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-third-brain")
        await pilot.pause()

        query = app.screen.query_one("#third-brain-query")
        query.value = "boundary"
        await pilot.click("#third-brain-run")
        await pilot.pause()
        await pilot.pause()

        await pilot.click("#tb-provider-pragmagraph")
        await pilot.pause()
        tab = app.screen.query_one(ThirdBrainTab)
        path_button = app.screen.query_one("#tb-mode-path")
        assert "--disabled" in str(path_button.classes)

        await pilot.click("#tb-mode-path")
        await pilot.pause()

        assert tab._mode == "query"
        assert tab._selected_provider_names("path") == []


@pytest.mark.asyncio
async def test_third_brain_tab_copy_open_and_refresh_actions_are_callable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openminion.cli.tui.tabs import thirdbrain as third_brain_module

    copied: list[str] = []
    opened: list[str] = []

    monkeypatch.setattr(
        third_brain_module,
        "copy_to_clipboard",
        lambda text: copied.append(str(text)) or True,
    )
    monkeypatch.setattr(
        third_brain_module,
        "open_path",
        lambda path: opened.append(str(path)) or True,
    )

    spy = _SpyThirdBrainProvider()
    app = OpenMinionApp(providers=ProviderBundle(provider=spy))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-third-brain")
        await pilot.pause()

        query = app.screen.query_one("#third-brain-query")
        query.value = "service"
        await pilot.click("#third-brain-run")
        await pilot.pause()
        await pilot.pause()

        app.screen.query_one("#third-brain-copy-json").press()
        await pilot.pause()
        app.screen.query_one("#third-brain-open-source").press()
        await pilot.pause()
        app.screen.query_one("#third-brain-refresh").press()
        await pilot.pause()
        await pilot.pause()

        assert copied
        assert opened
        assert spy.refresh_calls == 1


@pytest.mark.asyncio
async def test_third_brain_tab_can_save_and_restore_query_views(
    tmp_path: Path,
) -> None:
    spy = _SpyThirdBrainProvider()
    app = OpenMinionApp(
        runtime=_BridgeRuntime(data_root=tmp_path),
        providers=ProviderBundle(provider=spy),
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-third-brain")
        await pilot.pause()

        query = app.screen.query_one("#third-brain-query")
        query.value = "service"
        await pilot.click("#third-brain-run")
        await pilot.pause()
        await pilot.pause()

        assert spy.search_calls == 1

        await pilot.click("#third-brain-save-view")
        await pilot.pause()

        persisted = saved_views_path(tmp_path)
        assert persisted.exists()
        payload = json.loads(persisted.read_text(encoding="utf-8"))
        assert len(payload["saved_views"]) == 1
        assert payload["saved_views"][0]["mode"] == "query"
        assert payload["saved_views"][0]["query"] == "service"

    app = OpenMinionApp(
        runtime=_BridgeRuntime(data_root=tmp_path),
        providers=ProviderBundle(provider=spy),
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-third-brain")
        await pilot.pause()

        tab = app.screen.query_one(ThirdBrainTab)
        assert len(tab._saved_views) == 1

        await tab._run_saved_view(tab._saved_views[0])
        await pilot.pause()

        assert spy.search_calls == 2
        assert app.screen.query_one("#third-brain-query").value == "service"


@pytest.mark.asyncio
async def test_third_brain_tab_can_save_and_restore_neighborhood_views(
    tmp_path: Path,
) -> None:
    spy = _SpyThirdBrainProvider()
    app = OpenMinionApp(
        runtime=_BridgeRuntime(data_root=tmp_path),
        providers=ProviderBundle(provider=spy),
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-third-brain")
        await pilot.pause()

        query = app.screen.query_one("#third-brain-query")
        query.value = "service"
        await pilot.click("#third-brain-run")
        await pilot.pause()
        await pilot.pause()

        result_rows = app.screen.query("#third-brain-left .third-brain-row")
        result_row = result_rows.first()
        assert result_row.id is not None
        await pilot.click(f"#{result_row.id}")
        await pilot.pause()
        await pilot.click("#tb-mode-neighborhood")
        await pilot.pause()
        await pilot.click("#third-brain-run")
        await pilot.pause()
        await pilot.pause()

        assert spy.neighborhood_calls == 1

        await pilot.click("#third-brain-save-view")
        await pilot.pause()

        payload = json.loads(saved_views_path(tmp_path).read_text(encoding="utf-8"))
        assert payload["saved_views"][0]["mode"] == "neighborhood"
        assert payload["saved_views"][0]["source_entity_id"] == "symbol:spy.result"

    app = OpenMinionApp(
        runtime=_BridgeRuntime(data_root=tmp_path),
        providers=ProviderBundle(provider=spy),
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-third-brain")
        await pilot.pause()

        tab = app.screen.query_one(ThirdBrainTab)
        assert len(tab._saved_views) == 1

        await tab._run_saved_view(tab._saved_views[0])
        await pilot.pause()
        await pilot.pause()

        assert spy.neighborhood_calls == 2
        selected = tab._selected_result()
        assert selected is not None
        assert selected["node_or_edge_id"] == "symbol:spy.result"


@pytest.mark.asyncio
async def test_third_brain_tab_tracks_run_to_run_change_sets() -> None:
    spy = _SpyThirdBrainProvider()
    app = OpenMinionApp(providers=ProviderBundle(provider=spy))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-third-brain")
        await pilot.pause()

        query = app.screen.query_one("#third-brain-query")
        query.value = "service"
        await pilot.click("#third-brain-run")
        await pilot.pause()
        await pilot.pause()

        query = app.screen.query_one("#third-brain-query")
        query.value = "boundary"
        await pilot.click("#third-brain-run")
        await pilot.pause()
        await pilot.pause()

        tab = app.screen.query_one(ThirdBrainTab)
        summary = tab._last_change_summary
        assert summary is not None
        assert summary.had_previous_run is True
        assert summary.previous_query == "service"
        assert summary.query == "boundary"
        assert summary.added_result_ids == (
            f"{spy._provider_name}|doc:spy.boundary|{_SERVICE_PATH}|211",
        )
        assert summary.removed_result_ids == (
            f"{spy._provider_name}|symbol:spy.result|{_SERVICE_PATH}|101",
        )
        assert summary.added_omitted_ids == (
            f"{spy._provider_name}|symbol:spy.omitted|budget",
        )

        app.screen.query_one("#tb-inspector-changes").press()
        await pilot.pause()
        inspector = app.screen.query_one("#third-brain-inspector-text")
        rendered = str(inspector.render())
        assert "added_result_ids" in rendered
        assert "doc:spy.boundary" in rendered


@pytest.mark.asyncio
async def test_third_brain_tab_can_compare_coenabled_provider_results() -> None:
    app = OpenMinionApp(
        providers=ProviderBundle(provider=_CoenabledThirdBrainProvider())
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-third-brain")
        await pilot.pause()

        query = app.screen.query_one("#third-brain-query")
        query.value = "service"
        await pilot.click("#third-brain-run")
        await pilot.pause()
        await pilot.pause()

        rows = app.screen.query("#third-brain-left .third-brain-row")
        assert len(rows) == 2

        first_row = rows.first()
        assert first_row.id is not None
        await pilot.click(f"#{first_row.id}")
        await pilot.pause()
        app.screen.query_one("#tb-inspector-compare").press()
        await pilot.pause()

        inspector = app.screen.query_one("#third-brain-inspector-text")
        rendered = str(inspector.render())
        assert "side_by_side_provider_comparison" in rendered
        assert '"provider": "graphify"' in rendered
        assert '"provider": "pragmagraph"' in rendered
        assert '"missing_providers": []' in rendered


@pytest.mark.asyncio
async def test_third_brain_tab_can_render_local_map_from_neighbors() -> None:
    spy = _SpyThirdBrainProvider()
    app = OpenMinionApp(providers=ProviderBundle(provider=spy))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-third-brain")
        await pilot.pause()

        query = app.screen.query_one("#third-brain-query")
        query.value = "service"
        await pilot.click("#third-brain-run")
        await pilot.pause()
        await pilot.pause()

        result_rows = app.screen.query("#third-brain-left .third-brain-row")
        result_row = result_rows.first()
        assert result_row.id is not None
        await pilot.click(f"#{result_row.id}")
        await pilot.pause()
        await pilot.click("#tb-mode-neighborhood")
        await pilot.pause()
        await pilot.click("#third-brain-run")
        await pilot.pause()
        await pilot.pause()

        app.screen.query_one("#tb-inspector-map").press()
        await pilot.pause()

        inspector = app.screen.query_one("#third-brain-inspector-text")
        rendered = str(inspector.render())
        assert "local_graph_map" in rendered
        assert "Neighborhood map with 1 visible neighbor edges." in rendered
        assert "symbol:spy.neighbor" in rendered
