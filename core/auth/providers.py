from __future__ import annotations

from typing import Any, Literal

PROVIDER_TYPES = ("github", "google", "microsoft", "oidc")

ProviderType = Literal["github", "google", "microsoft", "oidc"]

IS_OIDC: dict[str, bool] = {
    "google": True,
    "microsoft": True,
    "oidc": True,
    "github": False,
}

PRESETS: dict[str, dict[str, Any]] = {
    "google": {
        "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration",
        "default_scope": "openid email profile",
    },
    "microsoft": {
        "server_metadata_url": "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
        "default_scope": "openid email profile",
    },
    "github": {
        "authorize_url": "https://github.com/login/oauth/authorize",
        "access_token_url": "https://github.com/login/oauth/access_token",
        "api_base_url": "https://api.github.com/",
        "default_scope": "read:user user:email",
    },
    "oidc": {
        "default_scope": "openid email profile",
    },
}


def normalize_provider_type(value: str) -> str:
    pt = value.lower().strip()
    if pt not in PROVIDER_TYPES:
        raise ValueError(f"Unknown provider type {value!r}; expected one of {PROVIDER_TYPES}")
    return pt


def build_register_kwargs(
    *,
    provider_type: str,
    client_id: str,
    client_secret: str,
    discovery_url: str | None,
    scopes: str | None,
) -> dict[str, Any]:
    pt = normalize_provider_type(provider_type)
    scope = scopes or PRESETS[pt]["default_scope"]

    kwargs: dict[str, Any] = {
        "client_id": client_id,
        "client_secret": client_secret,
        "client_kwargs": {
            "scope": scope,
            "code_challenge_method": "S256",
        },
    }

    if pt == "github":
        preset = PRESETS["github"]
        kwargs["authorize_url"] = preset["authorize_url"]
        kwargs["access_token_url"] = preset["access_token_url"]
        kwargs["api_base_url"] = preset["api_base_url"]
    elif pt in ("google", "microsoft"):
        kwargs["server_metadata_url"] = PRESETS[pt]["server_metadata_url"]
    elif pt == "oidc":
        if not discovery_url:
            raise ValueError("generic OIDC requires a discovery_url")
        kwargs["server_metadata_url"] = discovery_url

    return kwargs
