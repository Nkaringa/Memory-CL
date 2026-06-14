"""Runtime configuration service — Postgres-over-env precedence layer.

The onboarding phase lets operators set the MCP key, OpenAI key, and
embedding mode at runtime (stored in the `app_config` table) WITHOUT a
container restart. `RuntimeConfig` resolves every config value with a
single rule:

    runtime value (app_config) if set  ELSE  env value (Settings)  ELSE  None

NON-BREAKING guarantee: when `app_config` is empty (or its column is
NULL), every accessor falls straight through to the existing
`Settings`/env value, so a deployment that has never touched the new
table behaves EXACTLY as before.

Reads are synchronous against an in-memory snapshot so the auth
dependency (`apps.mcp.auth`) and the native-transport ASGI middleware
(`apps.mcp.native_auth`) — neither of which can cheaply await — get an
O(1) answer. The snapshot is loaded once at startup (`refresh()`) and
re-loaded after every write (the routers call `refresh()` on the
returned config). `invalidate()` marks the snapshot stale so the next
`refresh()` is forced even if a caller tries to skip it.
"""

from __future__ import annotations

import threading

from core.config import Settings
from storage.app_config_repo import AppConfigRepository, AppConfigRow


class RuntimeConfig:
    """Resolves config from `app_config` (runtime) with env fallback.

    Construct with the repo + the process `Settings`. Call `await
    refresh()` once after storage is connected (lifespan does this); the
    sync accessors then read the cached snapshot. After any write through
    the repo, call `await refresh()` again to pick up the new value.
    """

    def __init__(self, repo: AppConfigRepository, settings: Settings) -> None:
        self._repo = repo
        self._settings = settings
        self._row: AppConfigRow | None = None
        self._loaded = False
        # Guards the snapshot swap. Reads are racy-tolerant (a single
        # attribute read), but the loaded-flag + row pair must flip
        # atomically so a concurrent reader never sees a half-update.
        self._lock = threading.Lock()

    # ----- Cache lifecycle -----
    async def refresh(self) -> None:
        """Reload the snapshot from Postgres. Call after every write."""
        row = await self._repo.get()
        with self._lock:
            self._row = row
            self._loaded = True

    def invalidate(self) -> None:
        """Mark the snapshot stale so the next `refresh()` is mandatory.

        Does NOT clear the current snapshot — readers keep the last-known
        value until `refresh()` swaps in the fresh one, so there is never
        a window where a configured key briefly reads as None (which would
        open auth). Routers always pair a write with `await refresh()`.
        """
        with self._lock:
            self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def repo(self) -> AppConfigRepository:
        """The backing write path. Routers write through this, then call
        `await refresh()` so the snapshot picks up the change."""
        return self._repo

    def _snapshot(self) -> AppConfigRow | None:
        with self._lock:
            return self._row

    # ----- Resolved accessors (sync) -----
    def mcp_api_key(self) -> str | None:
        """app_config.mcp_api_key if set, else env Settings.mcp_api_key, else None."""
        row = self._snapshot()
        if row is not None and row.mcp_api_key and row.mcp_api_key.strip():
            return row.mcp_api_key
        env = self._settings.mcp_api_key
        if env is not None and env.get_secret_value().strip():
            return env.get_secret_value()
        return None

    def openai_api_key(self) -> str | None:
        """app_config.openai_api_key if set, else env Settings.openai_api_key, else None."""
        row = self._snapshot()
        if row is not None and row.openai_api_key and row.openai_api_key.strip():
            return row.openai_api_key
        env = self._settings.openai_api_key
        if env is not None and env.get_secret_value().strip():
            return env.get_secret_value()
        return None

    def embedding_mode(self) -> str:
        """'openai' | 'local'. Runtime value if set, else 'openai' default.

        (There is no env equivalent for the mode — it is a Phase-1
        runtime-only setting. The column defaults to 'openai'.)
        """
        row = self._snapshot()
        if row is not None and row.embedding_mode:
            return row.embedding_mode
        return "openai"

    def embedding_model(self) -> str:
        """Runtime override if set, else env Settings.embedding_model."""
        row = self._snapshot()
        if row is not None and row.embedding_model and row.embedding_model.strip():
            return row.embedding_model
        return self._settings.embedding_model

    def embeddings_enabled(self) -> bool:
        """True when embeddings can run.

        Local mode (Phase 2): always enabled — the on-device embedder
        (fastembed) needs no API key. OpenAI mode: enabled iff an OpenAI
        key resolves (runtime or env).
        """
        if self.embedding_mode() == "local":
            return True
        return self.openai_api_key() is not None

    def webhook_secret(self) -> str | None:
        """app_config.webhook_secret if set, else env WEBHOOK_SECRET, else None.

        Used to verify inbound git-push webhook signatures. When None, the
        webhook endpoint rejects every request (it never runs open)."""
        row = self._snapshot()
        if row is not None and row.webhook_secret and row.webhook_secret.strip():
            return row.webhook_secret
        env = self._settings.webhook_secret
        if env is not None and env.get_secret_value().strip():
            return env.get_secret_value()
        return None

    def onboarding_completed(self) -> bool:
        row = self._snapshot()
        return bool(row and row.onboarding_completed)

    def configured(self) -> bool:
        """True once an MCP key is set (runtime or env). Drives the
        bootstrap auth rule: setup endpoints are open until configured."""
        return self.mcp_api_key() is not None

    def mcp_key_hint(self) -> str | None:
        """Masked tail of the resolved MCP key for display, or None.

        Format: '••••' + last 4 chars. Never the full key. Short keys
        (≤ 4 chars) are fully masked to avoid revealing the whole secret.
        """
        key = self.mcp_api_key()
        if not key:
            return None
        if len(key) <= 4:
            return "••••"
        return "••••" + key[-4:]


__all__ = ["RuntimeConfig"]
