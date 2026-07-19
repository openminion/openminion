from __future__ import annotations


from openminion.cli.interactive.screen import FocusScreen


# ── Pattern matching ─────────────────────────────────────────────────────────


def test_api_key_pattern_appends_credentials_hint() -> None:
    body = FocusScreen._append_error_hint("API key missing.")
    assert "openminion config init" in body
    assert body.count("\n") == 1


def test_unauthorized_pattern_appends_credentials_hint() -> None:
    body = FocusScreen._append_error_hint("HTTP 401 unauthorized")
    assert "openminion config init" in body


def test_401_pattern_appends_credentials_hint() -> None:
    body = FocusScreen._append_error_hint("Returned 401 from /api/foo")
    assert "openminion config init" in body


def test_network_pattern_appends_retry_hint() -> None:
    body = FocusScreen._append_error_hint("Connection refused")
    assert "Check your network" in body
    body = FocusScreen._append_error_hint("Timeout after 30s")
    assert "Check your network" in body


def test_permission_pattern_appends_permissions_hint() -> None:
    body = FocusScreen._append_error_hint("Permission denied")
    assert "file permissions" in body
    body = FocusScreen._append_error_hint("EACCES: cannot open file")
    assert "file permissions" in body


def test_not_found_pattern_appends_path_hint() -> None:
    body = FocusScreen._append_error_hint("File not found: /tmp/foo")
    assert "Confirm the path" in body
    body = FocusScreen._append_error_hint("ENOENT: no such file")
    assert "Confirm the path" in body


def test_pattern_matching_is_case_insensitive() -> None:
    body_upper = FocusScreen._append_error_hint("API KEY MISSING")
    body_mixed = FocusScreen._append_error_hint("Api Key Missing")
    body_lower = FocusScreen._append_error_hint("api key missing")
    assert "openminion config init" in body_upper
    assert "openminion config init" in body_mixed
    assert "openminion config init" in body_lower


# ── No match → no hint ───────────────────────────────────────────────────────


def test_unmatched_body_returned_unchanged() -> None:
    body = FocusScreen._append_error_hint("Some unexpected error")
    assert body == "Some unexpected error"


def test_empty_body_returned_unchanged() -> None:
    assert FocusScreen._append_error_hint("") == ""
    assert FocusScreen._append_error_hint("   ") == "   "


# ── Long stack trace skipped ─────────────────────────────────────────────────


def test_three_line_body_skipped_even_with_match() -> None:
    body = "api key missing\nstack frame 1\nstack frame 2"
    out = FocusScreen._append_error_hint(body)
    assert out == body, "3-line body must not be modified"


def test_two_line_body_still_gets_hint() -> None:
    body = "api key missing\ncheck your config"
    out = FocusScreen._append_error_hint(body)
    assert "openminion config init" in out


# ── Pattern table shape ──────────────────────────────────────────────────────


def test_error_hint_patterns_table_shape() -> None:
    table = FocusScreen._ERROR_HINT_PATTERNS
    assert len(table) == 4
    for patterns, hint in table:
        assert isinstance(patterns, tuple)
        assert all(isinstance(p, str) and p for p in patterns)
        assert isinstance(hint, str) and hint.startswith("→")
