"""Registry that builds an authlib OAuth instance from enabled provider rows.

Each provider is registered under its row ``id`` as the authlib client name.
Calling :meth:`rebuild` replaces the previous OAuth instance atomically, which
is necessary because authlib registrations are additive and cannot be removed
from an existing ``OAuth`` object.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from authlib.integrations.starlette_client import OAuth

from core.auth.providers import build_register_kwargs
from core.logging import get_logger

if TYPE_CHECKING:
    from storage.auth_provider_repo import AuthProviderRow

_log = get_logger(__name__)


class OAuthRegistry:
    """Live registry of authlib OAuth clients for enabled providers."""

    def __init__(self) -> None:
        self._oauth: OAuth = OAuth()
        self._public: list[dict] = []
        self._types: dict[str, str] = {}
        self._ids: set[str] = set()

    def rebuild(self, providers: list[AuthProviderRow]) -> None:
        """Replace the registry with only the currently *enabled* providers.

        Each provider's ``register()`` call is wrapped in try/except so a
        single bad configuration cannot abort the whole rebuild.
        """
        oauth = OAuth()
        public: list[dict] = []
        types: dict[str, str] = {}
        ids: set[str] = set()

        for provider in providers:
            if not provider.enabled:
                continue
            try:
                kwargs = build_register_kwargs(
                    provider_type=provider.provider_type,
                    client_id=provider.client_id,
                    client_secret=provider.client_secret,
                    discovery_url=provider.discovery_url,
                    scopes=provider.scopes,
                )
                oauth.register(name=provider.id, **kwargs)
            except Exception:
                _log.warning(
                    "oauth_registry.skip_provider",
                    provider_id=provider.id,
                    provider_type=provider.provider_type,
                    exc_info=True,
                )
                continue

            public.append({
                "id": provider.id,
                "provider_type": provider.provider_type,
                "display_name": provider.display_name,
            })
            types[provider.id] = provider.provider_type
            ids.add(provider.id)

        # Atomic swap
        self._oauth = oauth
        self._public = public
        self._types = types
        self._ids = ids

    def client_for(self, id: str):
        """Return the authlib remote app for *id*, or ``None`` if not registered."""
        if id not in self._ids:
            return None
        return getattr(self._oauth, id, None)

    def enabled_public_list(self) -> list[dict]:
        """Return a copy of the public provider list (no secrets)."""
        return list(self._public)

    def provider_type_for(self, id: str) -> str | None:
        """Return the provider_type string for *id*, or ``None`` if unknown."""
        return self._types.get(id)


__all__ = ["OAuthRegistry"]
