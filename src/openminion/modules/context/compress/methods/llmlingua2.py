"""LLMLingua-2 compression adapter."""

from dataclasses import dataclass
from typing import Any, Iterable, Protocol, Sequence

from ..errors import MethodError
from ..schemas import CompressedBlock, CompressionPolicy, InputBlock
from .extractive import ExtractiveCompressor

METHOD_ID = "llmlingua2.v1"
_VERSION = "v1.0"


class _LLMLingua2Backend(Protocol):
    """Minimal duck-typed surface used by ``LLMLingua2Compressor``.

    The real ``llmlingua.PromptCompressor.compress_prompt`` returns a
    dict containing at least the keys named below. The Protocol lets us
    inject a fake backend in tests without monkeypatching the real one.
    """

    def compress_prompt(
        self,
        context: list[str],
        instruction: str,
        question: str,
        target_token: int,
    ) -> dict[str, Any]:  # pragma: no cover - protocol declaration only
        ...


def _try_load_real_backend() -> _LLMLingua2Backend | None:
    """Lazy-import the optional `llmlingua` backend.

    Constructs the LLMLingua-2 variant explicitly (`use_llmlingua2=True`
    with the `microsoft/llmlingua-2-xlm-roberta-large-meetingbank`
    model) on CPU.  The bare `PromptCompressor()` constructor defaults
    to LLMLingua-1 on CUDA, which fails on CPU-only hosts.
    """

    try:
        from llmlingua import PromptCompressor  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        return PromptCompressor(
            model_name="microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
            use_llmlingua2=True,
            device_map="cpu",
        )
    except Exception:
        return None


@dataclass(frozen=True)
class LLMLingua2Result:
    blocks: Sequence[CompressedBlock]
    dropped_reason_stats: dict[str, int]
    warnings: Sequence[str]
    method_meta: dict[str, str]
    fallback_used: bool

    @property
    def method_id(self) -> str:
        return METHOD_ID


class LLMLingua2Compressor:
    """Feature-flagged adapter for LLMLingua-2 style compression."""

    def __init__(
        self,
        *,
        available: bool = False,
        adapter_version: str = "v1.0",
        backend: _LLMLingua2Backend | None = None,
    ) -> None:
        self._available_flag = available
        self._adapter_version = adapter_version
        self._resolved_backend: _LLMLingua2Backend | None = backend
        self._extractor = ExtractiveCompressor()

    @property
    def method_id(self) -> str:
        return METHOD_ID

    @property
    def version(self) -> str:
        return _VERSION

    def is_available(self) -> bool:
        """True when the operator-enable flag is set AND a backend resolves.

        Resolution order:
          1. If a backend was injected at construction time, use it.
          2. Otherwise, lazy-load the real backend. Cache the result so the
             import + model-load cost is paid at most once.
        """

        if not self._available_flag:
            return False
        if self._resolved_backend is not None:
            return True
        self._resolved_backend = _try_load_real_backend()
        return self._resolved_backend is not None

    def compress(
        self,
        blocks: Iterable[InputBlock],
        policy: CompressionPolicy,
        query: str,
    ) -> LLMLingua2Result:
        if not self.is_available():
            raise MethodError(
                f"{METHOD_ID} adapter is not available; "
                "caller must fall back to extractive.v1"
            )

        assert self._resolved_backend is not None  # narrowed by is_available()

        input_blocks = list(blocks)
        if not input_blocks:
            return LLMLingua2Result(
                blocks=(),
                dropped_reason_stats={},
                warnings=(),
                method_meta=self._method_meta(),
                fallback_used=False,
            )

        target_token = self._target_token_budget(input_blocks, policy)
        compressed_blocks, warnings = self._run_backend(
            input_blocks,
            query=query,
            target_token=target_token,
        )

        return LLMLingua2Result(
            blocks=tuple(compressed_blocks),
            dropped_reason_stats={},
            warnings=tuple(warnings),
            method_meta=self._method_meta(),
            fallback_used=False,
        )

    # ------------------------------------------------------------------ internals

    def _method_meta(self) -> dict[str, str]:
        return {
            "method_id": METHOD_ID,
            "version": _VERSION,
            "adapter_version": self._adapter_version,
        }

    def _target_token_budget(
        self,
        input_blocks: list[InputBlock],
        policy: CompressionPolicy,
    ) -> int:
        """Resolve the compression target budget from ``policy.target_ratio``.

        Uses a rough char/4 token estimate scaled by ``policy.target_ratio``
        (default 0.25 = compress to ~25% of input). Avoids importing a real
        tokenizer here; downstream consumers re-tokenize with the canonical
        tokenizer anyway.
        """

        total_chars = sum(len(block.text or "") for block in input_blocks)
        estimated_tokens = max(1, total_chars // 4)
        ratio = getattr(policy, "target_ratio", 0.25)
        try:
            ratio = float(ratio)
        except (TypeError, ValueError):
            ratio = 0.25
        ratio = max(0.05, min(1.0, ratio))
        return max(1, int(estimated_tokens * ratio))

    def _run_backend(
        self,
        input_blocks: list[InputBlock],
        *,
        query: str,
        target_token: int,
    ) -> tuple[list[CompressedBlock], list[str]]:
        """Run backend helper."""

        assert self._resolved_backend is not None
        warnings: list[str] = []
        compressed: list[CompressedBlock] = []
        per_block_budget = max(1, target_token // max(1, len(input_blocks)))

        for block in input_blocks:
            text = block.text or ""
            if not text.strip():
                # Empty input → empty output; do not call the backend.
                compressed.append(self._empty_compressed_block(block))
                continue
            try:
                result = self._resolved_backend.compress_prompt(
                    context=[text],
                    instruction="",
                    question=query,
                    target_token=per_block_budget,
                )
            except Exception as exc:
                warnings.append(
                    f"llmlingua2:block_compress_failed:{block.block_id}:{type(exc).__name__}"
                )
                # On per-block failure, preserve the block uncompressed
                # rather than dropping it — losing data is worse than not
                # compressing it. The warning is captured for telemetry.
                compressed.append(self._passthrough_compressed_block(block))
                continue

            compressed_text = str(result.get("compressed_prompt", "")).strip()
            if not compressed_text:
                warnings.append(f"llmlingua2:empty_compressed_output:{block.block_id}")
                compressed.append(self._passthrough_compressed_block(block))
                continue

            compressed.append(
                CompressedBlock(
                    block_id=block.block_id,
                    type=_coerce_compressed_block_type(block.type),
                    text=compressed_text,
                    refs=list(block.refs),
                    unit_refs=[f"{block.block_id}:llmlingua2"],
                    compression_meta={
                        "method_id": METHOD_ID,
                        "compression_ratio": (len(compressed_text) / max(1, len(text))),
                    },
                )
            )

        return compressed, warnings

    @staticmethod
    def _empty_compressed_block(block: InputBlock) -> CompressedBlock:
        return CompressedBlock(
            block_id=block.block_id,
            type=_coerce_compressed_block_type(block.type),
            text="",
            refs=list(block.refs),
            unit_refs=[],
            compression_meta={"method_id": METHOD_ID, "empty_input": True},
        )

    @staticmethod
    def _passthrough_compressed_block(block: InputBlock) -> CompressedBlock:
        return CompressedBlock(
            block_id=block.block_id,
            type=_coerce_compressed_block_type(block.type),
            text=block.text or "",
            refs=list(block.refs),
            unit_refs=[f"{block.block_id}:llmlingua2-passthrough"],
            compression_meta={"method_id": METHOD_ID, "passthrough": True},
        )


# ----------------------------------------------------------- module helpers


_INPUT_TO_COMPRESSED_TYPE_MAP: dict[str, str] = {
    "retrieval": "retrieval",
    "dialogue": "dialogue",
    "memory": "memory",
    "skill": "skill",
    "wm": "memory",
    "episode": "episode_condensate",
}


def _coerce_compressed_block_type(input_type: str) -> Any:
    """Map an ``InputBlock.type`` to the narrower ``CompressedBlockType`` set.

    The compress contract has a slightly different Literal for compressed
    blocks (no ``wm``, ``episode`` is renamed to ``episode_condensate``).
    Falls back to ``retrieval`` for unknown types so we never break the
    pipeline on a new BlockType.
    """

    return _INPUT_TO_COMPRESSED_TYPE_MAP.get(input_type, "retrieval")
