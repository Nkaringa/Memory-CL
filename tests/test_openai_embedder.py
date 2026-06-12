from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from core.embeddings import Embedder, EmbeddingProviderError, OpenAIEmbedder
from core.embeddings.openai_embedder import MAX_INPUT_CHARS, OPENAI_EMBEDDINGS_URL


def _embedding_for(text: str, dimension: int) -> list[float]:
    """Deterministic per-text fake vector so order is verifiable."""
    return [float(len(text))] * dimension


def _ok_response(request: httpx.Request, *, dimension: int, shuffle: bool = False) -> httpx.Response:
    """Build a valid /v1/embeddings response for the request's inputs.

    With `shuffle=True` the `data` items come back in reverse `index`
    order — the API contract only guarantees `index`, not list order.
    """
    body = json.loads(request.content)
    items = [
        {"object": "embedding", "index": i, "embedding": _embedding_for(t, dimension)}
        for i, t in enumerate(body["input"])
    ]
    if shuffle:
        items = list(reversed(items))
    return httpx.Response(200, json={"object": "list", "data": items, "model": body["model"]})


def _make_embedder(
    handler: Any,
    *,
    dimension: int = 4,
    batch_size: int = 100,
    sleep: Any = None,
    api_key: str = "sk-test",
) -> OpenAIEmbedder:
    return OpenAIEmbedder(
        api_key=api_key,
        dimension=dimension,
        batch_size=batch_size,
        transport=httpx.MockTransport(handler),
        sleep=sleep if sleep is not None else AsyncMock(),
    )


# ---- construction / Protocol -----------------------------------------------
def test_openai_embedder_satisfies_protocol() -> None:
    e = _make_embedder(lambda req: _ok_response(req, dimension=1536), dimension=1536)
    assert isinstance(e, Embedder)
    assert e.name == "openai"
    assert e.dimension == 1536


def test_openai_embedder_rejects_invalid_knobs() -> None:
    with pytest.raises(ValueError):
        OpenAIEmbedder(api_key="")
    with pytest.raises(ValueError):
        OpenAIEmbedder(api_key="sk-x", dimension=0)
    with pytest.raises(ValueError):
        OpenAIEmbedder(api_key="sk-x", batch_size=0)


# ---- happy path -------------------------------------------------------------
async def test_single_batch_happy_path_sends_auth_and_preserves_order() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _ok_response(request, dimension=4, shuffle=True)

    e = _make_embedder(handler, dimension=4)
    vectors = await e.embed_batch(["a", "bb", "ccc"])

    assert len(requests) == 1
    assert requests[0].url == OPENAI_EMBEDDINGS_URL
    assert requests[0].headers["Authorization"] == "Bearer sk-test"
    sent = json.loads(requests[0].content)
    assert sent == {
        "model": "text-embedding-3-small",
        "input": ["a", "bb", "ccc"],
        "dimensions": 4,
    }

    # Order preserved even though the mock returned data reversed.
    assert vectors == [
        tuple(_embedding_for("a", 4)),
        tuple(_embedding_for("bb", 4)),
        tuple(_embedding_for("ccc", 4)),
    ]
    assert all(isinstance(v, tuple) for v in vectors)


async def test_empty_input_returns_empty_and_makes_no_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no HTTP call expected for empty input")

    e = _make_embedder(handler)
    assert await e.embed_batch([]) == []


# ---- batching ----------------------------------------------------------------
async def test_multi_batch_split_and_global_order() -> None:
    batch_inputs: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        batch_inputs.append(json.loads(request.content)["input"])
        return _ok_response(request, dimension=2)

    texts = ["t1", "t22", "t333", "t4444", "t55555"]
    e = _make_embedder(handler, dimension=2, batch_size=2)
    vectors = await e.embed_batch(texts)

    assert batch_inputs == [["t1", "t22"], ["t333", "t4444"], ["t55555"]]
    assert vectors == [tuple(_embedding_for(t, 2)) for t in texts]


# ---- retry / failure ----------------------------------------------------------
async def test_retries_on_429_then_succeeds_with_backoff() -> None:
    statuses = iter([429, 200])
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        status = next(statuses)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "rate limited"}})
        return _ok_response(request, dimension=2)

    sleep = AsyncMock()
    e = _make_embedder(handler, dimension=2, sleep=sleep)
    vectors = await e.embed_batch(["hello"])

    assert calls == 2
    assert vectors == [tuple(_embedding_for("hello", 2))]
    sleep.assert_awaited_once()
    # Exponential backoff starts at a positive delay.
    assert sleep.await_args.args[0] > 0


async def test_final_failure_after_three_attempts_raises_provider_error() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": {"message": "upstream down"}})

    sleep = AsyncMock()
    e = _make_embedder(handler, sleep=sleep)
    with pytest.raises(EmbeddingProviderError) as exc_info:
        await e.embed_batch(["x"])

    assert calls == 3
    assert sleep.await_count == 2  # no sleep after the final attempt
    # Exponential: each delay strictly larger than the previous.
    delays = [c.args[0] for c in sleep.await_args_list]
    assert delays[1] > delays[0]
    assert exc_info.value.status_code == 503


async def test_non_retryable_4xx_fails_immediately() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    sleep = AsyncMock()
    e = _make_embedder(handler, sleep=sleep)
    with pytest.raises(EmbeddingProviderError) as exc_info:
        await e.embed_batch(["x"])

    assert calls == 1
    sleep.assert_not_awaited()
    assert exc_info.value.status_code == 401


async def test_malformed_response_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"object": "list", "data": []})

    e = _make_embedder(handler)
    with pytest.raises(EmbeddingProviderError):
        await e.embed_batch(["x"])


# ---- dimension validation ------------------------------------------------------
async def test_request_body_pins_dimensions_param() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _ok_response(request, dimension=7)

    e = _make_embedder(handler, dimension=7)
    await e.embed_batch(["x"])
    assert seen[0]["dimensions"] == 7


async def test_dimension_mismatch_raises_provider_error_before_returning() -> None:
    """The provider returning the wrong vector size must fail loudly —
    silently storing mis-sized vectors would poison the collection."""
    def handler(request: httpx.Request) -> httpx.Response:
        # Embedder configured for 4 dims; provider returns 3.
        return _ok_response(request, dimension=3)

    e = _make_embedder(handler, dimension=4)
    with pytest.raises(EmbeddingProviderError) as exc_info:
        await e.embed_batch(["x"])
    msg = str(exc_info.value)
    assert "3" in msg and "4" in msg


# ---- secret redaction -----------------------------------------------------------
async def test_error_messages_redact_api_key_material() -> None:
    """A provider error body that echoes key material must never leak
    into the raised message (which gets logged)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "Incorrect API key provided: sk-proj-LEAKED_key-42"}},
        )

    e = _make_embedder(handler, api_key="sk-proj-LEAKED_key-42")
    with pytest.raises(EmbeddingProviderError) as exc_info:
        await e.embed_batch(["x"])
    msg = str(exc_info.value)
    assert "sk-proj-LEAKED_key-42" not in msg
    assert "sk-***" in msg


# ---- truncation ---------------------------------------------------------------
async def test_each_input_is_truncated_defensively() -> None:
    seen: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["input"])
        return _ok_response(request, dimension=2)

    huge = "z" * (MAX_INPUT_CHARS + 5_000)
    e = _make_embedder(handler, dimension=2)
    vectors = await e.embed_batch([huge, "small"])

    assert len(seen) == 1
    assert seen[0][0] == "z" * MAX_INPUT_CHARS
    assert seen[0][1] == "small"
    assert len(vectors) == 2
