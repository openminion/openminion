from __future__ import annotations

import re
from pathlib import Path

import pytest


OPENMINION_ROOT = Path(__file__).resolve().parents[4]  # …/openminion/
FOCUS_SCREEN = (
    OPENMINION_ROOT / "src" / "openminion" / "cli" / "tui" / "focus" / "screen.py"
)


EXPECTED_CAT1_METHODS: set[tuple[str, str]] = set()

EXPECTED_CAT2_EVENT_CLASSES: set[str] = {
    "ChatSearchBar.SearchChanged",
    "ChatSearchBar.SearchClosed",
}

EXPECTED_CAT3_HANDLERS: set[str] = {
    "on_chat_search_bar_search_changed",
    "on_chat_search_bar_search_closed",
}

EXPECTED_CAT4_IMPORTS: set[str] = {
    "ChatSearchBar",
}


@pytest.fixture(scope="module")
def screen_source() -> str:
    if not FOCUS_SCREEN.is_file():
        pytest.skip(f"focus/screen.py not present at {FOCUS_SCREEN}")
    return FOCUS_SCREEN.read_text(encoding="utf-8")


def _grep_cat1_methods(source: str) -> set[tuple[str, str]]:
    pattern = re.compile(r"query_one\((Chat(?:View|InputBar))\)\.([a-z_]+)\(")
    return {(m.group(1), m.group(2)) for m in pattern.finditer(source)}


def _grep_cat2_event_classes(source: str) -> set[str]:
    pattern = re.compile(r"\b(Chat(?:InputBar|SearchBar|View))\.([A-Z]\w+)\b")
    matches = {f"{m.group(1)}.{m.group(2)}" for m in pattern.finditer(source)}
    return matches


def _grep_cat3_handlers(source: str) -> set[str]:
    pattern = re.compile(
        r"^\s*def (on_(?:chat_input_bar|chat_search_bar|chat_view|message_widget)_\w+)\(",
        re.MULTILINE,
    )
    return {m.group(1) for m in pattern.finditer(source)}


def _grep_cat4_imports(source: str) -> set[str]:
    interest = {"ChatInputBar", "ChatSearchBar", "ChatView", "MessageWidget"}
    found: set[str] = set()
    pattern = re.compile(
        r"from\s+(?:openminion\.cli\.tui\.widgets(?:\.\w+)?|\.+widgets(?:\.\w+)?)\s+import\s+(\([^)]*\)|[^\n]+)",
        re.MULTILINE,
    )
    for m in pattern.finditer(source):
        body = m.group(1)
        body = body.strip("()")
        for raw in re.split(r"[,\s]+", body):
            name = raw.strip()
            if name in interest:
                found.add(name)
    return found


def test_cat1_method_calls_match_matrix(screen_source: str) -> None:
    found = _grep_cat1_methods(screen_source)
    assert found == EXPECTED_CAT1_METHODS, (
        f"Cat-1 matrix drift — expected={EXPECTED_CAT1_METHODS}, "
        f"found={found}. Post-FNS-04 zero forbidden-widget query_one "
        f"calls should remain in focus/screen.py."
    )


def test_cat2_event_classes_match_matrix(screen_source: str) -> None:
    found = _grep_cat2_event_classes(screen_source)
    relevant = found & EXPECTED_CAT2_EVENT_CLASSES
    assert relevant == EXPECTED_CAT2_EVENT_CLASSES, (
        f"Cat-2 matrix drift — expected={EXPECTED_CAT2_EVENT_CLASSES}, "
        f"found={relevant}. Either an event class was removed (update "
        f"matrix to drop the row) or detection is broken."
    )
    unanticipated = found - EXPECTED_CAT2_EVENT_CLASSES
    assert not unanticipated, (
        f"Cat-2 matrix drift — new shared-widget event class(es) "
        f"detected: {unanticipated}. Update the compatibility matrix."
    )


def test_cat3_handlers_match_matrix(screen_source: str) -> None:
    found = _grep_cat3_handlers(screen_source)
    assert found == EXPECTED_CAT3_HANDLERS, (
        f"Cat-3 matrix drift — expected={EXPECTED_CAT3_HANDLERS}, "
        f"found={found}. Either a handler was added (update matrix to "
        f"add the row) or removed (update matrix to drop)."
    )


def test_cat4_imports_match_matrix(screen_source: str) -> None:
    found = _grep_cat4_imports(screen_source)
    assert found == EXPECTED_CAT4_IMPORTS, (
        f"Cat-4 matrix drift — expected={EXPECTED_CAT4_IMPORTS}, "
        f"found={found}. If FNS-04 has landed this set should shrink "
        f"to just `ChatSearchBar`."
    )
