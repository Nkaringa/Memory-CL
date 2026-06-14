"""SQLite async engine + data-dir helpers for lite mode.

Lite mode keeps everything in one portable directory (default `~/.memcl`):
a single SQLite file plus the fastembed model cache. No server, no Docker.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def expand_data_dir(data_dir: str) -> Path:
    """Expand `~`/vars and ensure the lite data directory exists."""
    path = Path(data_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_sqlite_engine(db_path: str | Path) -> AsyncEngine:
    """Async SQLAlchemy engine over a SQLite file via aiosqlite.

    The same engine object is handed to every lite repo (they share the
    single-writer SQLite db) — identical to how the server repos share the
    one Postgres engine.
    """
    return create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)


__all__ = ["expand_data_dir", "make_sqlite_engine"]
