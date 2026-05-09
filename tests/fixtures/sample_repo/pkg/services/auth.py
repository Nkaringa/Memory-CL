"""Authentication service — exercised by graph-builder cross-file tests."""

from abc import ABC, abstractmethod

from pkg.utils import add, retry


class TokenStore(ABC):
    """Abstract token store — subclasses implement persistence."""

    @abstractmethod
    def get(self, key: str) -> str | None: ...

    @abstractmethod
    def set(self, key: str, value: str) -> None: ...


class InMemoryTokenStore(TokenStore):
    """Process-local store; lost on restart."""

    DEFAULT_TTL = 3600

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._tokens.get(key)

    def set(self, key: str, value: str) -> None:
        self._tokens[key] = value


def login(user: str, password: str, store: TokenStore) -> str:
    """Validate and store a session token."""
    token = retry(lambda: f"tok-{add(len(user), len(password))}")
    store.set(user, token)
    return token


def refresh(user: str, store: TokenStore) -> str | None:
    return store.get(user)
