"""memcl configuration — file IO + precedence resolution.

Precedence (highest wins):

    flags  >  env (MEMCL_BASE_URL / MEMCL_API_KEY / MEMCL_TIMEOUT)
           >  ~/.memcl/config.toml (path overridable via MEMCL_CONFIG)
           >  defaults

Every resolved value remembers WHERE it came from so `memcl config show`
and `memcl doctor` can explain the effective config instead of making
the user guess.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 30.0

# Ingests legitimately run for minutes; when the user never set a
# timeout anywhere we quietly raise the ceiling for that one command.
INGEST_DEFAULT_TIMEOUT = 3600.0


def config_path() -> Path:
    """Location of the config file (MEMCL_CONFIG overrides for tests/CI)."""
    override = os.environ.get("MEMCL_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".memcl" / "config.toml"


def load_config_file() -> dict[str, object]:
    """Parse the config file; unreadable/invalid files behave as absent."""
    path = config_path()
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return dict(tomllib.load(fh))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def write_config_file(
    *, base_url: str, api_key: str | None, timeout: float,
) -> Path:
    """Write ~/.memcl/config.toml (0600 — it can hold the API key)."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# memcl configuration — created by `memcl config init`.",
        "# Precedence: flags > MEMCL_* env vars > this file > defaults.",
        f'base_url = "{base_url}"',
    ]
    if api_key:
        lines.append(f'api_key = "{api_key}"')
    lines.append(f"timeout = {timeout}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


@dataclass(frozen=True)
class Resolved:
    """A config value plus the layer that supplied it."""

    value: str | float | None
    source: str  # "flag" | "env" | "config" | "default"


@dataclass(frozen=True)
class CliSettings:
    base_url: Resolved
    api_key: Resolved
    timeout: Resolved

    @property
    def base_url_value(self) -> str:
        return str(self.base_url.value)

    @property
    def api_key_value(self) -> str | None:
        return None if self.api_key.value is None else str(self.api_key.value)

    @property
    def timeout_value(self) -> float:
        return float(self.timeout.value)  # type: ignore[arg-type]


def _pick(
    flag: str | float | None,
    env_name: str,
    file_values: dict[str, object],
    file_key: str,
    default: str | float | None,
    *,
    as_float: bool = False,
) -> Resolved:
    if flag is not None:
        return Resolved(flag, "flag")
    env = os.environ.get(env_name)
    if env is not None and env != "":
        return Resolved(float(env) if as_float else env, "env")
    raw = file_values.get(file_key)
    if raw is not None:
        return Resolved(float(raw) if as_float else str(raw), "config")  # type: ignore[arg-type]
    return Resolved(default, "default")


def resolve_settings(
    *,
    base_url_flag: str | None = None,
    api_key_flag: str | None = None,
    timeout_flag: float | None = None,
) -> CliSettings:
    """Apply the full precedence chain once, up front."""
    file_values = load_config_file()
    return CliSettings(
        base_url=_pick(
            base_url_flag, "MEMCL_BASE_URL", file_values, "base_url",
            DEFAULT_BASE_URL,
        ),
        api_key=_pick(
            api_key_flag, "MEMCL_API_KEY", file_values, "api_key", None,
        ),
        timeout=_pick(
            timeout_flag, "MEMCL_TIMEOUT", file_values, "timeout",
            DEFAULT_TIMEOUT, as_float=True,
        ),
    )


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT",
    "INGEST_DEFAULT_TIMEOUT",
    "CliSettings",
    "Resolved",
    "config_path",
    "load_config_file",
    "resolve_settings",
    "write_config_file",
]
