"""Shared MCP credential rules — used by BOTH the REST dependency and the
native ASGI middleware so they can never diverge.

A request is accepted when the presented key equals the legacy single MCP
key (backward-compatible) OR hashes to an active named token (the new
revocable tokens). Auth is "configured" — i.e. enforced — when either a
legacy key is set or at least one active token exists; otherwise it stays
in dev-mode (open), exactly as before.

`token_cache` is the `core.token_cache.TokenCache` (or None when not wired,
e.g. test apps / surfaces mounted outside the lifespan)."""

from __future__ import annotations

from typing import Protocol


class _Cache(Protocol):
    def is_valid(self, raw_key: str) -> bool: ...
    def active_count(self) -> int: ...


def auth_is_configured(expected_key: str | None, token_cache: _Cache | None) -> bool:
    """True when auth should be enforced (a legacy key OR any active token)."""
    if expected_key is not None:
        return True
    return token_cache is not None and token_cache.active_count() > 0


def credential_accepted(
    presented: str | None, expected_key: str | None, token_cache: _Cache | None
) -> bool:
    """True when the presented credential is the legacy key or a live token."""
    if presented is None:
        return False
    if expected_key is not None and presented == expected_key:
        return True
    return token_cache is not None and token_cache.is_valid(presented)


__all__ = ["auth_is_configured", "credential_accepted"]
