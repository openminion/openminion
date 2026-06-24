from __future__ import annotations

import json
import time
from typing import Any, Dict, List

import pytest

from openminion.base.generated_paths import resolve_generated_root
from openminion.modules.context.compress.methods.extractive import ExtractiveCompressor
from openminion.modules.context.compress.methods.llmlingua2 import LLMLingua2Compressor
from openminion.modules.context.compress.schemas import CompressionPolicy, InputBlock


_POLICY = CompressionPolicy(method_prepass=None, target_ratio=0.4)
_QUERY = "summarize the key facts"


def _fixture_short() -> List[InputBlock]:
    return [
        InputBlock(
            block_id="short-a",
            type="retrieval",
            text=(
                "Jupiter is the largest planet in our solar system and is "
                "classified as a gas giant. It has at least 95 moons."
            ),
            refs=["short-a"],
            meta={"source_id": "short-a"},
        ),
        InputBlock(
            block_id="short-b",
            type="retrieval",
            text=(
                "Saturn is the sixth planet from the Sun and is known for "
                "its prominent ring system composed of ice and rocky debris."
            ),
            refs=["short-b"],
            meta={"source_id": "short-b"},
        ),
    ]


def _fixture_medium() -> List[InputBlock]:
    paragraph = (
        "The Apollo program was a series of crewed spaceflights run by NASA "
        "from 1961 to 1972. Apollo 11 was the first to successfully land "
        "humans on the Moon in 1969. Six subsequent missions returned crewed "
        "landings between 1969 and 1972, with each mission deploying scientific "
        "instruments and returning samples of lunar material to Earth. The "
        "program produced a wealth of geological, atmospheric, and engineering "
        "data that informs space exploration to this day."
    )
    return [
        InputBlock(
            block_id=f"medium-{i}",
            type="retrieval",
            text=paragraph,
            refs=[f"medium-{i}"],
            meta={"source_id": f"medium-{i}"},
        )
        for i in range(3)
    ]


def _fixture_long() -> List[InputBlock]:
    paragraph = (
        "Climate models converge on the conclusion that anthropogenic "
        "greenhouse-gas emissions, primarily carbon dioxide and methane, "
        "are the dominant driver of post-industrial warming. "
        "Reconstructed temperature records spanning millennia show that the "
        "rate of recent change is several times faster than any prior epoch "
        "without an obvious natural forcing. Mitigation strategies span "
        "energy-system decarbonization, land-use change, industrial process "
        "redesign, and direct atmospheric carbon removal. Adaptation "
        "strategies — coastal defenses, drought-tolerant agriculture, "
        "heat-tolerant urban infrastructure — are increasingly important "
        "regardless of mitigation success because some warming is now locked in. "
    ) * 4
    return [
        InputBlock(
            block_id=f"long-{i}",
            type="retrieval",
            text=paragraph,
            refs=[f"long-{i}"],
            meta={"source_id": f"long-{i}"},
        )
        for i in range(2)
    ]


_FIXTURES = {
    "short": _fixture_short,
    "medium": _fixture_medium,
    "long": _fixture_long,
}


def _measure_extractive(blocks: List[InputBlock]) -> Dict[str, Any]:
    compressor = ExtractiveCompressor()
    started = time.perf_counter()
    result = compressor.compress(blocks, _POLICY, _QUERY)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    input_chars = sum(len(block.text or "") for block in blocks)
    output_chars = sum(len(cb.text or "") for cb in result.blocks)
    return {
        "method_id": "extractive.v1",
        "input_chars": input_chars,
        "output_chars": output_chars,
        "compression_ratio": (output_chars / input_chars if input_chars else 0.0),
        "latency_ms": round(elapsed_ms, 3),
        "block_count_in": len(blocks),
        "block_count_out": len(list(result.blocks)),
    }


def _measure_llmlingua2(blocks: List[InputBlock]) -> Dict[str, Any]:
    compressor = LLMLingua2Compressor(available=True)
    if not compressor.is_available():
        return {
            "method_id": "llmlingua2.v1",
            "skipped": "llmlingua backend unavailable (extra not installed or model load failed)",
        }
    started = time.perf_counter()
    result = compressor.compress(blocks, _POLICY, _QUERY)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    input_chars = sum(len(block.text or "") for block in blocks)
    output_chars = sum(len(cb.text or "") for cb in result.blocks)
    return {
        "method_id": "llmlingua2.v1",
        "input_chars": input_chars,
        "output_chars": output_chars,
        "compression_ratio": (output_chars / input_chars if input_chars else 0.0),
        "latency_ms": round(elapsed_ms, 3),
        "block_count_in": len(blocks),
        "block_count_out": len(list(result.blocks)),
    }


def test_ab_eval_harness_writes_artifact(tmp_path):

    pytest.importorskip("llmlingua")  # eval is meaningless without the real backend
    eval_root = resolve_generated_root() / "compression-eval"
    eval_root.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    for name, factory in _FIXTURES.items():
        blocks = factory()
        rows.append(
            {
                "fixture": name,
                "extractive": _measure_extractive(blocks),
                "llmlingua2": _measure_llmlingua2(blocks),
            }
        )

    artifact_path = eval_root / "llmlingua2_vs_extractive.json"
    artifact_path.write_text(
        json.dumps(
            {
                "policy_target_ratio": _POLICY.target_ratio,
                "query": _QUERY,
                "rows": rows,
                "conclusion": _summarize(rows),
            },
            indent=2,
        )
    )

    # Shape assertions only — the A/B numbers themselves are evidence,
    # not pass/fail. The harness has done its job once the artifact exists
    # with the expected shape.
    assert artifact_path.exists()
    assert len(rows) == 3
    for row in rows:
        assert "extractive" in row
        assert "llmlingua2" in row
        assert row["extractive"]["method_id"] == "extractive.v1"


def _summarize(rows: List[Dict[str, Any]]) -> str:
    extractive_ratios = [
        row["extractive"]["compression_ratio"]
        for row in rows
        if "compression_ratio" in row["extractive"]
    ]
    llm_ratios = [
        row["llmlingua2"]["compression_ratio"]
        for row in rows
        if "compression_ratio" in row["llmlingua2"]
    ]
    if not llm_ratios:
        return "llmlingua2 unavailable; extractive baseline recorded only"
    avg_ext = sum(extractive_ratios) / len(extractive_ratios)
    avg_llm = sum(llm_ratios) / len(llm_ratios)
    if avg_llm < avg_ext:
        return (
            f"llmlingua2 produced tighter compression on average "
            f"({avg_llm:.3f} vs extractive {avg_ext:.3f})"
        )
    return (
        f"extractive produced tighter compression on average "
        f"({avg_ext:.3f} vs llmlingua2 {avg_llm:.3f})"
    )
