from __future__ import annotations

import asyncio
import re

import pytest

from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION, ProviderBundle
from openminion.cli.tui.app import (
    DemoCronProvider,
    DemoPolicyProvider,
    DemoSessionsProvider,
    DemoSystemProvider,
    DemoTasksProvider,
    OpenMinionApp,
    _MockApprovalStore,
)
from textual.widgets import Input


class _SpyMemoryProvider:
    contract_version = CLI_INTERFACE_VERSION

    def __init__(self) -> None:
        self.list_calls = 0
        self.search_calls = 0
        self.search_queries: list[str] = []
        self._records = [
            {
                "id": "mem-001",
                "type": "episodic",
                "scope": "session",
                "content_preview": "User prefers concise answers",
                "ts": "2026-03-14",
            },
            {
                "id": "mem-002",
                "type": "semantic",
                "scope": "global",
                "content_preview": "Project uses Textual 8.x TUI",
                "ts": "2026-03-15",
            },
            {
                "id": "mem-003",
                "type": "working",
                "scope": "session",
                "content_preview": "Current task: refactor auth",
                "ts": "2026-03-15",
            },
        ]

    def list_records(self, limit: int = 50) -> list[dict]:
        self.list_calls += 1
        return list(self._records)

    def list_candidates(self) -> list[dict]:
        return []

    def search(self, query: str) -> list[dict]:
        self.search_calls += 1
        self.search_queries.append(query)
        q = query.lower()
        return [
            record for record in self._records if q in record["content_preview"].lower()
        ]


@pytest.mark.asyncio
async def test_memory_search_debounce_cancel_replace_and_clear_restore() -> None:
    approval_store = _MockApprovalStore()
    memory_provider = _SpyMemoryProvider()
    app = OpenMinionApp(
        providers=ProviderBundle(
            tasks=DemoTasksProvider(approval_store),
            cron=DemoCronProvider(),
            sessions=DemoSessionsProvider(),
            system=DemoSystemProvider(),
            policy=DemoPolicyProvider(approval_store),
            memory=memory_provider,
        )
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-memory")
        await pilot.pause()

        initial_rows = app.screen.query("#memory-list .memory-row")
        assert len(initial_rows) == 3
        assert memory_provider.list_calls == 1
        assert memory_provider.search_calls == 0

        await pilot.press("/")
        await pilot.pause()
        assert app.screen.query_one("#memory-search", Input).has_focus

        search = app.screen.query_one("#memory-search", Input)
        search.value = "project"
        search.value = "current"
        await pilot.pause()
        await asyncio.sleep(0.25)
        await pilot.pause()

        filtered_rows = app.screen.query("#memory-list .memory-row")
        assert len(filtered_rows) == 1
        assert memory_provider.search_calls >= 1
        assert memory_provider.search_queries[-1] == "current"

        search = app.screen.query_one("#memory-search", Input)
        search.value = ""
        await pilot.pause()

        restored_rows = app.screen.query("#memory-list .memory-row")
        assert len(restored_rows) == 3
        assert memory_provider.list_calls == 2
        assert memory_provider.search_calls >= 1


@pytest.mark.asyncio
async def test_memory_candidate_similarity_bar_is_fixed_width() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-memory")
        await pilot.pause()

        rows = app.screen.query("#memory-candidates .candidate-row")
        assert len(rows) >= 1
        for row in rows:
            rendered = str(row.render())
            match = re.search(r"\[([█░]+)\]", rendered)
            assert match is not None
            assert len(match.group(1)) == 10


@pytest.mark.asyncio
async def test_memory_click_expands_detail_panel() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-memory")
        await pilot.pause()

        await pilot.click("#mem-mem-001")
        await pilot.pause()

        detail = app.screen.query_one("#memory-candidates .memory-detail-body")
        assert "concise answers" in str(detail.render())
