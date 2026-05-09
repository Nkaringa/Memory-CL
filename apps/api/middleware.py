"""HTTP middleware — Phase-10 observability glue.

Exposes a single piece of middleware:

    RequestContextMiddleware

It does three things on every request:

    1. Reads `X-Request-ID` from the inbound headers, or generates a
       UUID4 if the caller didn't provide one. The value is canonical
       lowercase 8-4-4-4-12.
    2. Binds it (and a few other request-shape fields) to the
       structlog contextvars so every log line emitted while serving
       this request carries the same correlation id.
    3. Sets the same id as a current-OTEL-span attribute and echoes it
       back on the response in `X-Request-ID` so SDK / CLI / UI
       clients can correlate their own traces.

The middleware is registered before FastAPI's own routing so it
covers every endpoint, including /health/* and /docs.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from core.logging import bind_request_context, clear_request_context
from core.observability import get_tracer

_TRACER = get_tracer(__name__)
_VALID_ID = re.compile(r"^[0-9a-fA-F-]{8,128}$")
_HEADER = "x-request-id"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Adds a deterministic correlation id to every request.

    Honors a caller-supplied ``X-Request-ID`` when it looks like a
    plausible identifier (alphanumeric + dashes, length-bounded). If
    the value is malformed we generate a fresh one rather than
    propagate untrusted input into our logs and traces.
    """

    def __init__(self, app: ASGIApp, *, header_name: str = _HEADER) -> None:
        super().__init__(app)
        self._header = header_name.lower()

    async def dispatch(  # type: ignore[override]
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(self._header)
        request_id = incoming if incoming and _VALID_ID.match(incoming) else _new_id()

        # Bind to logging contextvars so every structlog event in this
        # request carries the same id without each call site needing
        # to remember to include it.
        bind_request_context(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        span = None
        try:
            with _TRACER.start_as_current_span("http.request") as span:
                span.set_attribute("http.method", request.method)
                span.set_attribute("http.target", request.url.path)
                span.set_attribute("memcl.request_id", request_id)
                response = await call_next(request)
                span.set_attribute("http.status_code", response.status_code)
        finally:
            clear_request_context()

        # Always echo the id so the caller can correlate their side.
        response.headers[self._header] = request_id
        return response


def _new_id() -> str:
    return str(uuid.uuid4())


__all__ = ["RequestContextMiddleware"]
