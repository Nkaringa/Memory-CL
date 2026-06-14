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


class CreateInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    role: str


class AcceptInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str
    email: str | None = None
    password: str | None = None
    display_name: str | None = None


class CreateGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_type: str
    subject_id: str
    access: str
