from __future__ import annotations

import inspect
import typing
from pathlib import Path

import openminion.modules.brain.loop.strategies.research.handler as handler_mod
from openminion.modules.brain.schemas.autonomy.strategy import (
    ResearchConvergenceSignal,
)


def _handler_source() -> str:
    path = Path(inspect.getfile(handler_mod))
    return path.read_text(encoding="utf-8")


def test_handler_source_does_not_import_structured_mode_response() -> None:
    src = _handler_source()
    assert "from openminion.modules.brain.loop.tools.structured_llm" not in src, (
        "ASRR-04: research handler must not import the structured-LLM "
        "surface (``structured_mode_response``) — that was the LLM-judge "
        "convergence path. Re-introducing it silently reintroduces the "
        "LLM-judge convergence anti-pattern."
    )
    # Defensive: the function name itself must not appear (could be
    # imported under an alias).
    assert "structured_mode_response(" not in src, (
        "ASRR-04: ``structured_mode_response(`` call detected in research "
        "handler. The LLM-judge convergence path is retired and may not "
        "be silently re-enabled."
    )


def test_handler_source_does_not_pass_convergence_check_to_structured_call() -> None:
    src = _handler_source()
    assert "schema=ConvergenceCheck" not in src, (
        "ASRR-04: passing ``ConvergenceCheck`` as a structured-LLM schema "
        "is the exact LLM-judge convergence shape ASRR retires."
    )


def test_handler_source_does_not_define_build_convergence_prompt() -> None:
    src = _handler_source()
    assert "_build_convergence_prompt" not in src, (
        "ASRR-04: the LLM-judge convergence-prompt builder is retired. "
        "Reintroducing the helper signals a silent revert of the "
        "structural convergence regime."
    )


def test_check_convergence_returns_typed_research_convergence_signal() -> None:

    handler_cls = handler_mod.ResearchMode
    method = handler_cls._check_convergence
    hints = typing.get_type_hints(method)
    assert hints.get("return") is ResearchConvergenceSignal, (
        "ASRR-04: ``ResearchMode._check_convergence`` must return "
        "the typed ``ResearchConvergenceSignal`` (structural surface). "
        f"Got: {hints.get('return')!r}"
    )


def test_handler_module_does_not_reference_convergence_check_type() -> None:

    src = _handler_source()
    # Allow doc-string references to the retired surface (for clarity
    # in code comments / docstrings); forbid live code references.
    code_lines = []
    in_docstring = False
    docstring_quote = None
    for line in src.splitlines():
        stripped = line.strip()
        if not in_docstring:
            if stripped.startswith('"""') or stripped.startswith("'''"):
                docstring_quote = stripped[:3]
                # Single-line docstring?
                if stripped.count(docstring_quote) >= 2 and len(stripped) > 3:
                    continue
                in_docstring = True
                continue
            # Strip inline comments
            code_part = line.split("#", 1)[0]
            code_lines.append(code_part)
        else:
            if docstring_quote and docstring_quote in line:
                in_docstring = False
                docstring_quote = None
            continue

    code_blob = "\n".join(code_lines)
    assert "ConvergenceCheck" not in code_blob, (
        "ASRR-04: ``ConvergenceCheck`` may not appear in live code in "
        "the research handler. Doc-comment references are permitted; "
        "code references signal the LLM-judge path is being silently "
        "reintroduced."
    )
