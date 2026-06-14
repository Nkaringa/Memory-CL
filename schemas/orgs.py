"""Pydantic request/response schemas for the /orgs endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SetRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str


class CreateTeamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    slug: str


class AddTeamMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
