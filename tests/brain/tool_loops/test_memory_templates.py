from __future__ import annotations

from openminion.modules.brain.loop.tools.memory_templates import (
    LoopTemplate,
    build_template_hint,
    match_templates,
)


def test_template_round_trip() -> None:
    tmpl = LoopTemplate(
        match_tags=("intent.search", "intent.files", "intent.python"),
        tool_sequence=("file.read", "exec.run"),
        avg_iterations=3.0,
        success=True,
    )
    d = tmpl.to_dict()
    restored = LoopTemplate.from_dict(d)

    assert restored.match_tags == tmpl.match_tags
    assert restored.tool_sequence == tmpl.tool_sequence
    assert restored.avg_iterations == tmpl.avg_iterations
    assert restored.success is True


def test_template_matching_by_keywords() -> None:
    t1 = LoopTemplate(
        match_tags=("intent.search", "intent.files"),
        tool_sequence=("glob",),
        avg_iterations=1.0,
        success=True,
    )
    t2 = LoopTemplate(
        match_tags=("intent.search", "intent.files", "intent.python", "intent.code"),
        tool_sequence=("grep", "file.read"),
        avg_iterations=2.0,
        success=True,
    )
    t3 = LoopTemplate(
        match_tags=("intent.deploy", "intent.server"),
        tool_sequence=("exec.run",),
        avg_iterations=5.0,
        success=False,
    )

    results = match_templates(
        [t1, t2, t3],
        ("intent.search", "intent.python", "intent.files"),
        top_n=2,
    )

    assert len(results) == 2
    assert results[0] is t2
    assert results[1] is t1


def test_hint_generation_format() -> None:
    templates = [
        LoopTemplate(
            match_tags=("intent.search",),
            tool_sequence=("grep", "file.read"),
            avg_iterations=2.5,
            success=True,
        ),
    ]
    hint = build_template_hint(templates)

    assert "For similar tasks" in hint
    assert "grep" in hint
    assert "file.read" in hint
    assert "2.5" in hint


def test_use_memory_templates_disabled_by_default() -> None:
    from openminion.modules.brain.loop.tools.contracts import AdaptiveToolLoopProfile

    profile = AdaptiveToolLoopProfile(
        profile_name="test",
        mode_name="act",
        tool_exposure_policy="explicit_allowlist",
        allowed_tools=frozenset({"tool_a"}),
    )
    assert profile.use_memory_templates is False


def test_dissimilar_keywords_no_match() -> None:
    templates = [
        LoopTemplate(
            match_tags=("intent.deploy", "intent.server", "intent.docker"),
            tool_sequence=("exec.run",),
            avg_iterations=4.0,
            success=True,
        ),
    ]
    results = match_templates(
        templates,
        ("intent.read", "intent.python", "intent.search"),
    )

    assert results == []


def test_empty_templates_returns_empty() -> None:
    results = match_templates([], ("intent.search", "intent.files"))
    assert results == []


def test_empty_match_tags_returns_empty() -> None:
    templates = [
        LoopTemplate(
            match_tags=("intent.search",),
            tool_sequence=("grep",),
            avg_iterations=1.0,
            success=True,
        ),
    ]
    results = match_templates(templates, ())
    assert results == []


def test_match_templates_normalizes_duplicate_case_mixed_tags() -> None:
    template = LoopTemplate(
        match_tags=("Intent.Search", "intent.files"),
        tool_sequence=("grep",),
        avg_iterations=1.0,
        success=True,
    )
    results = match_templates(
        [template],
        ("intent.search", "INTENT.SEARCH", "intent.files"),
    )
    assert results == [template]


def test_build_template_hint_empty() -> None:
    assert build_template_hint([]) == ""


def test_match_templates_top_n() -> None:
    templates = [
        LoopTemplate(
            match_tags=("intent.search", "intent.python"),
            tool_sequence=("grep",),
            avg_iterations=1.0,
            success=True,
        ),
        LoopTemplate(
            match_tags=("intent.search", "intent.python", "intent.code"),
            tool_sequence=("file.read",),
            avg_iterations=2.0,
            success=True,
        ),
        LoopTemplate(
            match_tags=("intent.search",),
            tool_sequence=("glob",),
            avg_iterations=1.5,
            success=True,
        ),
    ]
    results = match_templates(
        templates,
        ("intent.search", "intent.python", "intent.code"),
        top_n=2,
    )
    assert len(results) == 2
