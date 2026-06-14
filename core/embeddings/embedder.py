from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Sequence
from typing import Literal, Protocol, runtime_checkable

EmbedderName = Literal["deterministic", "openai", "voyage"]


@runtime_checkable
class Embedder(Protocol):
    """Vector encoder contract.

    Returning sequences of `tuple[float, ...]` (rather than `list`) means
    callers can use embeddings as dict keys or set members for de-dup
    and caching without explicit conversion.
    """

    name: str

    @property
    def dimension(self) -> int: ...

    async def embed_batch(self, texts: Sequence[str]) -> list[tuple[float, ...]]: ...

    async def aclose(self) -> None:
        """Release any held resources (HTTP client, ONNX session). No-op for
        resource-free embedders; always safe to call exactly once."""
        ...


class DeterministicEmbedder:
    """Hash-based deterministic embedder (no external API, no PRNG).

    Used in tests, in CI, and as a stand-in until a model-backed
    embedder is wired in Phase 4. Output is L2-normalized so cosine
    distances behave like a real embedding space, but the vectors carry
    no semantic information — they are purely a stable identity hash.

    SHA-512 produces 64 bytes; for any required `dimension` we hash
    `text + ":" + i` for i in 0..ceil(dim/16) so the output is
    arbitrary-length while remaining deterministic.
    """

    name: str = "deterministic"

    def __init__(self, dimension: int = 1536) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be > 0")
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed_batch(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        return [self._embed_one(t) for t in texts]

    async def aclose(self) -> None:
        """No-op — the deterministic embedder holds no resources."""
        return None

    def _embed_one(self, text: str) -> tuple[float, ...]:
        floats: list[float] = []
        chunk_idx = 0
        # Each SHA-512 yields 64 bytes -> 16 IEEE-754 floats. Loop until
        # we have at least `dimension` floats.
        while len(floats) < self._dimension:
            digest = hashlib.sha512(
                f"{text}:{chunk_idx}".encode()
            ).digest()
            for i in range(0, len(digest), 4):
                word = digest[i : i + 4]
                if len(word) < 4:
                    break
                # Interpret as signed 32-bit int -> float in [-1, 1).
                (val,) = struct.unpack(">i", word)
                floats.append(val / (2**31))
            chunk_idx += 1
        floats = floats[: self._dimension]

        # L2-normalize so cosine similarity ≈ inner product.
        norm = math.sqrt(sum(f * f for f in floats))
        if norm == 0.0:
            return tuple([0.0] * self._dimension)
        return tuple(f / norm for f in floats)
