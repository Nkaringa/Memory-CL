"""MCP auth accepts a named token OR the legacy key (shared rule)."""

from __future__ import annotations

from apps.mcp.token_auth import auth_is_configured, credential_accepted


class _Cache:
    def __init__(self, valid: set[str], count: int | None = None) -> None:
        self._valid = valid
        self._count = count if count is not None else len(valid)

    def is_valid(self, raw_key: str) -> bool:
        return raw_key in self._valid

    def active_count(self) -> int:
        return self._count


def test_dev_mode_when_nothing_configured() -> None:
    assert auth_is_configured(None, None) is False
    assert auth_is_configured(None, _Cache(set(), count=0)) is False


def test_configured_when_legacy_key_or_tokens_exist() -> None:
    assert auth_is_configured("legacy", None) is True
    assert auth_is_configured(None, _Cache({"tok"})) is True


def test_legacy_key_accepted() -> None:
    assert credential_accepted("legacy", "legacy", None) is True
    assert credential_accepted("wrong", "legacy", None) is False


def test_named_token_accepted_alongside_legacy_key() -> None:
    cache = _Cache({"tok-123"})
    # legacy key still works
    assert credential_accepted("legacy", "legacy", cache) is True
    # named token works
    assert credential_accepted("tok-123", "legacy", cache) is True
    # neither
    assert credential_accepted("nope", "legacy", cache) is False


def test_named_token_works_without_legacy_key() -> None:
    cache = _Cache({"tok-123"})
    assert credential_accepted("tok-123", None, cache) is True
    assert credential_accepted("other", None, cache) is False


def test_revoked_token_rejected() -> None:
    # A revoked token is simply absent from the cache's active set.
    cache = _Cache(set(), count=0)
    assert credential_accepted("was-valid", "legacy", cache) is False  # legacy still required
    assert credential_accepted(None, "legacy", cache) is False
