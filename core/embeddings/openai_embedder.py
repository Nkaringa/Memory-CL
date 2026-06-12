from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx

from core.compression.logevent import emit_phase3_event
from core.observability import get_tracer

_tracer = get_tracer("core.embeddings.openai_embedder")

OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"

# Defensive per-input cap (~8000 tokens at ~4 chars/token). The OpenAI
# API hard-rejects inputs over 8192 tokens; truncating here degrades
# the tail of a pathologically large unit instead of failing its whole
# batch. Real units almost never get close — chunking happens upstream.
MAX_INPUT_CHARS = 32_000

_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 0.5


class EmbeddingProviderError(RuntimeError):
    """Raised when the embedding provider fails permanently.

    Either a non-retryable HTTP status (4xx other than 429), a
    malformed response body, or exhaustion of all retry attempts on
    429/5xx/transport errors. `status_code` is populated when the
    failure came from an HTTP response (None for transport errors).
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class OpenAIEmbedder:
    """Model-backed embedder over the OpenAI REST API (via httpx).

    Satisfies the `Embedder` Protocol. No `openai` SDK dependency —
    POSTs `https://api.openai.com/v1/embeddings` directly with the
    already-pinned `httpx`. Inputs are split into `batch_size` batches
    and results are re-assembled in input order (the API guarantees
    `data[i].index`, not list order). 429 and 5xx responses are retried
    up to 3 attempts with exponential backoff; other failures raise
    `EmbeddingProviderError` immediately.

    `transport` and `sleep` are injectable for tests (mirrors the
    `sdk.client.AsyncMemoryClient` idiom): pass `httpx.MockTransport`
    to fake the HTTP layer and an `AsyncMock` to observe backoff
    without real waiting.
    """

    name: str = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "text-embedding-3-small",
        dimension: int = 1536,
        batch_size: int = 100,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key must be non-empty")
        if dimension <= 0:
            raise ValueError("dimension must be > 0")
        if batch_size <= 0:
            raise ValueError("batch_size must be > 0")
        self._model = model
        self._dimension = dimension
        self._batch_size = batch_size
        self._sleep: Callable[[float], Awaitable[None]] = (
            sleep if sleep is not None else asyncio.sleep
        )
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )

    @property
    def dimension(self) -> int:
        return self._dimension

    async def aclose(self) -> None:
        await self._client.aclose()

    async def embed_batch(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        if not texts:
            return []
        start = time.perf_counter()
        truncated = [t[:MAX_INPUT_CHARS] for t in texts]
        with _tracer.start_as_current_span("openai_embedder.embed_batch") as span:
            span.set_attribute("count", len(truncated))
            span.set_attribute("model", self._model)
            vectors: list[tuple[float, ...]] = []
            try:
                for i in range(0, len(truncated), self._batch_size):
                    vectors.extend(
                        await self._post_with_retry(truncated[i : i + self._batch_size])
                    )
            except EmbeddingProviderError as exc:
                emit_phase3_event(
                    event="openai_embed_batch",
                    operation="embed",
                    status="failed",
                    duration_ms=(time.perf_counter() - start) * 1000,
                    level="error",
                    texts=len(truncated),
                    model=self._model,
                    embedder=self.name,
                    error=str(exc),
                )
                raise
            emit_phase3_event(
                event="openai_embed_batch",
                operation="embed",
                status="success",
                duration_ms=(time.perf_counter() - start) * 1000,
                level="info",
                texts=len(truncated),
                vectors=len(vectors),
                model=self._model,
                embedder=self.name,
            )
            return vectors

    async def _post_with_retry(self, batch: Sequence[str]) -> list[tuple[float, ...]]:
        """POST one batch, retrying 429/5xx/transport errors with backoff."""
        last_detail = ""
        last_status: int | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = await self._client.post(
                    OPENAI_EMBEDDINGS_URL,
                    json={"model": self._model, "input": list(batch)},
                )
            except httpx.HTTPError as exc:
                # Transport-level failure (DNS, timeout, reset) — retryable.
                last_detail, last_status = f"transport error: {exc}", None
            else:
                if resp.status_code == 200:
                    return self._parse_response(resp, expected=len(batch))
                last_status = resp.status_code
                last_detail = resp.text[:500]
                if resp.status_code != 429 and resp.status_code < 500:
                    # Non-retryable client error (401, 400, 404, ...).
                    raise EmbeddingProviderError(
                        f"OpenAI embeddings request rejected "
                        f"(HTTP {resp.status_code}): {last_detail}",
                        status_code=resp.status_code,
                    )
            if attempt < _MAX_ATTEMPTS - 1:
                await self._sleep(_BACKOFF_BASE_SECONDS * (2**attempt))
        raise EmbeddingProviderError(
            f"OpenAI embeddings failed after {_MAX_ATTEMPTS} attempts "
            f"(last status={last_status}): {last_detail}",
            status_code=last_status,
        )

    def _parse_response(
        self, resp: httpx.Response, *, expected: int
    ) -> list[tuple[float, ...]]:
        try:
            payload: Any = resp.json()
            data = payload["data"]
            if len(data) != expected:
                raise EmbeddingProviderError(
                    f"OpenAI embeddings returned {len(data)} vectors "
                    f"for {expected} inputs",
                    status_code=resp.status_code,
                )
            # The contract guarantees `index`, not list order — sort.
            ordered = sorted(data, key=lambda d: int(d["index"]))
            return [tuple(float(x) for x in d["embedding"]) for d in ordered]
        except EmbeddingProviderError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingProviderError(
                f"OpenAI embeddings response malformed: {exc!r}",
                status_code=resp.status_code,
            ) from exc
