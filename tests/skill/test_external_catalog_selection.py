from __future__ import annotations

from pathlib import Path

from openminion.modules.skill.models import SkillPackage
from openminion.modules.skill.runtime.skill import Skill

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures" / "external_catalog"


def _cfg(
    tmp_path: Path,
    *,
    known_tools: list[str] | None = None,
    include_known_tools: bool = True,
) -> dict:
    skill_cfg: dict[str, object] = {
        "sqlite_path": str(tmp_path / "skill-external-catalog.db"),
        "wal": False,
        "default_status_filter": ["draft", "verified", "blessed"],
        "high_risk_status_filter": ["blessed", "verified", "draft"],
    }
    if include_known_tools:
        skill_cfg["known_tools"] = list(known_tools or [])
    return {"skill": skill_cfg}


def _fixture_path(*parts: str) -> Path:
    return FIXTURES_ROOT.joinpath(*parts)


def _ingest_catalog(ctl: Skill) -> dict[str, tuple[str, str]]:
    fixtures = [
        ("openai", "linear"),
        ("openai", "playwright"),
        ("openai", "frontend-skill"),
        ("anthropic", "claude-api"),
        ("anthropic", "webapp-testing"),
    ]
    out: dict[str, tuple[str, str]] = {}
    for provider, name in fixtures:
        skill_id, version_hash, warnings = ctl.ingest_file(
            _fixture_path(provider, name, "SKILL.md")
        )
        assert not any(item.startswith("lint.error:") for item in warnings)
        out[name] = (skill_id, version_hash)
    return out


def test_linear_bundle_ingest_enriches_descriptor_fields(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=["http_request"]))
    try:
        skill_id, version_hash, warnings = ctl.ingest_file(
            _fixture_path("openai", "linear", "SKILL.md")
        )

        assert not any(item.startswith("lint.error:") for item in warnings)
        package = ctl.get_skill(skill_id, version_hash)
        assert package.name == "linear"
        assert package.display_name == "Linear"
        assert package.short_description == "Manage Linear issues in Codex"
        assert package.default_prompt == "Help me triage and update a Linear issue."
        assert package.dependency_hints == {
            "tools": ["http_request"],
            "mcp_servers": ["linear"],
        }
        assert package.tools == ["http_request"]
        assert set(package.reference_hints) == {"config.toml", "e.g"}
        assert package.bundle_metadata["source"] == "openai"
        assert package.bundle_metadata["trust"] == "untrusted_local"
        assert package.sections["summary"] == (
            "Coordinate Linear issue triage, status updates, and ownership changes."
        )
        summary = package.to_catalog_summary()
        assert summary["name"] == "Linear"
        assert summary["one_liner"] == "Manage Linear issues in Codex"

        roundtrip = SkillPackage.from_dict(package.to_dict())
        assert roundtrip.display_name == "Linear"
        assert roundtrip.short_description == "Manage Linear issues in Codex"
        assert set(roundtrip.reference_hints) == {"config.toml", "e.g"}

        snippet, _ = ctl.render_snippet(skill_id, version_hash, "act", 220)
        assert snippet.startswith("Skill: Linear")
        assert "Review the current issue state and labels." in snippet
    finally:
        ctl.close()


def test_ingest_text_does_not_bundle_enrich(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=["http_request"]))
    try:
        markdown = _fixture_path("openai", "linear", "SKILL.md").read_text(
            encoding="utf-8"
        )
        skill_id, version_hash, warnings = ctl.ingest_text(
            name="linear",
            markdown=markdown,
        )

        assert not any(item.startswith("lint.error:") for item in warnings)
        package = ctl.get_skill(skill_id, version_hash)
        assert package.display_name is None
        assert package.short_description is None
        assert package.default_prompt is None
        # ingest_text uses bundle_root=None → source="not_attempted".
        # The caller never gave the runtime an opportunity to look for a
        # companion file.
        assert package.bundle_metadata == {
            "source": "not_attempted",
            "trust": "untrusted_local",
        }
        assert package.sections["summary"] == (
            "Coordinate Linear issue triage, status updates, and ownership changes."
        )
    finally:
        ctl.close()


def test_ingest_artifact_does_not_bundle_enrich(tmp_path: Path) -> None:
    markdown = _fixture_path("openai", "linear", "SKILL.md").read_text(encoding="utf-8")
    ctl = Skill(
        _cfg(tmp_path, known_tools=["http_request"]),
        artifact_loader=lambda _ref: markdown,
    )
    try:
        skill_id, version_hash, warnings = ctl.ingest_artifact(
            "artifact://linear",
            name="linear",
        )

        assert not any(item.startswith("lint.error:") for item in warnings)
        package = ctl.get_skill(skill_id, version_hash)
        assert package.display_name is None
        assert package.short_description is None
        # ingest_artifact uses bundle_root=None → source="not_attempted".
        assert package.bundle_metadata == {
            "source": "not_attempted",
            "trust": "untrusted_local",
        }
    finally:
        ctl.close()


def test_short_description_precedence_uses_markdown_metadata(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=["file"]))
    try:
        skill_id, version_hash, warnings = ctl.ingest_file(
            _fixture_path("openai", "frontend-skill", "SKILL.md")
        )

        assert warnings == []
        package = ctl.get_skill(skill_id, version_hash)
        assert package.display_name == "Frontend Skill"
        assert package.short_description == "Implement frontend polish for UI tasks"
        assert package.default_prompt == "Help me refine this frontend experience."
    finally:
        ctl.close()


def test_exact_name_top1(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=["http_request", "browser", "file"]))
    try:
        _ingest_catalog(ctl)
        matches = ctl.match("claude-api", None, "agent.catalog", k=3)
        assert matches[0].skill_id == "claude-api"
    finally:
        ctl.close()


def test_exact_display_name_top1(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=["http_request", "browser", "file"]))
    try:
        _ingest_catalog(ctl)
        matches = ctl.match("Linear", None, "agent.catalog", k=3)
        assert matches[0].skill_id == "linear"
    finally:
        ctl.close()


def test_short_description_queries_return_target_in_top3(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=["http_request", "browser", "file"]))
    try:
        _ingest_catalog(ctl)
        queries = {
            "linear": "Manage Linear issues in Codex",
            "playwright": "Run Playwright browser validation in Codex",
            "frontend-skill": "Implement frontend polish for UI tasks",
        }
        for expected_skill_id, query in queries.items():
            matches = ctl.match(query, None, "agent.catalog", k=3)
            ids = [item.skill_id for item in matches[:3]]
            assert expected_skill_id in ids
    finally:
        ctl.close()


def test_template_lint_omits_registry_unavailable_noise(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, include_known_tools=False))
    try:
        skill_id, version_hash, warnings = ctl.ingest_file(
            _fixture_path("anthropic", "template-negative", "SKILL.md")
        )
        assert any("skill.procedure_missing" in item for item in warnings)
        assert not any("tool.registry_unavailable" in item for item in warnings)

        lint_report = ctl.lint(skill_id, version_hash)
        warning_codes = [item["code"] for item in lint_report["warnings"]]
        assert "skill.procedure_missing" in warning_codes
        assert "tool.registry_unavailable" not in warning_codes
        assert "tool.unknown" not in warning_codes
    finally:
        ctl.close()


def test_explicit_empty_registry_still_warns_unknown_tools(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=[]))
    try:
        skill_id, version_hash, warnings = ctl.ingest_file(
            _fixture_path("anthropic", "template-negative", "SKILL.md")
        )
        assert any("lint.warning:tool.unknown" in item for item in warnings)

        lint_report = ctl.lint(skill_id, version_hash)
        warning_codes = [item["code"] for item in lint_report["warnings"]]
        assert "tool.unknown" in warning_codes
    finally:
        ctl.close()


# Dense-family fixture helpers and baselines

FIGMA_FAMILY = [
    ("openai", "figma"),
    ("openai", "figma_generate_design"),
    ("openai", "figma_create_design_system_rules"),
    ("openai", "figma_code_connect_components"),
    ("openai", "figma_create_new_file"),
]

ANTHROPIC_DESCRIPTOR_SCARCE_FAMILY = [
    ("anthropic", "mcp_builder"),
    ("anthropic", "skill_creator"),
    ("anthropic", "slack_gif_creator"),
    ("anthropic", "theme_factory"),
    ("anthropic", "web_artifacts_builder"),
]

SUSPICIOUS_TOOL_SAMPLES = [
    ("anthropic", "claude-api"),
    ("openai", "figma_create_design_system_rules"),
    ("openai", "linear"),
]


def _ingest_family(
    ctl: Skill, family: list[tuple[str, str]]
) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for provider, name in family:
        skill_id, version_hash, warnings = ctl.ingest_file(
            _fixture_path(provider, name, "SKILL.md")
        )
        assert not any(item.startswith("lint.error:") for item in warnings)
        out[name] = (skill_id, version_hash)
    return out


# --- Figma dense-family tests ---


def test_figma_family_ingest_all(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=["browser"]))
    try:
        catalog = _ingest_family(ctl, FIGMA_FAMILY)
        assert len(catalog) == 5
        for name, (skill_id, version_hash) in catalog.items():
            package = ctl.get_skill(skill_id, version_hash)
            assert package.display_name is not None
            assert package.short_description is not None
            assert package.bundle_metadata.get("source") == "openai"
            assert package.bundle_metadata.get("trust") == "untrusted_local"
    finally:
        ctl.close()


def test_figma_family_exact_name_top1(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=["browser"]))
    try:
        catalog = _ingest_family(ctl, FIGMA_FAMILY)
        for name, (skill_id, _vh) in catalog.items():
            matches = ctl.match(name, None, "agent.figma", k=5)
            assert matches[0].skill_id == skill_id, (
                f"exact name '{name}' did not return self top-1"
            )
    finally:
        ctl.close()


def test_figma_family_exact_display_name_top1(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=["browser"]))
    try:
        catalog = _ingest_family(ctl, FIGMA_FAMILY)
        for name, (skill_id, version_hash) in catalog.items():
            package = ctl.get_skill(skill_id, version_hash)
            display_name = package.display_name
            assert display_name
            matches = ctl.match(display_name, None, "agent.figma", k=5)
            assert matches[0].skill_id == skill_id, (
                f"exact display_name '{display_name}' did not return self top-1"
            )
    finally:
        ctl.close()


def test_figma_family_summary_query_baseline(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=["browser"]))
    try:
        catalog = _ingest_family(ctl, FIGMA_FAMILY)
        for name, (skill_id, version_hash) in catalog.items():
            package = ctl.get_skill(skill_id, version_hash)
            query = package.short_description or package.summary
            matches = ctl.match(query, None, "agent.figma", k=5)
            ids = [m.skill_id for m in matches[:3]]
            assert skill_id in ids, (
                f"{name}: summary query did not return self in top-3"
            )
    finally:
        ctl.close()


# --- Anthropic descriptor-scarce family tests ---


def test_anthropic_descriptor_scarce_ingest_all(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=["file", "http_request", "browser"]))
    try:
        catalog = _ingest_family(ctl, ANTHROPIC_DESCRIPTOR_SCARCE_FAMILY)
        assert len(catalog) == 5
        for name, (skill_id, version_hash) in catalog.items():
            package = ctl.get_skill(skill_id, version_hash)
            assert package.display_name is None
            assert package.short_description is None
            assert package.bundle_metadata == {
                "source": "none",
                "trust": "untrusted_local",
            }
    finally:
        ctl.close()


def test_anthropic_descriptor_scarce_exact_name_top1(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=["file", "http_request", "browser"]))
    try:
        catalog = _ingest_family(ctl, ANTHROPIC_DESCRIPTOR_SCARCE_FAMILY)
        for name, (skill_id, _vh) in catalog.items():
            matches = ctl.match(name, None, "agent.anthropic", k=5)
            assert matches[0].skill_id == skill_id, (
                f"exact name '{name}' did not return self top-1"
            )
    finally:
        ctl.close()


def test_anthropic_descriptor_scarce_summary_query_baseline(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=["file", "http_request", "browser"]))
    try:
        catalog = _ingest_family(ctl, ANTHROPIC_DESCRIPTOR_SCARCE_FAMILY)
        for name, (skill_id, version_hash) in catalog.items():
            package = ctl.get_skill(skill_id, version_hash)
            query = package.summary
            matches = ctl.match(query, None, "agent.anthropic", k=5)
            ids = [m.skill_id for m in matches[:3]]
            assert skill_id in ids, (
                f"{name}: summary query did not return self in top-3"
            )
    finally:
        ctl.close()


# --- Suspicious-tool extraction tests ---


def test_figma_family_render_snippet(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=["browser"]))
    try:
        catalog = _ingest_family(ctl, FIGMA_FAMILY)
        for name, (skill_id, version_hash) in catalog.items():
            package = ctl.get_skill(skill_id, version_hash)
            snippet, snippet_hash = ctl.render_snippet(
                skill_id, version_hash, "act", 300
            )
            assert snippet.startswith(f"Skill: {package.display_name}"), (
                f"{name}: snippet header mismatch"
            )
            assert "Procedure:" in snippet, f"{name}: procedure section missing"
            assert len(snippet) > 50, f"{name}: snippet suspiciously short"
    finally:
        ctl.close()


def test_anthropic_descriptor_scarce_render_snippet(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=["file", "http_request", "browser"]))
    try:
        catalog = _ingest_family(ctl, ANTHROPIC_DESCRIPTOR_SCARCE_FAMILY)
        for name, (skill_id, version_hash) in catalog.items():
            package = ctl.get_skill(skill_id, version_hash)
            snippet, snippet_hash = ctl.render_snippet(
                skill_id, version_hash, "act", 300
            )
            assert snippet.startswith(f"Skill: {package.name}"), (
                f"{name}: snippet header should use name (no display_name)"
            )
            assert "Procedure:" in snippet, f"{name}: procedure section missing"
            assert len(snippet) > 50, f"{name}: snippet suspiciously short"
    finally:
        ctl.close()


def test_suspicious_tools_claude_api_baseline(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=["http_request"]))
    try:
        skill_id, version_hash, _warnings = ctl.ingest_file(
            _fixture_path("anthropic", "claude-api", "SKILL.md")
        )
        package = ctl.get_skill(skill_id, version_hash)
        assert package.tools == ["http_request"]
        assert {
            "requirements.txt",
            "pyproject.toml",
            "setup.py",
            "package.json",
            "models.md",
            "Anthropic.Tool",
            "e.g",
            "README.md",
        }.issubset(set(package.reference_hints))
    finally:
        ctl.close()


def test_suspicious_tools_figma_design_system_rules_baseline(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=["browser"]))
    try:
        skill_id, version_hash, _warnings = ctl.ingest_file(
            _fixture_path("openai", "figma_create_design_system_rules", "SKILL.md")
        )
        package = ctl.get_skill(skill_id, version_hash)
        assert package.tools == ["browser"]
        assert {
            "CLAUDE.md",
            "AGENTS.md",
            "tailwind.config.js",
            "Button.tsx",
            "index.tsx",
        }.issubset(set(package.reference_hints))
    finally:
        ctl.close()


def test_suspicious_tools_linear_baseline(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path, known_tools=["http_request"]))
    try:
        skill_id, version_hash, _warnings = ctl.ingest_file(
            _fixture_path("openai", "linear", "SKILL.md")
        )
        package = ctl.get_skill(skill_id, version_hash)
        assert package.tools == ["http_request"]
        assert {"config.toml", "e.g"}.issubset(set(package.reference_hints))
    finally:
        ctl.close()
