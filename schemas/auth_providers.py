"""Pydantic schemas for the /config/auth/providers admin endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator


class ProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_type: str
    display_name: str
    client_id: str
    client_secret: str
    discovery_url: str | None = None
    scopes: str | None = None

    @model_validator(mode="after")
    def _oidc_requires_discovery_url(self) -> "ProviderCreate":
        if self.provider_type.lower().strip() == "oidc" and not self.discovery_url:
            raise ValueError("OIDC provider requires a discovery_url")
        return self


class ProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str
    client_id: str
    client_secret: str
    discovery_url: str | None = None
    scopes: str | None = None


class ProviderView(BaseModel):
    id: str
    provider_type: str
    display_name: str
    client_id: str
    has_secret: bool
    discovery_url: str | None
    scopes: str | None
    enabled: bool


class ProviderListResponse(BaseModel):
    providers: list[ProviderView]


class EnableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
